"""
Heatmap visualization using VTK cubes for spatial tiles.

This module renders analysis results as semi-transparent cubes
positioned at tile locations, with opacity based on overlap counts.
"""

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
    z_height: float = 194*0.28  # todo, data and meta data decide?
    z_offset: float = 0.0    
    edge_visibility: bool = True
    edge_color: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    edge_opacity: float = 0.3


class HeatmapRenderer:    
    def __init__(self, renderer: vtkRenderer, config: Optional[HeatmapConfig] = None):
        self.renderer = renderer
        self.config = config or HeatmapConfig()
        
        self._actors: Dict[Tuple[int, int], vtkActor] = {}
        self._visible = True
        
        self._current_tiles: List[TileData] = []
        self._current_spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    
    def update_tiles(
        self,
        tiles: List[TileData],
        spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0),
        color: Optional[Tuple[float, float, float]] = None,
    ):
        self.clear()
        
        if not tiles:
            return
        
        self._current_tiles = tiles
        self._current_spacing = spacing
        
        counts = [t.count for t in tiles]
        min_count = min(counts)
        max_count = max(counts)
        count_range = max_count - min_count if max_count > min_count else 1
        
        sx, sy, sz = spacing
        base_color = color if color else self.config.base_color
        
        for tile in tiles:
            x_center = (tile.x0 + tile.x1) / 2.0 * sx
            y_center = (tile.y0 + tile.y1) / 2.0 * sy
            z_center = self.config.z_height / 2.0 + self.config.z_offset
            
            x_size = (tile.x1 - tile.x0) * sx
            y_size = (tile.y1 - tile.y0) * sy
            z_size = self.config.z_height
            
            normalized = (tile.count - min_count) / count_range
            opacity = self.config.min_opacity + normalized * (self.config.max_opacity - self.config.min_opacity)
            
            actor = self._create_cube_actor(
                center=(x_center, y_center, z_center),
                size=(x_size, y_size, z_size),
                color=base_color,
                opacity=opacity,
            )
            
            tile_key = (tile.x0, tile.y0)
            self._actors[tile_key] = actor
            
            if self._visible:
                self.renderer.AddActor(actor)
    
    def _create_cube_actor(
        self,
        center: Tuple[float, float, float],
        size: Tuple[float, float, float],
        color: Tuple[float, float, float],
        opacity: float,
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
        
        prop = actor.GetProperty()
        prop.SetColor(*color)
        prop.SetOpacity(opacity)
        
        if self.config.edge_visibility:
            prop.EdgeVisibilityOn()
            prop.SetEdgeColor(*self.config.edge_color)
            prop.SetLineWidth(1.0)
        
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
    
    def clear(self):
        for actor in self._actors.values():
            self.renderer.RemoveActor(actor)
        self._actors.clear()
        self._current_tiles = []
    
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