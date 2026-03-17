from __future__ import annotations

from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass

import vtk
from vtkmodules.vtkFiltersSources import vtkCubeSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkRenderer

from ..analysis import TileData


@dataclass
class HeatmapConfig:
    base_color: Tuple[float, float, float] = (1.0, 1.0, 1.0)  
    min_opacity: float = 0.1
    max_opacity: float = 0.8
    z_height: float = 1.0  # todo, data and meta data decide?
    z_offset: float = 0.0    
    edge_visibility: bool = True
    edge_color: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    edge_opacity: float = 1.0
    edge_width: float = 1.0
    percentile_cutoff: float = 0.7  # Only show tiles above this active_fraction percentile
    opacity_scale: str = 'exponential'  # 'linear' or 'exponential'
    gamma: float = 8.0  # Used if opacity_scale is 'exponential', <1 spreads highs, >1 spreads lows
    outline_only: bool = False  # If True, draw only tile outlines (wireframe); brightness = gray→white by value, same thickness
    outline_line_width: float = 5.0  # Fixed line width for all outline tiles


class HeatmapRenderer:    
    def __init__(
        self,
        renderer: vtkRenderer,
        *,
        outline_renderer: Optional[vtkRenderer] = None,
        config: Optional[HeatmapConfig] = None,
    ):
        # renderer: used for filled tiles (behind the volume)
        # outline_renderer: used for wireframe-only tiles (in front of the volume)
        self.renderer = renderer
        self.outline_renderer = outline_renderer
        self.config = config or HeatmapConfig()
        
        self._actors: Dict[Tuple[int, int], vtkActor] = {}
        self._outline_actors: Dict[Tuple[int, int], vtkActor] = {}
        self._visible = True
        
        self._current_tiles: List[TileData] = []
        self._current_spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0)
        
        self._actor_to_tile: Dict[vtkActor, TileData] = {}
    
    def update_tiles(
        self,
        tiles: List[TileData],
        spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0),
        color: Optional[Tuple[float, float, float]] = None,
        outline_only: Optional[bool] = None,
    ):
        self.clear()
        
        if not tiles:
            return
        
        self._current_tiles = tiles
        self._current_spacing = spacing
        if outline_only is not None:
            self.config.outline_only = outline_only
        
        fractions = [t.active_fraction for t in tiles]
        fractions_sorted = sorted(fractions)
        cutoff_idx = max(0, int(len(fractions_sorted) * self.config.percentile_cutoff))
        cutoff_value = fractions_sorted[cutoff_idx]
        tiles = [t for t in tiles if t.active_fraction >= cutoff_value and t.active_fraction > 0]

        if not tiles:
            return
        max_frac = max(fractions) if fractions else 1.0
        scale = max_frac if max_frac > 0 else 1.0
        min_frac = min(fractions) if fractions else 0.0
        frac_range = scale - min_frac if scale > min_frac else 1.0
        
        sx, sy, sz = spacing
        base_color = color if color else self.config.base_color
        
        for tile in tiles:
            x_center = (tile.x0 + tile.x1) / 2.0 * sx
            y_center = (tile.y0 + tile.y1) / 2.0 * sy
            z_center = self.config.z_height / 2.0 + self.config.z_offset
            
            x_size = (tile.x1 - tile.x0) * sx
            y_size = (tile.y1 - tile.y0) * sy
            z_size = self.config.z_height
            
            # Outline mode: only brightness (gray→white by value); same line width for all.
            if self.config.outline_only:
                normalized = (tile.active_fraction - min_frac) / frac_range if frac_range > 0 else 1.0
                value_0_10 = max(0.0, min(10.0, normalized * 10.0))
                tile_color = (value_0_10 / 10.0, value_0_10 / 10.0, value_0_10 / 10.0)  # 0=black, 10=white
                opacity = 1.0
            elif self.config.opacity_scale == 'linear':
                normalized = tile.active_fraction / scale
            else:
                normalized = (tile.active_fraction - min_frac) / frac_range if frac_range > 0 else 0.0
                normalized = normalized ** self.config.gamma
            
            if not self.config.outline_only:
                opacity = self.config.min_opacity + normalized * (self.config.max_opacity - self.config.min_opacity)
                tile_color = base_color

            tile_key = (tile.x0, tile.y0)

            # Mode behavior:
            # - outline_only=True: draw wireframe in outline_renderer (front), no fill
            # - outline_only=False: draw fill in renderer (behind), no wireframe
            if self.config.outline_only:
                if self.outline_renderer is None:
                    continue
                outline_actor = self._create_cube_actor(
                    center=(x_center, y_center, z_center),
                    size=(x_size, y_size, z_size),
                    color=tile_color,
                    opacity=opacity,
                    outline_only=True,
                )
                self._outline_actors[tile_key] = outline_actor
                self._actor_to_tile[outline_actor] = tile
                if self._visible:
                    self.outline_renderer.AddActor(outline_actor)
            else:
                fill_actor = self._create_cube_actor(
                    center=(x_center, y_center, z_center),
                    size=(x_size, y_size, z_size),
                    color=tile_color,
                    opacity=opacity,
                    outline_only=False,
                )
                self._actors[tile_key] = fill_actor
                self._actor_to_tile[fill_actor] = tile
                if self._visible:
                    self.renderer.AddActor(fill_actor)
    
    def _create_cube_actor(
        self,
        center: Tuple[float, float, float],
        size: Tuple[float, float, float],
        color: Tuple[float, float, float],
        opacity: float,
        outline_only: bool = False,
    ) -> vtkActor:
        cube = vtkCubeSource()
        cube.SetCenter(*center)
        cube.SetXLength(size[0])
        cube.SetYLength(size[1])
        cube.SetZLength(size[2])
        
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(cube.GetOutputPort())
        
        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.SetScale(128, 128, 1.0)  # TOD
        
        prop = actor.GetProperty()
        prop.SetColor(*color)
        prop.SetOpacity(opacity)
        
        if outline_only:
            # Wireframe: full tile border; fixed thickness, brightness varies by tile value
            prop.SetRepresentationToWireframe()
            prop.SetLineWidth(self.config.outline_line_width)
        else:
            if self.config.edge_visibility:
                prop.EdgeVisibilityOff()
                prop.SetEdgeColor(*self.config.edge_color)
                prop.SetLineWidth(self.config.edge_width)
        
        return actor
    
    def set_color(self, color: Tuple[float, float, float]):
        self.config.base_color = color
        for actor in self._actors.values():
            actor.GetProperty().SetColor(*color)
    
    def set_visible(self, visible: bool):
        if visible == self._visible:
            return
        
        self._visible = visible
        
        for actor in self._actors.values():
            if visible:
                if not self.renderer.HasViewProp(actor):
                    self.renderer.AddActor(actor)
            else:
                self.renderer.RemoveActor(actor)
        if self.outline_renderer is not None:
            for actor in self._outline_actors.values():
                if visible:
                    if not self.outline_renderer.HasViewProp(actor):
                        self.outline_renderer.AddActor(actor)
                else:
                    self.outline_renderer.RemoveActor(actor)
    
    def clear(self):
        for actor in self._actors.values():
            self.renderer.RemoveActor(actor)
        if self.outline_renderer is not None:
            for actor in self._outline_actors.values():
                self.outline_renderer.RemoveActor(actor)
        self._actors.clear()
        self._outline_actors.clear()
        self._current_tiles = []
        self._actor_to_tile.clear()
        
    def get_tile_for_actor(self, actor) -> Optional[TileData]:
        """Reverse-lookup: given a picked vtkActor, return its TileData."""
        return self._actor_to_tile.get(actor)
    
    def get_tile_at_position(self, x: float, y: float) -> Optional[TileData]:
        sx, sy, _ = self._current_spacing
        
        vx = x / sx
        vy = y / sy
        
        for tile in self._current_tiles:
            if tile.x0 <= vx < tile.x1 and tile.y0 <= vy < tile.y1:
                return tile
        
        return None
    
    @property
    def tile_count(self) -> int:
        return len(self._actors)
    
    @property
    def is_visible(self) -> bool:
        return self._visible


def hex_to_rgb(color_hex: str) -> Tuple[float, float, float]:
    color_hex = color_hex.lstrip('#')
    r = int(color_hex[0:2], 16) / 255.0
    g = int(color_hex[2:4], 16) / 255.0
    b = int(color_hex[4:6], 16) / 255.0
    return (r, g, b)