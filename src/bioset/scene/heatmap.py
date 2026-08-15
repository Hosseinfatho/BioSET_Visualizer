from __future__ import annotations

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
    # If >0, draw a matching outline behind the volume (back) and connect the
    # 4 corners with the same line width/brightness.
    outline_box_depth: float = 0.0
    outline_box_back_z: float = 0.0
    outline_box_front_z: float = 0.0  # if set, world Z of front plane (in front of image)


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
        # Two flat squares bracketing the volume: one drawn in front of it, one
        # behind. z offsets are baked into the glyph sources relative to the
        # instance-point plane.
        if self.config.outline_box_depth and self.config.outline_box_depth > 0:
            z_back = z_center
            if self.config.outline_box_front_z:
                z_front = float(self.config.outline_box_front_z)
            else:
                z_front = z_center + self.config.z_height / 2.0
                z_back = z_front - float(self.config.outline_box_depth)

            self._activate("front", instances,
                           self._rect_source(cw_x, cw_y, z_front - z_center), lut)
            self._activate("back", instances,
                           self._rect_source(cw_x, cw_y, z_back - z_center), lut)
        else:
            # No volume depth to bracket (bounds unknown): one square on the
            # cell plane rather than a box of zero thickness.
            self._activate("front", instances,
                           self._rect_source(cw_x, cw_y, 0.0), lut)

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
