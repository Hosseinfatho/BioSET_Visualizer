"""Iso-contours of the co-localization field, as real geometry.

The integrated heatmap used to draw its outline in the fragment shader: a
threshold on a maximum-intensity projection along each ray, turned into a
screen-space distance by dividing by `dFdx`/`dFdy`. That produced a
characteristic set of artifacts, all of which this module exists to avoid:

  * bilinear interpolation is only C0, so the gradient jumped at every cell
    seam — visible kinks and width changes on a 16x16 lattice;
  * `max()` along the ray is not differentiable, so wherever the arg-max
    sample changed between neighbouring pixels the derivative spiked and the
    line broke into dashes;
  * on plateaus the gradient vanished, and the outline with it;
  * being a projection along the view ray, the contour changed shape when the
    camera orbited — it traced the silhouette of the above-threshold column,
    not the field.

Contouring the field on the CPU removes all of those by construction: the
curves are view-independent geometry, depth-composited like anything else.

The input is the same `HeatmapField` the grid heatmap consumes, so resolution
follows the camera through the existing LOD machinery for free.

WHAT THE LINE MEANS
-------------------
One iso-line, and it always answers the same question: *within what I am
looking at right now, where should I look next?* Two properties make that
true, and both are easy to break:

  * the iso-value is a percentile of the values inside the VIEWPORT, not the
    whole slide — so the line is always on screen (see `ContourConfig`);
  * the percentile RISES as the camera moves in, so the overlay is a
    drill-down: an outer envelope when zoomed out, the critical core when
    zoomed in.

Work is split accordingly. `update_field` smooths and caches (once per LOD);
`update_for_viewport` re-cuts the curve from that cache and is cheap enough to
run on every pan.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field as _field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkImageData, vtkPolyData
from vtkmodules.vtkFiltersCore import (
    vtkCleanPolyData,
    vtkMarchingSquares,
    vtkStripper,
)
from vtkmodules.vtkFiltersGeneral import vtkSplineFilter
from vtkmodules.vtkImagingGeneral import vtkImageGaussianSmooth
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkRenderer

from .heatmap import select_view_planes


@dataclass
class ContourConfig:
    """Tuning for the iso-contour overlay."""

    # ONE iso-value, taken as a percentile of the values inside the VIEWPORT,
    # indexed by hierarchy level (0 = Fine/near ... 3 = Overview/far).
    #
    # Both halves of that sentence are load-bearing.
    #
    # Scoping to the viewport is what guarantees the line is on screen at all.
    # Levels taken from the whole field are absolute values, so a viewport
    # sitting inside a uniformly hot (or uniformly cold) region contains no
    # iso-line anywhere. Measured on mis_v3 at LOD 0 over 8 deep-zoom
    # viewports, counting contour vertices landing inside the viewport rect:
    #
    #     levels from whole field    Hoechst 4/8 empty,  MART1 2/8 empty
    #     levels from viewport       Hoechst 0/8,        MART1 0/8
    #
    # A percentile of the visible values is by construction attained inside
    # the visible region, so there is always something to look at.
    #
    # Ramping the percentile with zoom is what makes it a drill-down: far out
    # the line is the broad envelope of the aggregated field, and each step in
    # narrows it to the critical part of what is currently framed.
    #
    # The ramp is driven by the VIEWPORT WIDTH IN MICRONS, not the hierarchy
    # level. The level saturates at 0 (16-voxel cells, 2.24 um) while zoom
    # keeps going for another ~40x on this slide (1528 um across), so a
    # level-indexed table stopped ramping exactly where the overlap outline is
    # wanted — every further zoom showed the same p88 of a shrinking window.
    #
    # Anchors are (width_um, percentile), log-interpolated between and clamped
    # outside. p70 at full slide reads as "which way is worth going"; p96 at
    # 40 um is as close to outlining the actual marker overlap as this field
    # supports (see the note on absolute thresholds in `min_cells_above`).
    critical_ramp_wide: Tuple[float, float] = (1200.0, 70.0)
    critical_ramp_tight: Tuple[float, float] = (40.0, 96.0)

    # Keep at least this many cells above the level. Below ~25 um across there
    # are only ~11 cells to a side at LOD 0, and the ramp's target percentile
    # would leave one or two — not enough to form a curve. Capping degrades the
    # line into a broader outline instead of losing it.
    #
    # NB an ABSOLUTE overlap threshold is not an option here: the per-cell
    # co-localization fraction tops out around 0.69-0.79 and no cell in any
    # combination reaches 0.8, because this field is cell-level co-occurrence
    # rather than a voxel-exact intersection (which the preprocessing does not
    # emit). A high percentile of the visible field is the honest approximation.
    min_cells_above: int = 8

    # Fraction of the viewport extent added per side before contouring, so the
    # curve does not visibly terminate at the window edge. The PERCENTILE is
    # still taken from the un-margined rect — that is the on-screen guarantee
    # above; this only extends the geometry past the frame.
    viewport_margin_frac: float = 0.25

    # Cells below this fraction of the field maximum are not signal and are
    # excluded from the percentile.
    #
    # `> 0` is the wrong test once the field has been blurred: the Gaussian
    # gives every cell a positive value, so an OFF-TISSUE viewport is 100%
    # "positive" at ~1e-8 of the maximum, and a percentile of that halo drew a
    # contour through empty space. Measured on a Hoechst viewport clear of the
    # tissue: max 6.4e-4 against a field max of 0.444, and every cell below 1%
    # of it. On-tissue viewports sit entirely ABOVE the floor, so this only
    # ever suppresses viewports that genuinely hold nothing — which correctly
    # draw no line at all.
    noise_floor_frac: float = 0.01

    # How far the viewport must move, as a fraction of its own extent, before
    # the line is re-cut. The iso-value is a percentile of a moving window, so
    # it drifts continuously; without a gate the curve breathes while panning.
    viewport_update_frac: float = 0.15

    # Gaussian blur before contouring, in MICRONS. This is not cosmetic: the
    # raw field is speckled at cell granularity, and contouring it unsmoothed
    # yields hundreds of short fragments — the same dashed look the shader
    # produced.
    #
    # The unit matters more than the value. Expressed in CELLS, the physical
    # smoothing scale shrank with the LOD (a cell is 35.8 um at LOD 2 but
    # 2.24 um at LOD 0), so zooming in stopped resolving the same structure
    # and started tracing subcellular speckle. Polylines per level, Hoechst /
    # MART1 at LOD 2 -> 1 -> 0:
    #
    #     sigma = 2.5 cells      9 ->  37 -> 496     9 ->  22 -> 303
    #     sigma = 22 um         42 ->  37 ->  34    28 ->  20 ->  36
    #
    # A fixed micron scale keeps the count — and the features — stable across
    # zoom, which is what "resolves as you zoom" should mean: the same curves,
    # drawn more precisely. Binning already smooths at the cell size, so the
    # effective scale is ~sqrt(cell^2 + sigma^2) and the coarse levels are not
    # under-smoothed despite sigma being under one cell there.
    #
    # But a CONSTANT 22 um is wrong once the viewport is only a few multiples
    # of it: at 50 um across it spans half the view and flattens it, leaving
    # nothing for the ramp above to bite on. Relative spread (p95-p5)/p50
    # inside the viewport, Hoechst+lamin-ABC at LOD 0:
    #
    #     viewport    sigma 22    sigma 10    sigma 5    sigma 2
    #       400 um       1.382       1.794      2.036      2.259
    #       100 um       0.438       0.702      0.912      1.175
    #        50 um       0.139       0.519      0.954      1.354
    #
    # So sigma tracks the viewport, clamped: the established 22 um holds for
    # any view wider than ~264 um, and shrinks below that.
    smoothing_sigma_max_um: float = 22.0
    smoothing_sigma_min_um: float = 2.0
    viewport_sigma_divisor: float = 12.0

    # Re-smoothing the full field costs ~10 ms, so only redo it when the
    # viewport scale has really changed — otherwise every wheel notch would
    # pay. Pans at constant zoom reuse the cached blur entirely.
    sigma_change_frac: float = 0.25

    # Spline resampling step, as a fraction of a cell. Smaller = smoother
    # curves and more points.
    spline_step_cells: float = 0.5

    # Contours are the only mark on screen at high zoom, so they carry the
    # reading; 2.5 px was too faint to follow, and 3.5 still was.
    line_width: float = 6.0
    # Drop closed loops with a perimeter shorter than this, in MICRONS — same
    # reasoning as the sigma above: speckle is made of tiny loops and real
    # structure is not, but "tiny" is a physical size, not a cell count.
    #
    # And, like the sigma, it cannot stay FIXED once the viewport approaches
    # it. At a 50 um view the real loops measured 18 and 38 um and a flat
    # 50 um threshold discarded both, so the overlay went blank exactly at the
    # zoom it was being extended to serve. The effective threshold is the
    # smaller of this and a fraction of the view, so it keeps pruning speckle
    # proportionally instead of erasing everything.
    min_polyline_um: float = 50.0
    min_polyline_frac_of_view: float = 0.125
    # One line, so there is no nesting to rank by opacity.
    opacity: float = 1.0
    color: Tuple[float, float, float] = (1.0, 1.0, 1.0)

    # Cap on cells contoured in one pass — a safety valve, not a resolution
    # limit. The uncropped LOD-0 field is 345x682 = 235k cells and costs
    # ~320 ms through the whole pipeline, so it must fit: dropping the finest
    # level is exactly the resolution the contours exist to provide. The
    # viewport crop normally keeps the real figure far below this.
    max_cells: int = 300_000

    # Shared with the grid squares: never let the contour plane approach the
    # lens closer than this fraction of the camera-to-focal distance.
    min_near_distance_frac: float = 0.06


def densify(field) -> np.ndarray:
    """Sparse `HeatmapField` -> dense (ny, nx) float32.

    The field carries only non-zero cells, so the zeros this leaves behind are
    genuine absence of signal rather than missing data.
    """
    dense = np.zeros((field.ny, field.nx), dtype=np.float32)
    if field.counts.size:
        dense[field.cells_yx[:, 0], field.cells_yx[:, 1]] = field.fractions
    return dense


def field_to_image(dense: np.ndarray, cell_size_vox: int,
                   sx: float, sy: float,
                   origin_cells: Tuple[int, int] = (0, 0)) -> vtkImageData:
    """Wrap a dense cell grid as vtkImageData in WORLD units.

    Spacing is the cell size in world units, so the contour comes out already
    positioned over the volume — no UV mapping, which is what the shader
    version needed and what tied it to the volume's bounding box.
    `origin_cells` shifts the grid when the field has been viewport-cropped.
    """
    ny, nx = dense.shape
    img = vtkImageData()
    img.SetDimensions(nx, ny, 1)
    img.SetSpacing(cell_size_vox * sx, cell_size_vox * sy, 1.0)
    # Samples sit at cell CENTRES.
    img.SetOrigin((origin_cells[1] + 0.5) * cell_size_vox * sx,
                  (origin_cells[0] + 0.5) * cell_size_vox * sy,
                  0.0)
    arr = numpy_to_vtk(np.ascontiguousarray(dense.ravel()), deep=True)
    arr.SetName("value")
    img.GetPointData().SetScalars(arr)
    return img


class ContourRenderer:
    """Iso-contour actors for one co-localization field.

    One persistent actor per level, following the same pattern as
    `HeatmapRenderer`: actors and mappers are created once and re-pointed at
    new polydata, so an update never rebuilds GPU pipeline objects.
    """

    def __init__(self, outline_renderer: Optional[vtkRenderer] = None, *,
                 config: Optional[ContourConfig] = None):
        self.renderer = outline_renderer
        self.config = config or ContourConfig()
        self._levels: List[dict] = []      # per level: {actor, mapper}
        self._visible = True
        self._active = False
        self._camera = None
        self._volume_z = (0.0, 0.0)
        self._n_points = 0
        self._n_lines = 0
        # Cached field for the current LOD, so a pan can re-cut the contour
        # without going back to the loader. Raw is kept alongside the blur
        # because sigma depends on the viewport and has to be redone on zoom.
        self._raw: Optional[np.ndarray] = None
        self._smoothed: Optional[np.ndarray] = None
        self._sigma_um = -1.0
        self._cell_size_vox = 1
        self._max_value = 0.0
        self._spacing = (1.0, 1.0)
        self._level = 0
        self._last_value = 0.0
        self._last_pct = 0.0
        self._last_roi = None
        if outline_renderer is not None:
            self._camera = outline_renderer.GetActiveCamera()

    # ── configuration ──────────────────────────────────────

    def set_camera(self, camera):
        self._camera = camera

    def set_volume_z(self, z_lo: float, z_hi: float):
        """World-Z of the two volume faces the contours are drawn against."""
        self._volume_z = (float(z_lo), float(z_hi))

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def line_count(self) -> int:
        return self._n_lines

    # ── build ──────────────────────────────────────────────

    def update_field(self, field, spacing: Tuple[float, float, float],
                     roi_vox=None) -> bool:
        """Take a new heatmap field: densify, smooth, cache, then contour.

        Only the smoothing is done here, and only once per field — the actual
        iso-value and geometry depend on the viewport and are re-derived by
        `update_for_viewport`, which is cheap enough to run on every pan.
        Splitting them is what makes panning responsive: the LOD worker only
        delivers a field when the LEVEL changes, so before the split nothing
        updated at all while panning within a level.

        Smoothing deliberately covers the FULL field rather than the viewport
        slice: a per-slice blur would handle its edges differently as the
        window moved, and the contour would visibly shift under panning.
        """
        if field is None or self.renderer is None or field.counts.size == 0:
            self.clear()
            return False

        sx, sy, _ = spacing
        self._raw = densify(field)
        self._cell_size_vox = field.cell_size_vox
        self._spacing = (sx, sy)
        self._level = int(getattr(field, "level", 0) or 0)
        self._smoothed = None
        self._sigma_um = -1.0
        return self.update_for_viewport(roi_vox)

    def _ensure_smoothed(self, width_um: float) -> bool:
        """Blur the cached raw field at the sigma this viewport implies.

        Reuses the existing blur unless the required sigma has moved by more
        than `sigma_change_frac`, so panning at constant zoom never pays for a
        re-smooth and only a real zoom change does.
        """
        if self._raw is None:
            return False
        cfg = self.config
        sigma = float(np.clip(width_um / max(1e-6, cfg.viewport_sigma_divisor),
                              cfg.smoothing_sigma_min_um, cfg.smoothing_sigma_max_um))
        if (self._smoothed is not None and self._sigma_um > 0
                and abs(sigma - self._sigma_um)
                <= cfg.sigma_change_frac * self._sigma_um):
            return True

        sx, _ = self._spacing
        cell_world = self._cell_size_vox * sx
        img = field_to_image(self._raw, self._cell_size_vox, sx, self._spacing[1])
        out = self._smooth(img, cell_world, sigma)
        self._smoothed = vtk_to_numpy(
            out.GetPointData().GetScalars()).reshape(self._raw.shape)
        self._max_value = float(self._smoothed.max()) if self._smoothed.size else 0.0
        self._sigma_um = sigma
        return True

    def update_for_viewport(self, roi_vox=None) -> bool:
        """Re-cut the contour for the current viewport. Returns True if drawn.

        The whole per-pan path: slice the cached smoothed array, take the
        percentile of the un-margined viewport, contour, swap the polydata.
        Touches neither the loader nor the smoother, which is why it measures
        1-3 ms for realistic viewports against ~31 ms for a full rebuild.
        """
        if self._raw is None or self.renderer is None:
            return False
        cfg = self.config
        sx, sy = self._spacing
        cell_world = self._cell_size_vox * sx

        width_um = self._viewport_width_um(roi_vox)
        if not self._ensure_smoothed(width_um):
            return False

        stat_box, draw_box = self._viewport_boxes(roi_vox)
        sy0, sy1, sx0, sx1 = stat_box
        stat = self._smoothed[sy0:sy1, sx0:sx1]
        # Significance floor, not `> 0` — see `noise_floor_frac`. A viewport
        # with nothing above it holds no signal, so it correctly draws no line
        # rather than contouring the blur halo.
        floor = self._max_value * self.config.noise_floor_frac
        stat = stat[stat > floor]
        if stat.size == 0:
            self._drop_actor()
            return False

        pct = self._percentile(width_um, stat.size)
        value = float(np.percentile(stat, pct))
        if value <= floor:
            self._drop_actor()
            return False

        dy0, dy1, dx0, dx1 = draw_box
        sub = np.ascontiguousarray(self._smoothed[dy0:dy1, dx0:dx1])
        if sub.size > cfg.max_cells:
            print(f"[contours] {sub.shape[0]}x{sub.shape[1]} over the "
                  f"{cfg.max_cells:,}-cell budget")
        # Contour the SLICE, with its origin, rather than a full-size grid
        # padded with zeros outside the crop. Marching squares leaves curves
        # open at the image border but closes them against a zero field, so
        # the padded form traced the crop rectangle as if it were structure.
        img = field_to_image(sub, self._cell_size_vox, sx, sy, (dy0, dx0))
        min_len = min(cfg.min_polyline_um,
                      width_um * cfg.min_polyline_frac_of_view)
        polys = self._contour(img, [value], cell_world, min_len)
        poly = polys[0] if polys else None
        if poly is None or poly.GetNumberOfPoints() == 0:
            self._drop_actor()
            return False

        entry = self._ensure_level(0)
        entry["mapper"].SetInputData(poly)
        prop = entry["actor"].GetProperty()
        prop.SetOpacity(cfg.opacity)
        prop.SetColor(*cfg.color)
        prop.SetLineWidth(cfg.line_width)
        if not self.renderer.HasViewProp(entry["actor"]):
            self.renderer.AddActor(entry["actor"])
        entry["actor"].SetVisibility(1 if self._visible else 0)

        self._n_points = poly.GetNumberOfPoints()
        self._n_lines = poly.GetNumberOfLines()
        self._last_value = value
        self._last_pct = pct
        self._last_roi = roi_vox
        self._active = self._n_lines > 0
        self.update_for_camera()
        return self._active

    def _viewport_width_um(self, roi_vox) -> float:
        """Physical width of the view. The ramp's only input."""
        sx, _ = self._spacing
        if roi_vox is None:
            if self._raw is None:
                return self.config.critical_ramp_wide[0]
            return self._raw.shape[1] * self._cell_size_vox * sx
        return max(1e-6, (roi_vox[1] - roi_vox[0]) * sx)

    def _percentile(self, width_um: float, n_cells: int) -> float:
        """Criticality for this viewport: higher (more selective) the tighter
        the view, log-interpolated between the two anchors.

        Capped so at least `min_cells_above` cells survive — at extreme zoom
        the target would leave one or two, which cannot form a curve.
        """
        cfg = self.config
        (w_wide, p_wide), (w_tight, p_tight) = (cfg.critical_ramp_wide,
                                                cfg.critical_ramp_tight)
        if width_um >= w_wide:
            pct = p_wide
        elif width_um <= w_tight:
            pct = p_tight
        else:
            t = (np.log10(w_wide / width_um) / np.log10(w_wide / w_tight))
            pct = p_wide + t * (p_tight - p_wide)
        if n_cells > cfg.min_cells_above:
            pct = min(pct, 100.0 * (1.0 - cfg.min_cells_above / n_cells))
        return float(np.clip(pct, 1.0, 99.9))

    def _viewport_boxes(self, roi_vox):
        """(statistics box, drawing box) in cell indices.

        The statistics box is the true viewport — taking the percentile there
        is what guarantees the line is on screen. The drawing box adds a
        margin so the curve does not stop dead at the window edge.
        """
        ny, nx = self._smoothed.shape
        if roi_vox is None:
            return (0, ny, 0, nx), (0, ny, 0, nx)
        x0, x1, y0, y1 = roi_vox
        cs = self._cell_size_vox

        def rect(mx, my):
            return (
                max(0, int(np.floor((y0 - my) / cs))),
                min(ny, int(np.ceil((y1 + my) / cs))),
                max(0, int(np.floor((x0 - mx) / cs))),
                min(nx, int(np.ceil((x1 + mx) / cs))),
            )

        def grow(box, want):
            """Widen a box to `want` cells per axis, around its own centre and
            clamped to the grid. Marching squares needs genuinely 2-D input:
            a box one cell thick made it log "requires 2D data" and draw
            nothing at all, which is how a coarse level paired with a tight
            viewport came up empty."""
            b0, b1, b2, b3 = box
            if b1 - b0 < want:
                c = (b0 + b1) * 0.5
                b0 = max(0, int(c - want / 2)); b1 = min(ny, b0 + want)
                b0 = max(0, b1 - want)
            if b3 - b2 < want:
                c = (b2 + b3) * 0.5
                b2 = max(0, int(c - want / 2)); b3 = min(nx, b2 + want)
                b2 = max(0, b3 - want)
            return (b0, b1, b2, b3)

        stat = grow(rect(0.0, 0.0), min(4, ny, nx))
        f = self.config.viewport_margin_frac
        draw = grow(rect((x1 - x0) * f, (y1 - y0) * f), min(4, ny, nx))
        return stat, draw

    def _drop_actor(self):
        """Nothing to draw for this viewport — remove the line but KEEP the
        cached field, so the next pan can re-cut without a reload."""
        if self.renderer is not None:
            for entry in self._levels:
                self.renderer.RemoveActor(entry["actor"])
        self._n_points = self._n_lines = 0
        self._last_value = 0.0
        self._active = False

    def _smooth(self, img: vtkImageData, cell_world: float,
                sigma_um: float) -> vtkImageData:
        """Blur the field before contouring.

        Load-bearing, not cosmetic: contouring the raw field reproduces exactly
        the fragmented look the shader version had, because the field is
        speckled at cell granularity. `cell_world` converts the configured
        micron scale into the pixel units vtkImageGaussianSmooth wants.
        """
        sigma = float(sigma_um) / max(1e-6, cell_world)
        if sigma <= 0.05:
            return img
        blur = vtkImageGaussianSmooth()
        blur.SetInputData(img)
        blur.SetDimensionality(2)
        blur.SetStandardDeviations(sigma, sigma, 0.0)
        blur.SetRadiusFactors(sigma * 3.0, sigma * 3.0, 0.0)
        blur.Update()
        return blur.GetOutput()

    def _contour(self, img: vtkImageData, levels: Sequence[float],
                 cell_world: float, min_length: float) -> Optional[List[vtkPolyData]]:
        """Contour, join into polylines, drop specks, then subdivide to curves."""
        cfg = self.config
        out: List[vtkPolyData] = []
        for value in levels:
            squares = vtkMarchingSquares()
            squares.SetInputData(img)
            squares.SetNumberOfContours(1)
            squares.SetValue(0, float(value))
            squares.Update()
            if squares.GetOutput().GetNumberOfLines() == 0:
                out.append(vtkPolyData())
                continue

            # Marching squares emits loose 2-point segments. Merge coincident
            # endpoints, then stitch them into long polylines — without this
            # the spline filter has nothing to subdivide along.
            clean = vtkCleanPolyData()
            clean.SetInputConnection(squares.GetOutputPort())
            strip = vtkStripper()
            strip.SetInputConnection(clean.GetOutputPort())
            strip.SetMaximumLength(100000)
            strip.JoinContiguousSegmentsOn()
            strip.Update()

            pruned = self._prune_short(strip.GetOutput(), min_length)
            if pruned.GetNumberOfLines() == 0:
                out.append(vtkPolyData())
                continue

            spline = vtkSplineFilter()
            spline.SetInputData(pruned)
            spline.SetSubdivideToLength()
            spline.SetLength(max(1e-6, cell_world * cfg.spline_step_cells))
            spline.Update()

            poly = vtkPolyData()
            poly.DeepCopy(spline.GetOutput())
            out.append(poly)
        return out

    @staticmethod
    def _prune_short(poly: vtkPolyData, min_length: float) -> vtkPolyData:
        """Drop polylines shorter than `min_length` in world units.

        Speckle is made of tiny closed loops; real structure is not. Pruning
        before splining also keeps the spline filter off work that is about to
        be discarded.
        """
        if min_length <= 0 or poly.GetNumberOfLines() == 0:
            return poly
        pts = vtk_to_numpy(poly.GetPoints().GetData())
        lines = poly.GetLines()
        keep, i = [], 0
        conn = vtk_to_numpy(lines.GetConnectivityArray())
        offs = vtk_to_numpy(lines.GetOffsetsArray())
        for c in range(len(offs) - 1):
            seq = conn[offs[c]:offs[c + 1]]
            if seq.size < 2:
                continue
            d = np.diff(pts[seq][:, :2], axis=0)
            if float(np.hypot(d[:, 0], d[:, 1]).sum()) >= min_length:
                keep.append(seq)
        if not keep:
            return vtkPolyData()

        out = vtkPolyData()
        out.SetPoints(poly.GetPoints())
        cells = vtkCellArray()
        for seq in keep:
            cells.InsertNextCell(len(seq))
            for pid in seq:
                cells.InsertCellPoint(int(pid))
        out.SetLines(cells)
        return out

    # ── placement / visibility ─────────────────────────────

    def update_for_camera(self, camera=None) -> bool:
        """Sit the contours on whichever volume face the camera is looking at.

        Same problem and same solution as the grid squares — see
        `heatmap.select_view_planes`.
        """
        if camera is not None:
            self._camera = camera
        cam = self._camera
        if cam is None or not self._levels:
            return False
        z_lo, z_hi = self._volume_z
        if abs(z_hi - z_lo) < 1e-9:
            return False
        planes = select_view_planes(cam, z_lo, z_hi,
                                    self.config.min_near_distance_frac)
        if planes is None:
            return False
        near_z = planes[0]
        changed = False
        for entry in self._levels:
            actor = entry["actor"]
            if abs(actor.GetPosition()[2] - near_z) > 1e-9:
                actor.SetPosition(0.0, 0.0, near_z)
                changed = True
        return changed

    def set_visible(self, visible: bool):
        if visible == self._visible:
            return
        self._visible = bool(visible)
        for entry in self._levels:
            entry["actor"].SetVisibility(1 if self._visible else 0)

    def clear(self):
        """Full teardown — actors and the cached field both go."""
        if self.renderer is not None:
            for entry in self._levels:
                self.renderer.RemoveActor(entry["actor"])
        self._levels = []
        self._active = False
        self._n_points = self._n_lines = 0
        self._raw = None
        self._smoothed = None
        self._sigma_um = -1.0
        self._last_roi = None

    @property
    def iso_value(self) -> float:
        """The iso-value currently drawn (diagnostics and tests)."""
        return self._last_value

    @property
    def iso_percentile(self) -> float:
        """The percentile the ramp settled on, after the cell-count cap."""
        return self._last_pct

    @property
    def sigma_um(self) -> float:
        """Smoothing scale the cached blur was built at."""
        return self._sigma_um

    @property
    def has_field(self) -> bool:
        """True when a field is cached and a pan can re-cut it."""
        return self._raw is not None

    def needs_viewport_update(self, roi_vox) -> bool:
        """Has the view moved enough to be worth re-cutting the line?

        The iso-value is a percentile of a moving window, so it drifts
        continuously — re-cutting on every camera event would make the curve
        visibly breathe. Require the viewport to have moved or resized by a
        real fraction of its own extent first.
        """
        if self._smoothed is None or roi_vox is None:
            return False
        if self._last_roi is None:
            return True
        x0, x1, y0, y1 = roi_vox
        px0, px1, py0, py1 = self._last_roi
        w, h = max(1.0, px1 - px0), max(1.0, py1 - py0)
        t = self.config.viewport_update_frac
        return (abs(x0 - px0) > w * t or abs(x1 - px1) > w * t
                or abs(y0 - py0) > h * t or abs(y1 - py1) > h * t)

    # ── actors ─────────────────────────────────────────────

    def _ensure_level(self, index: int) -> dict:
        while len(self._levels) <= index:
            mapper = vtkPolyDataMapper()
            mapper.ScalarVisibilityOff()
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.SetPickable(False)
            prop = actor.GetProperty()
            prop.SetLighting(False)
            self._levels.append({"actor": actor, "mapper": mapper})
        return self._levels[index]
