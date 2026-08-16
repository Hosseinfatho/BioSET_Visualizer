from __future__ import annotations

import math
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

import numpy as np
import vtk
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkGlyph3DMapper,
    vtkPolyDataMapper,
    vtkRenderer,
)
from vtkmodules.util.numpy_support import numpy_to_vtk

from ..analysis import HeatmapField, TileData


@dataclass
class HeatmapConfig:
    base_color: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    # Cell value is encoded as grayscale brightness plus opacity, at a fixed
    # line width.
    outline_min_opacity: float = 0.05
    outline_max_opacity: float = 0.95
    z_height: float = 1.0
    z_offset: float = 0.0
    percentile_cutoff: float = 0.01  # Only show cells above this active_fraction percentile
    outline_line_width: float = 5.0  # Fixed line width for all cells

    # World Z of the two volume faces the grid brackets. Equal values mean the
    # volume depth is unknown, and a single square is drawn on the cell plane.
    #
    # These are the faces themselves, NOT an offset in front of them. Putting
    # the near square outside the volume made it invisible when zoomed in: all
    # three layered renderers share one vtkCamera, ClippingRange is camera
    # state, and only the volume renderer ever calls ResetCameraClippingRange —
    # so the near plane is always derived from the volume bounds and anything
    # in front of them gets clipped. Depth order is handled by the render
    # layers, not by pushing geometry toward the camera.
    volume_z_lo: float = 0.0
    volume_z_hi: float = 0.0

    # The near square is never allowed closer to the camera than this fraction
    # of the camera-to-focal distance. At deep zoom the camera ends up inside
    # the volume's Z slab, and without this the square lands on the lens.
    min_near_distance_frac: float = 0.06
    # Draw the far square only while the two project at similar sizes. Past
    # this ratio they no longer overlap and read as two unrelated grids.
    far_square_max_ratio: float = 1.15


def _camera_focal_distance(camera) -> float:
    """Eye-to-focal-point distance.

    Same quantity as `streaming.lod.camera_distance_to_focal`; duplicated here
    rather than imported because scene/ importing streaming/ closes an existing
    import cycle (see scene/builder.py).
    """
    px, py, pz = camera.GetPosition()
    fx, fy, fz = camera.GetFocalPoint()
    return math.sqrt((px - fx) ** 2 + (py - fy) ** 2 + (pz - fz) ** 2)


_LUT_SIZE = 256

# Glyph roles: which persistent actor draws what, and on which layer.
#   front — square outline in front of the volume, layer-2 renderer
#   back  — square outline behind the volume, layer-0 renderer
#
# Two flat squares, never a box. Corner connectors joining the two planes used
# to be drawn as well, which made each cell read as a wireframe cube; zoomed in,
# those connectors project as long diverging lines across the volume depth and
# swamp the grid.
_ROLES = ("front", "back")


class HeatmapRenderer:
    """Heatmap cells rendered as instanced glyphs (one persistent
    vtkGlyph3DMapper actor per role instead of one vtkActor per cell — the
    fine grid can carry hundreds of thousands of cells).

    Draws the grid: one square outline on the front face of the volume and a
    matching one on the back, with cell value encoded as grayscale brightness
    plus opacity at a fixed line width. The solid-cube "filled" mode this used
    to also offer has been removed; the other heatmap mode is the shader-based
    integrated one, which does not go through here.

    Actors and mappers are created once and reused across updates (input/source
    swaps only) so an update never re-creates GPU pipeline objects.
    """

    def __init__(
        self,
        renderer: vtkRenderer,
        *,
        outline_renderer: Optional[vtkRenderer] = None,
        config: Optional[HeatmapConfig] = None,
    ):
        # renderer: layer 0, behind the volume (back rects)
        # outline_renderer: layer 2, in front (front squares)
        self.renderer = renderer
        self.outline_renderer = outline_renderer
        self.config = config or HeatmapConfig()

        self._visible = True
        self._glyphs: Dict[str, dict] = {}     # role -> {actor, mapper, renderer}
        self._active_roles: set[str] = set()
        self._lut_cache: Dict[tuple, vtk.vtkLookupTable] = {}

        self._field: Optional[HeatmapField] = None
        self._current_spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0)
        # Camera the squares are placed against; shared by all three layers.
        self._camera = getattr(outline_renderer or renderer, "GetActiveCamera", lambda: None)()


    # ──────────────────────────────────────────────
    # Update
    # ──────────────────────────────────────────────

    def update_field(
        self,
        field: Optional[HeatmapField],
        spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0),
        color: Optional[Tuple[float, float, float]] = None,
    ):
        """Draw the grid for `field`.

        `color` is accepted and stored but does not affect the ramp: cells are
        drawn grayscale-by-value so brightness reads as magnitude. It was only
        ever used by the removed filled mode.
        """
        self.clear()
        if field is None or field.counts.size == 0:
            return
        if color is not None:
            self.config.base_color = color

        fractions = field.fractions.astype(np.float64)

        # Percentile floor (matches old behavior: drop bottom percentile + zeros)
        order = np.sort(fractions)
        cutoff_value = order[max(0, int(order.size * self.config.percentile_cutoff))]
        keep = (fractions >= cutoff_value) & (fractions > 0)
        if not np.any(keep):
            return

        cells = field.cells_yx[keep]
        counts = field.counts[keep]
        fracs = fractions[keep]

        # Quantile normalization: rank-based mapping spreads even small
        # differences across the full 0→1 range (ties get their average rank).
        n = fracs.size
        uniq, inverse, cnt = np.unique(fracs, return_inverse=True, return_counts=True)
        ends = np.cumsum(cnt)
        starts = ends - cnt
        avg_rank = (starts + ends - 1) / 2.0
        normalized = (avg_rank[inverse] / (n - 1)) if n > 1 else np.ones(n)

        self._field = HeatmapField(
            level=field.level,
            cell_size_vox=field.cell_size_vox,
            ny=field.ny,
            nx=field.nx,
            cells_yx=cells,
            counts=counts,
            fractions=fracs.astype(np.float32),
        )
        self._current_spacing = spacing

        sx, sy, _ = spacing
        cw_x = field.cell_size_vox * sx  # cell width in world units
        cw_y = field.cell_size_vox * sy
        z_center = self.config.z_height / 2.0 + self.config.z_offset

        # Instance points at cell centers, scalar = normalized rank value
        pts = np.empty((n, 3), dtype=np.float64)
        pts[:, 0] = (cells[:, 1] + 0.5) * cw_x
        pts[:, 1] = (cells[:, 0] + 0.5) * cw_y
        pts[:, 2] = z_center
        instances = vtkPolyData()
        vpts = vtkPoints()
        vpts.SetData(numpy_to_vtk(pts, deep=True))
        instances.SetPoints(vpts)
        scalars = numpy_to_vtk(normalized.astype(np.float32), deep=True)
        scalars.SetName("value")
        instances.GetPointData().SetScalars(scalars)

        if self.outline_renderer is None:
            return
        lut = self._make_lut()
        # Both squares are baked flat at the instance plane; where they actually
        # sit along Z is an actor position set by `update_for_camera`, so the
        # placement can follow the camera without rebuilding any geometry.
        square = self._rect_source(cw_x, cw_y, 0.0)
        self._activate("front", instances, square, lut)
        if self._has_depth:
            self._activate("back", instances, square, lut)
        self.update_for_camera(self._camera)

    @property
    def _has_depth(self) -> bool:
        """Whether the volume's two faces are known and distinct."""
        return abs(self.config.volume_z_hi - self.config.volume_z_lo) > 1e-9

    def set_camera(self, camera):
        """Camera used to place the squares. Shared across all three layers."""
        self._camera = camera

    def update_for_camera(self, camera=None) -> bool:
        """Place the squares relative to the camera; returns True if anything moved.

        Two things are decided here:

        * **Which face is near.** The roles are fixed to their renderers
          (`front` on the layer drawn over the volume, `back` on the one drawn
          behind), but which volume face each sits on follows the camera — so
          orbiting underneath keeps the visible square on the face being
          looked at instead of leaving it pinned to the top.
        * **Whether the far square is worth drawing.** Two squares 54 units
          apart project at very different sizes once the camera is close; past
          `far_square_max_ratio` they stop overlapping and read as two
          unrelated grids, so only the near one is kept.
        """
        if camera is not None:
            self._camera = camera
        cam = self._camera
        if cam is None or not self._active_roles:
            return False

        z_center = self.config.z_height / 2.0 + self.config.z_offset
        if not self._has_depth:
            return self._place("front", z_center, z_center)

        cx, cy, cz = cam.GetPosition()
        _, _, dz = cam.GetDirectionOfProjection()

        def distance(z_plane):
            """Distance from the camera to a world-Z plane along the view axis.

            Positive means in front of the camera. None when the view is
            edge-on and the plane is never crossed.
            """
            if abs(dz) < 1e-9:
                return None
            return (z_plane - cz) / dz

        lo, hi = float(self.config.volume_z_lo), float(self.config.volume_z_hi)
        d_lo, d_hi = distance(lo), distance(hi)

        # Only faces IN FRONT of the camera are candidates. Zooming deep puts
        # the camera inside the volume's own Z slab, so one face ends up behind
        # it — ranking by signed distance would then pick the face behind the
        # camera as "near", which is exactly the square that used to vanish.
        candidates = sorted((d, z) for d, z in ((d_lo, lo), (d_hi, hi))
                            if d is not None and d > 0.0)

        if not candidates:
            # Whole volume behind the camera: nothing sensible to place.
            return self._set_visible_role("front", False) | self._set_visible_role("back", False)

        d_near, near_z = candidates[0]
        has_far = len(candidates) > 1
        d_far, far_z = candidates[1] if has_far else (0.0, near_z)

        # Degenerate guard: sitting exactly on a face makes the square project
        # at an unbounded size. Nudge it off the lens. With the faces chosen
        # correctly this effectively never fires — the near face is normally
        # about as far away as the cells it outlines.
        min_d = max(self.config.min_near_distance_frac
                    * _camera_focal_distance(cam), 1e-6)
        if d_near < min_d:
            near_z = cz + dz * min_d
            d_near = min_d

        show_far = has_far and (d_far / d_near) <= self.config.far_square_max_ratio

        changed = self._place("front", near_z, z_center)
        changed |= self._place("back", far_z, z_center)
        changed |= self._set_visible_role("front", True)
        changed |= self._set_visible_role("back", show_far)
        return changed

    def _set_visible_role(self, role: str, visible: bool) -> bool:
        """Toggle one role's actor. Independent of `set_visible`, which adds and
        removes actors for the whole heatmap."""
        entry = self._glyphs.get(role)
        if entry is None:
            return False
        want = 1 if visible else 0
        if entry["actor"].GetVisibility() == want:
            return False
        entry["actor"].SetVisibility(want)
        return True

    def _place(self, role: str, world_z: float, z_center: float) -> bool:
        """Move a role's actor so its square sits at `world_z`. Cheap: this only
        dirties the actor matrix, leaving the glyph instance buffer alone."""
        entry = self._glyphs.get(role)
        if entry is None:
            return False
        dz = world_z - z_center
        if abs(entry["actor"].GetPosition()[2] - dz) < 1e-9:
            return False
        entry["actor"].SetPosition(0.0, 0.0, dz)
        return True

    def _make_lut(self):
        """256-entry LUT encoding the value→brightness/opacity ramp, memoized.

        Per-instance color+alpha comes from mapping the instance scalar through
        this table (avoids per-instance RGBA direct-scalar quirks on glyph
        mappers).
        """
        c = self.config
        key = (round(c.outline_min_opacity, 4), round(c.outline_max_opacity, 4))
        cached = self._lut_cache.get(key)
        if cached is not None:
            return cached

        lut = vtk.vtkLookupTable()
        lut.SetNumberOfTableValues(_LUT_SIZE)
        lut.SetRange(0.0, 1.0)
        for i in range(_LUT_SIZE):
            t = i / (_LUT_SIZE - 1)
            a = (c.outline_min_opacity
                 + t * (c.outline_max_opacity - c.outline_min_opacity))
            lut.SetTableValue(i, t, t, t, a)
        lut.Build()
        if len(self._lut_cache) > 16:
            self._lut_cache.clear()
        self._lut_cache[key] = lut
        return lut

    def _role_renderer(self, role: str) -> Optional[vtkRenderer]:
        if role == "back":
            return self.renderer
        return self.outline_renderer

    def _ensure_glyph(self, role: str) -> dict:
        """Create the persistent actor+mapper for a role on first use."""
        entry = self._glyphs.get(role)
        if entry is not None:
            return entry
        mapper = vtkGlyph3DMapper()
        mapper.ScalingOff()
        mapper.OrientOff()
        mapper.SetScalarRange(0.0, 1.0)
        mapper.SetColorModeToMapScalars()
        mapper.ScalarVisibilityOn()

        actor = vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetLineWidth(self.config.outline_line_width)
        # Analytic picking is used instead of geometric pickers.
        actor.SetPickable(False)

        entry = {"actor": actor, "mapper": mapper, "renderer": self._role_renderer(role)}
        self._glyphs[role] = entry
        return entry

    def _activate(self, role: str, instances, source_poly, lut):
        """Point a persistent glyph actor at new instance/source data and show it."""
        entry = self._ensure_glyph(role)
        mapper = entry["mapper"]
        mapper.SetInputData(instances)
        mapper.SetSourceData(source_poly)
        mapper.SetLookupTable(lut)
        self._active_roles.add(role)
        ren = entry["renderer"]
        if self._visible and ren is not None and not ren.HasViewProp(entry["actor"]):
            ren.AddActor(entry["actor"])

    def _deactivate_all(self):
        for role in list(self._active_roles):
            entry = self._glyphs.get(role)
            if entry and entry["renderer"] is not None:
                entry["renderer"].RemoveActor(entry["actor"])
        self._active_roles.clear()

    @staticmethod
    def _rect_source(w: float, h: float, z: float) -> vtkPolyData:
        """4-edge rectangle polyline centered at the origin, at relative z."""
        hx, hy = w / 2.0, h / 2.0
        pts = vtkPoints()
        for x, y in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)):
            pts.InsertNextPoint(x, y, z)
        lines = vtkCellArray()
        for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)):
            ln = vtk.vtkLine()
            ln.GetPointIds().SetId(0, a)
            ln.GetPointIds().SetId(1, b)
            lines.InsertNextCell(ln)
        poly = vtkPolyData()
        poly.SetPoints(pts)
        poly.SetLines(lines)
        return poly

    # ──────────────────────────────────────────────
    # Visibility / lifecycle
    # ──────────────────────────────────────────────

    def set_color(self, color: Tuple[float, float, float]):
        self.config.base_color = color
        # Colors are baked into the (cached) LUT; a re-render with the new
        # color happens on the next update_field call.

    def set_visible(self, visible: bool):
        if visible == self._visible:
            return
        self._visible = visible
        for role in self._active_roles:
            entry = self._glyphs.get(role)
            if not entry or entry["renderer"] is None:
                continue
            if visible:
                if not entry["renderer"].HasViewProp(entry["actor"]):
                    entry["renderer"].AddActor(entry["actor"])
            else:
                entry["renderer"].RemoveActor(entry["actor"])


    def clear(self):
        self._deactivate_all()
        # Reset placement: the actor entries survive deactivation, so a stale
        # position (or a hidden far square) would carry into the next field.
        for entry in self._glyphs.values():
            entry["actor"].SetPosition(0.0, 0.0, 0.0)
            entry["actor"].SetVisibility(1)
        self._field = None

    @property
    def current_cell_size_vox(self) -> int:
        return self._field.cell_size_vox if self._field else 0

    # Analytic picking (display ray -> z-plane -> cell) and the hover highlight
    # actor lived here. Both existed only to serve hover-highlighting and the
    # right-click tile drill-down; each hover cost a pick plus a full
    # render/encode/push, and neither drives anything now that surfaces are
    # opt-in. `clear_highlight` remains as a no-op for existing call sites.

    def clear_highlight(self) -> bool:
        return False

    @property
    def tile_count(self) -> int:
        return int(self._field.counts.size) if self._field is not None else 0

    @property
    def is_visible(self) -> bool:
        return self._visible


def hex_to_rgb(color_hex: str) -> Tuple[float, float, float]:
    color_hex = color_hex.lstrip('#')
    r = int(color_hex[0:2], 16) / 255.0
    g = int(color_hex[2:4], 16) / 255.0
    b = int(color_hex[4:6], 16) / 255.0
    return (r, g, b)
