from __future__ import annotations

from typing import Dict, Optional, Tuple
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

# Glyph roles: which persistent actor draws what, and on which layer.
#   filled     — solid cubes, layer-0 renderer (behind the volume)
#   wire       — wireframe cubes (simple outline mode), layer-2 renderer
#   front      — box-grid front rectangles, layer-2 renderer
#   back       — box-grid back rectangles, layer-0 renderer
#   connectors — box-grid corner connectors, layer-2 renderer
_ROLES = ("filled", "wire", "front", "back", "connectors")


class HeatmapRenderer:
    """Heatmap cells rendered as instanced glyphs (one persistent
    vtkGlyph3DMapper actor per role instead of one vtkActor per cell — the
    fine grid can carry hundreds of thousands of cells).

    Modes (same visual encodings as the per-actor implementation this replaces):
      - filled: solid cubes behind the volume; value → opacity at constant color
      - outline: wireframe box grid bracketing the volume (front rect + corner
        connectors in front, back rect behind); value → grayscale brightness +
        opacity at fixed line width

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
        # renderer: layer 0, behind the volume (filled cells + back rects)
        # outline_renderer: layer 2, in front (front rects + connectors)
        self.renderer = renderer
        self.outline_renderer = outline_renderer
        self.config = config or HeatmapConfig()

        self._visible = True
        self._glyphs: Dict[str, dict] = {}     # role -> {actor, mapper, renderer}
        self._active_roles: set[str] = set()
        self._lut_cache: Dict[tuple, vtk.vtkLookupTable] = {}

        self._field: Optional[HeatmapField] = None
        self._current_spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0)
        self._index_grid: Optional[np.ndarray] = None  # (ny, nx) int32 -> field row

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
        # Dense picking index: vectorized build, O(1) lookup (the Python-dict
        # variant dominated main-thread apply time at fine levels).
        self._index_grid = np.full((field.ny, field.nx), -1, dtype=np.int32)
        self._index_grid[cells[:, 0], cells[:, 1]] = np.arange(n, dtype=np.int32)

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

                self._activate("front", instances,
                               self._rect_source(cw_x, cw_y, rel_front), lut)
                self._activate("back", instances,
                               self._rect_source(cw_x, cw_y, rel_back), lut)
                self._activate("connectors", instances,
                               self._connector_source(cw_x, cw_y, rel_back, rel_front), lut)
            else:
                cube = vtkCubeSource()
                cube.SetXLength(cw_x)
                cube.SetYLength(cw_y)
                cube.SetZLength(self.config.z_height)
                cube.Update()
                self._activate("wire", instances, cube.GetOutput(), lut)
        else:
            lut = self._make_lut(outline=False, color=color or self.config.base_color)
            cube = vtkCubeSource()
            cube.SetXLength(cw_x)
            cube.SetYLength(cw_y)
            cube.SetZLength(self.config.z_height)
            cube.Update()
            self._activate("filled", instances, cube.GetOutput(), lut)

    def _make_lut(self, outline: bool, color: Tuple[float, float, float] = (1, 1, 1)):
        """256-entry LUT encoding the value→color/opacity ramps, memoized.

        Per-instance color+alpha comes from mapping the instance scalar through
        this table (avoids per-instance RGBA direct-scalar quirks on glyph
        mappers).
        """
        c = self.config
        key = (
            outline,
            tuple(round(x, 4) for x in color),
            c.opacity_scale, round(c.gamma, 4),
            round(c.min_opacity, 4), round(c.max_opacity, 4),
            round(c.outline_min_opacity, 4), round(c.outline_max_opacity, 4),
        )
        cached = self._lut_cache.get(key)
        if cached is not None:
            return cached

        lut = vtk.vtkLookupTable()
        lut.SetNumberOfTableValues(_LUT_SIZE)
        lut.SetRange(0.0, 1.0)
        for i in range(_LUT_SIZE):
            t = i / (_LUT_SIZE - 1)
            if outline:
                # grayscale brightness + opacity ramp, fixed line width
                a = (c.outline_min_opacity
                     + t * (c.outline_max_opacity - c.outline_min_opacity))
                lut.SetTableValue(i, t, t, t, a)
            else:
                tt = t ** c.gamma if c.opacity_scale == 'exponential' else t
                a = c.min_opacity + tt * (c.max_opacity - c.min_opacity)
                lut.SetTableValue(i, color[0], color[1], color[2], a)
        lut.Build()
        if len(self._lut_cache) > 16:
            self._lut_cache.clear()
        self._lut_cache[key] = lut
        return lut

    def _role_renderer(self, role: str) -> Optional[vtkRenderer]:
        if role in ("filled", "back"):
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
        if role != "filled":
            if role == "wire":
                prop.SetRepresentationToWireframe()
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
        if not visible:
            self.clear_highlight()

    def clear(self):
        self._deactivate_all()
        self._field = None
        self._index_grid = None
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
        f = self._field
        if f is None or self._index_grid is None:
            return None
        if not (0 <= cy < self._index_grid.shape[0] and 0 <= cx < self._index_grid.shape[1]):
            return None
        i = int(self._index_grid[cy, cx])
        if i < 0:
            return None
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
