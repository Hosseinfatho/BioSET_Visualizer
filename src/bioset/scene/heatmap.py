from __future__ import annotations

from typing import Optional, Tuple
from dataclasses import dataclass

import numpy as np
import vtk
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersSources import vtkCubeSource
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
    min_opacity: float = 0.1
    max_opacity: float = 0.8
    # Outline opacity is independent from filled-cell opacity.
    # In outline mode we encode cell value via opacity (and grayscale brightness),
    # while keeping line thickness fixed.
    outline_min_opacity: float = 0.05
    outline_max_opacity: float = 0.95
    z_height: float = 1.0
    z_offset: float = 0.0
    percentile_cutoff: float = 0.01  # Only show cells above this active_fraction percentile
    opacity_scale: str = 'exponential'  # 'linear' or 'exponential'
    gamma: float = 0.5  # Used if opacity_scale is 'exponential', <1 spreads highs, >1 spreads lows
    outline_only: bool = False
    outline_line_width: float = 5.0  # Fixed line width for all outline cells
    # If >0 in outline_only mode, draw a matching outline behind the volume (back)
    # and connect the 4 corners with the same line width/brightness.
    outline_box_depth: float = 0.0
    outline_box_back_z: float = 0.0
    outline_box_front_z: float = 0.0  # if set, world Z of front plane (in front of image)


_LUT_SIZE = 256


class HeatmapRenderer:
    """Heatmap cells rendered as instanced glyphs (one vtkGlyph3DMapper actor
    per mode-layer instead of one vtkActor per cell — the fine grid can carry
    hundreds of thousands of cells).

    Modes (same visual encodings as the per-actor implementation this replaces):
      - filled: solid cubes behind the volume; value → opacity at constant color
      - outline: wireframe box grid bracketing the volume (front rect + corner
        connectors in front, back rect behind); value → grayscale brightness +
        opacity at fixed line width
    """

    def __init__(
        self,
        renderer: vtkRenderer,
        *,
        outline_renderer: Optional[vtkRenderer] = None,
        config: Optional[HeatmapConfig] = None,
    ):
        # renderer: layer 0, behind the volume (filled cells + back rects)
        # outline_renderer: layer 2, in front (front rects + connectors)
        self.renderer = renderer
        self.outline_renderer = outline_renderer
        self.config = config or HeatmapConfig()

        self._visible = True
        self._actors: list[tuple[vtkRenderer, vtkActor]] = []  # (owning renderer, actor)

        self._field: Optional[HeatmapField] = None
        self._current_spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0)
        self._cell_index: dict[tuple[int, int], int] = {}  # (cy, cx) -> row in field

        # Persistent hover-highlight actor (single wireframe rect, repositioned)
        self._highlight_actor: Optional[vtkActor] = None
        self._highlight_cell: Optional[tuple[int, int]] = None

    # ──────────────────────────────────────────────
    # Update
    # ──────────────────────────────────────────────

    def update_field(
        self,
        field: Optional[HeatmapField],
        spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0),
        color: Optional[Tuple[float, float, float]] = None,
        outline_only: Optional[bool] = None,
    ):
        self.clear()
        if field is None or field.counts.size == 0:
            return
        if outline_only is not None:
            self.config.outline_only = outline_only

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
        self._cell_index = {
            (int(cy), int(cx)): i for i, (cy, cx) in enumerate(cells)
        }

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

        if self.config.outline_only:
            if self.outline_renderer is None:
                return
            lut = self._make_lut(outline=True)
            if self.config.outline_box_depth and self.config.outline_box_depth > 0:
                # Box grid: front + back rectangles bracketing the volume,
                # corner connectors between them. z offsets are baked into the
                # glyph sources relative to the instance-point plane.
                z_back = z_center
                if self.config.outline_box_front_z:
                    z_front = float(self.config.outline_box_front_z)
                else:
                    z_front = z_center + self.config.z_height / 2.0
                    z_back = z_front - float(self.config.outline_box_depth)
                rel_front = z_front - z_center
                rel_back = z_back - z_center

                front = self._rect_source(cw_x, cw_y, rel_front)
                back = self._rect_source(cw_x, cw_y, rel_back)
                connectors = self._connector_source(cw_x, cw_y, rel_back, rel_front)
                self._add_glyph_actor(instances, front, lut, self.outline_renderer, lines=True)
                self._add_glyph_actor(instances, back, lut, self.renderer, lines=True)
                self._add_glyph_actor(instances, connectors, lut, self.outline_renderer, lines=True)
            else:
                # Simple wireframe cubes
                cube = vtkCubeSource()
                cube.SetXLength(cw_x)
                cube.SetYLength(cw_y)
                cube.SetZLength(self.config.z_height)
                cube.Update()
                self._add_glyph_actor(
                    instances, cube.GetOutput(), lut, self.outline_renderer,
                    lines=False, wireframe=True,
                )
        else:
            lut = self._make_lut(outline=False, color=color or self.config.base_color)
            cube = vtkCubeSource()
            cube.SetXLength(cw_x)
            cube.SetYLength(cw_y)
            cube.SetZLength(self.config.z_height)
            cube.Update()
            self._add_glyph_actor(instances, cube.GetOutput(), lut, self.renderer)

    def _make_lut(self, outline: bool, color: Tuple[float, float, float] = (1, 1, 1)):
        """256-entry LUT encoding the value→color/opacity ramps.

        Per-instance color+alpha comes from mapping the instance scalar through
        this table (avoids per-instance RGBA direct-scalar quirks on glyph
        mappers).
        """
        lut = vtk.vtkLookupTable()
        lut.SetNumberOfTableValues(_LUT_SIZE)
        lut.SetRange(0.0, 1.0)
        for i in range(_LUT_SIZE):
            t = i / (_LUT_SIZE - 1)
            if outline:
                # grayscale brightness + opacity ramp, fixed line width
                a = (self.config.outline_min_opacity
                     + t * (self.config.outline_max_opacity - self.config.outline_min_opacity))
                lut.SetTableValue(i, t, t, t, a)
            else:
                tt = t ** self.config.gamma if self.config.opacity_scale == 'exponential' else t
                a = self.config.min_opacity + tt * (self.config.max_opacity - self.config.min_opacity)
                lut.SetTableValue(i, color[0], color[1], color[2], a)
        lut.Build()
        return lut

    def _add_glyph_actor(self, instances, source_poly, lut, target_renderer,
                         lines: bool = False, wireframe: bool = False):
        mapper = vtkGlyph3DMapper()
        mapper.SetInputData(instances)
        mapper.SetSourceData(source_poly)
        mapper.ScalingOff()
        mapper.OrientOff()
        mapper.SetLookupTable(lut)
        mapper.SetScalarRange(0.0, 1.0)
        mapper.SetColorModeToMapScalars()
        mapper.ScalarVisibilityOn()

        actor = vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        if lines or wireframe:
            if wireframe:
                prop.SetRepresentationToWireframe()
            prop.SetLineWidth(self.config.outline_line_width)
        # Analytic picking is used instead of geometric pickers.
        actor.SetPickable(False)

        self._actors.append((target_renderer, actor))
        if self._visible:
            target_renderer.AddActor(actor)

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

    @staticmethod
    def _connector_source(w: float, h: float, z0: float, z1: float) -> vtkPolyData:
        """4 corner connector lines between relative z planes z0 and z1."""
        hx, hy = w / 2.0, h / 2.0
        corners = ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))
        pts = vtkPoints()
        for x, y in corners:
            pts.InsertNextPoint(x, y, z0)
        for x, y in corners:
            pts.InsertNextPoint(x, y, z1)
        lines = vtkCellArray()
        for i in range(4):
            ln = vtk.vtkLine()
            ln.GetPointIds().SetId(0, i)
            ln.GetPointIds().SetId(1, i + 4)
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
        # Colors are baked into the LUT; a re-render with the new color happens
        # on the next update_field call.

    def set_visible(self, visible: bool):
        if visible == self._visible:
            return
        self._visible = visible
        for ren, actor in self._actors:
            if visible:
                if not ren.HasViewProp(actor):
                    ren.AddActor(actor)
            else:
                ren.RemoveActor(actor)
        if not visible:
            self.clear_highlight()

    def clear(self):
        for ren, actor in self._actors:
            ren.RemoveActor(actor)
        self._actors.clear()
        self._field = None
        self._cell_index = {}
        self.clear_highlight()

    # ──────────────────────────────────────────────
    # Analytic picking (no geometric pickers: the heatmap is an axis-aligned
    # grid on a known z-plane, so a display ray → plane intersection resolves
    # the cell directly, independent of the instanced geometry)
    # ──────────────────────────────────────────────

    @property
    def current_cell_size_vox(self) -> int:
        return self._field.cell_size_vox if self._field else 0

    def cell_at(self, cy: int, cx: int) -> Optional[TileData]:
        i = self._cell_index.get((cy, cx))
        if i is None:
            return None
        f = self._field
        return TileData(
            x0=int(f.cells_yx[i, 1]), x1=int(f.cells_yx[i, 1]) + 1,
            y0=int(f.cells_yx[i, 0]), y1=int(f.cells_yx[i, 0]) + 1,
            count=int(f.counts[i]),
            active_fraction=float(f.fractions[i]),
        )

    def get_cell_at_display(self, x_disp: float, y_disp: float) -> Optional[TileData]:
        """Cell under a display-space point (VTK display coords, y up)."""
        if self._field is None:
            return None
        ren = self.renderer
        plane_z = self.config.z_height / 2.0 + self.config.z_offset

        def world_at(depth: float):
            ren.SetDisplayPoint(float(x_disp), float(y_disp), depth)
            ren.DisplayToWorld()
            w = ren.GetWorldPoint()
            if w[3] != 0:
                return np.array(w[:3]) / w[3]
            return np.array(w[:3])

        p0 = world_at(0.0)
        p1 = world_at(1.0)
        dz = p1[2] - p0[2]
        if abs(dz) < 1e-12:
            return None
        t = (plane_z - p0[2]) / dz
        wx = p0[0] + t * (p1[0] - p0[0])
        wy = p0[1] + t * (p1[1] - p0[1])

        sx, sy, _ = self._current_spacing
        cw_x = self._field.cell_size_vox * sx
        cw_y = self._field.cell_size_vox * sy
        if cw_x <= 0 or cw_y <= 0:
            return None
        cx = int(np.floor(wx / cw_x))
        cy = int(np.floor(wy / cw_y))
        return self.cell_at(cy, cx)

    def cell_world_center(self, tile: TileData) -> Tuple[float, float]:
        """World-space (x, y) center of a picked cell."""
        sx, sy, _ = self._current_spacing
        cs = self.current_cell_size_vox
        return (
            (tile.x0 + tile.x1) / 2.0 * cs * sx,
            (tile.y0 + tile.y1) / 2.0 * cs * sy,
        )

    # ──────────────────────────────────────────────
    # Hover highlight (one persistent actor, repositioned)
    # ──────────────────────────────────────────────

    def highlight_cell(self, tile: Optional[TileData]) -> bool:
        """Show the hover highlight on a cell (None clears). Returns True if
        the highlight changed."""
        if tile is None:
            return self.clear_highlight()
        key = (tile.y0, tile.x0)
        if key == self._highlight_cell:
            return False
        self._highlight_cell = key

        sx, sy, _ = self._current_spacing
        cs = self.current_cell_size_vox
        cx, cy = self.cell_world_center(tile)
        target = self.outline_renderer or self.renderer

        if self._highlight_actor is None:
            src = self._rect_source(1.0, 1.0, 0.0)
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(src)
            actor = vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            prop.SetColor(1.0, 1.0, 0.0)
            prop.SetLineWidth(3.0)
            prop.SetOpacity(1.0)
            actor.SetPickable(False)
            self._highlight_actor = actor

        actor = self._highlight_actor
        actor.SetScale(cs * sx, cs * sy, 1.0)
        z = self.config.outline_box_front_z or (
            self.config.z_height / 2.0 + self.config.z_offset)
        actor.SetPosition(cx, cy, z + 1.0)
        if not target.HasViewProp(actor):
            target.AddActor(actor)
        return True

    def clear_highlight(self) -> bool:
        if self._highlight_actor is None or self._highlight_cell is None:
            return False
        self._highlight_cell = None
        for ren in (self.outline_renderer, self.renderer):
            if ren is not None and ren.HasViewProp(self._highlight_actor):
                ren.RemoveActor(self._highlight_actor)
        return True

    # ──────────────────────────────────────────────

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
