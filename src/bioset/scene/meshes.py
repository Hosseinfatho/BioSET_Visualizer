from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from vtkmodules.vtkIOGeometry import vtkGLTFReader
from vtkmodules.vtkFiltersGeometry import vtkCompositeDataGeometryFilter
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkPolyDataMapper,
    vtkRenderer,
)
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter


@dataclass
class MeshTileInfo:
    """Parsed tile entry from manifest.json."""
    file: str
    channel_idx: int
    channel_name: str
    tile_x: int
    tile_y: int
    world_offset_x: int  # voxel offset at component 0
    world_offset_y: int
    world_offset_z: int
    tile_width: int
    tile_height: int
    tile_depth: int
    isovalue: Optional[float]
    vertex_count: int
    face_count: int


class MeshManager:
    """
    Manages mesh tile actors in the VTK scene.

    Reads the manifest produced by extract_mesh_tiles.py, loads GLB
    files on demand, and adds/removes actors from the renderer.
    """

    def __init__(
        self,
        mesh_dir: Path | str,
        renderer: vtkRenderer,
        base_spacing: Tuple[float, float, float] = (0.14, 0.14, 0.28),
        nov_renderer: Optional[vtkRenderer] = None,
    ):
        self.mesh_dir = Path(mesh_dir)
        self.renderer = renderer
        self.nov_renderer = nov_renderer
        self.base_sx, self.base_sy, self.base_sz = base_spacing

        self._manifest: Optional[dict] = None
        self._tiles: List[MeshTileInfo] = []

        self._actors: Dict[int, List[Tuple[str, vtkActor]]] = {}
        self._nov_actors: Dict[int, List[Tuple[str, vtkActor]]] = {}
        self._channel_colors: Dict[int, Tuple[float, float, float]] = {}
        self._world_polydatas: Dict[int, object] = {}  # channel_idx -> vtkPolyData

        self._load_manifest()

    def set_nov_renderer(self, nov_renderer: Optional[vtkRenderer]) -> None:
        """Attach / replace the Optimal View renderer used for mirrored mesh actors."""
        if self.nov_renderer is not None and nov_renderer is not self.nov_renderer:
            self.clear_nov_meshes()
        self.nov_renderer = nov_renderer

    def _load_manifest(self):
        manifest_path = self.mesh_dir / "manifest.json"
        if not manifest_path.exists():
            print(f"[meshes] No manifest.json in {self.mesh_dir} — mesh overlay disabled")
            return

        with open(manifest_path) as f:
            self._manifest = json.load(f)

        for t in self._manifest.get("tiles", []):
            self._tiles.append(MeshTileInfo(**{
                k: t[k] for k in MeshTileInfo.__dataclass_fields__
            }))

        print(f"[meshes] Loaded manifest: {len(self._tiles)} tiles, "
              f"channels={[c['name'] for c in self._manifest.get('channels', [])]}")

    @property
    def is_available(self) -> bool:
        return self._manifest is not None and len(self._tiles) > 0

    @property
    def manifest_channels(self) -> List[dict]:
        if not self._manifest:
            return []
        return self._manifest.get("channels", [])

    def get_tiles_for_channel(self, channel_idx: int) -> List[MeshTileInfo]:
        return [t for t in self._tiles if t.channel_idx == channel_idx]

    def get_tile(self, channel_idx: int, tile_x: int, tile_y: int) -> Optional[MeshTileInfo]:
        for t in self._tiles:
            if t.channel_idx == channel_idx and t.tile_x == tile_x and t.tile_y == tile_y:
                return t
        return None

    def _load_glb_polydata(self, glb_path: Path):
        """Read a GLB file and return vtkPolyData."""
        if not glb_path.exists():
            print(f"[meshes] GLB not found: {glb_path}")
            return None

        reader = vtkGLTFReader()
        reader.SetFileName(str(glb_path))
        reader.Update()

        geom = vtkCompositeDataGeometryFilter()
        geom.SetInputConnection(reader.GetOutputPort())
        geom.Update()

        pd = geom.GetOutput()
        if pd is None or pd.GetNumberOfPoints() == 0:
            print(f"[meshes] Empty mesh: {glb_path}")
            return None

        bounds = pd.GetBounds()
        print(f"[meshes] RAW GLB bounds: "
              f"X[{bounds[0]:.1f}, {bounds[1]:.1f}] "
              f"Y[{bounds[2]:.1f}, {bounds[3]:.1f}] "
              f"Z[{bounds[4]:.1f}, {bounds[5]:.1f}] "
              f"  ({pd.GetNumberOfPoints()} pts, {pd.GetNumberOfCells()} cells)")

        return pd

    def _transform_to_world(self, polydata, offset_x: int, offset_y: int, offset_z: int):
        from vtkmodules.util.numpy_support import vtk_to_numpy, numpy_to_vtk
        import vtk

        sx = self.base_sx
        sy = self.base_sy
        sz = self.base_sz

        tx = offset_x * sx
        ty = offset_y * sy
        tz = offset_z * sz

        pts = polydata.GetPoints()
        coords = vtk_to_numpy(pts.GetData()).copy().astype(np.float64)

        coords[:, 0] = coords[:, 0] * sx + tx
        coords[:, 1] = coords[:, 1] * sy + ty
        coords[:, 2] = coords[:, 2] * sz + tz

        new_pts = vtk.vtkPoints()
        new_pts.SetData(numpy_to_vtk(coords, deep=True))

        output = vtk.vtkPolyData()
        output.DeepCopy(polydata)
        output.SetPoints(new_pts)

        return output

    def _make_actor(self, polydata, color: Tuple[float, float, float], opacity: float = 1.0):
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(polydata)

        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetOpacity(opacity)
        actor.GetProperty().SetInterpolationToPhong()
        actor.GetProperty().SetAmbient(0.4)
        actor.GetProperty().SetDiffuse(0.8)
        actor.GetProperty().SetSpecular(0.0)
        actor.GetProperty().SetSpecularPower(1.0)
        actor.SetPickable(False)

        return actor
    
    def activate_channel_mesh(
        self,
        channel_idx: int,
        color_rgb: Tuple[float, float, float] = (1.0, 1.0, 1.0),
        tile_x: int = 6,
        tile_y: int = 1,
        opacity: float = 1.0,
    ):
        """Load and display a mesh tile for the given channel."""
        if not self.is_available:
            print(f"[meshes] Mesh data not available — skipping")
            return

        self.deactivate_channel_mesh(channel_idx)

        tile_info = self.get_tile(channel_idx, tile_x, tile_y)
        if tile_info is None:
            print(f"[meshes] No tile ch{channel_idx}_{tile_x}_{tile_y} in manifest")
            return

        glb_path = self.mesh_dir / tile_info.file
        polydata = self._load_glb_polydata(glb_path)
        if polydata is None:
            return

        world_pd = self._transform_to_world(
            polydata,
            tile_info.world_offset_x,
            tile_info.world_offset_y,
            tile_info.world_offset_z,
        )

        # Diagnostics
        wb = world_pd.GetBounds()
        vol_z_top = tile_info.tile_depth * self.base_sz
        print(f"[meshes] WORLD bounds (spacing=({self.base_sx}, {self.base_sy}, {self.base_sz})): "
              f"X[{wb[0]:.1f}, {wb[1]:.1f}] "
              f"Y[{wb[2]:.1f}, {wb[3]:.1f}] "
              f"Z[{wb[4]:.1f}, {wb[5]:.1f}]")
        print(f"[meshes] Volume Z top = {vol_z_top:.1f} "
              f"(mesh {'ABOVE' if wb[4] > vol_z_top else 'INSIDE'} volume)")

        actor = self._make_actor(world_pd, color_rgb, opacity)
        self.renderer.AddActor(actor)

        tile_key = f"ch{channel_idx}_{tile_x}_{tile_y}"
        if channel_idx not in self._actors:
            self._actors[channel_idx] = []
        self._actors[channel_idx].append((tile_key, actor))
        self._channel_colors[channel_idx] = color_rgb
        self._world_polydatas[channel_idx] = world_pd  # cache for label placement

        # If Optimal View already has a mirrored mesh for this channel, refresh it.
        if self.nov_renderer is not None and channel_idx in self._nov_actors:
            for old_key, old_actor in self._nov_actors.pop(channel_idx, []):
                try:
                    self.nov_renderer.RemoveActor(old_actor)
                except Exception:
                    pass
            self._add_nov_actor(channel_idx, tile_key, world_pd, actor)

        print(f"[meshes] Added mesh {tile_key}: "
              f"offset=({tile_info.world_offset_x}, {tile_info.world_offset_y}) "
              f"depth={tile_info.tile_depth}")

    def _add_nov_actor(
        self,
        channel_idx: int,
        tile_key: str,
        world_pd,
        source_actor: vtkActor,
    ) -> None:
        """Create a NOV-popup actor mirroring a main-scene mesh actor."""
        if self.nov_renderer is None:
            return
        opacity = source_actor.GetProperty().GetOpacity()
        color = source_actor.GetProperty().GetColor()
        nov_actor = self._make_actor(world_pd, tuple(color), opacity)
        nov_actor.SetVisibility(source_actor.GetVisibility())
        self.nov_renderer.AddActor(nov_actor)
        if channel_idx not in self._nov_actors:
            self._nov_actors[channel_idx] = []
        self._nov_actors[channel_idx].append((tile_key, nov_actor))

    def sync_nov_meshes(self, visible_channel_ids: Optional[List[int]] = None) -> None:
        """Mirror all active main-scene meshes into the NOV popup with the same colors."""
        if self.nov_renderer is None:
            return
        self.clear_nov_meshes()
        visible = set(visible_channel_ids) if visible_channel_ids is not None else None
        for channel_idx, entries in list(self._actors.items()):
            if visible is not None and channel_idx not in visible:
                continue
            world_pd = self._world_polydatas.get(channel_idx)
            for tile_key, actor in entries:
                pd = world_pd
                if pd is None:
                    mapper = actor.GetMapper()
                    pd = mapper.GetInput() if mapper is not None else None
                if pd is None:
                    continue
                self._add_nov_actor(channel_idx, tile_key, pd, actor)
        print(f"[meshes] Synced {sum(len(v) for v in self._nov_actors.values())} mesh actor(s) to NOV")

    def clear_nov_meshes(self) -> None:
        """Remove all mirrored mesh actors from the NOV popup renderer."""
        if not self._nov_actors:
            return
        for _channel_idx, entries in list(self._nov_actors.items()):
            for tile_key, actor in entries:
                if self.nov_renderer is not None:
                    try:
                        self.nov_renderer.RemoveActor(actor)
                    except Exception:
                        pass
                print(f"[meshes] Removed NOV mesh {tile_key}")
        self._nov_actors.clear()

    def set_nov_mesh_visibility(self, selected_channel_ids: List[int]) -> None:
        """Show/hide NOV mesh actors to match Optimal View channel selection."""
        sel = set(selected_channel_ids) if selected_channel_ids else set()
        # If NOV actors were never built (e.g. popup just opened), sync first.
        if self.nov_renderer is not None and self._actors and not self._nov_actors:
            self.sync_nov_meshes(selected_channel_ids)
            return
        for channel_idx, entries in self._nov_actors.items():
            vis = 1 if channel_idx in sel else 0
            for _, actor in entries:
                actor.SetVisibility(vis)

    def deactivate_channel_mesh(self, channel_idx: int):
        """Remove all mesh actors for a channel from the scene."""
        if channel_idx not in self._actors:
            return
        for tile_key, actor in self._actors[channel_idx]:
            self.renderer.RemoveActor(actor)
            print(f"[meshes] Removed mesh {tile_key}")
        del self._actors[channel_idx]
        if channel_idx in self._nov_actors:
            for tile_key, actor in self._nov_actors[channel_idx]:
                if self.nov_renderer is not None:
                    try:
                        self.nov_renderer.RemoveActor(actor)
                    except Exception:
                        pass
                print(f"[meshes] Removed NOV mesh {tile_key}")
            del self._nov_actors[channel_idx]
        self._channel_colors.pop(channel_idx, None)
        self._world_polydatas.pop(channel_idx, None)

    def update_channel_color(self, channel_idx: int, color_rgb: Tuple[float, float, float]):
        """Update the color of all mesh actors for a channel."""
        if channel_idx not in self._actors and channel_idx not in self._nov_actors:
            return
        self._channel_colors[channel_idx] = color_rgb
        for _, actor in self._actors.get(channel_idx, []):
            actor.GetProperty().SetColor(*color_rgb)
        for _, actor in self._nov_actors.get(channel_idx, []):
            actor.GetProperty().SetColor(*color_rgb)

    def update_spacing(self, sx: float, sy: float, sz: float):
        """Update base spacing (called when metadata is loaded)."""
        self.base_sx = sx
        self.base_sy = sy
        self.base_sz = sz
        print(f"[meshes] Spacing updated: ({sx}, {sy}, {sz})")

    def clear(self):
        """Remove all mesh actors from the scene."""
        for channel_idx in list(self._actors.keys()):
            self.deactivate_channel_mesh(channel_idx)
        self.clear_nov_meshes()
        self._actors.clear()
        self._nov_actors.clear()
        self._channel_colors.clear()
        self._world_polydatas.clear()

    def get_active_channels(self) -> set:
        return set(self._actors.keys())
    
    def find_tile_at_voxel(self, channel_idx: int, vox_x: float, vox_y: float) -> Optional[MeshTileInfo]:
        """Find the mesh tile whose footprint contains (vox_x, vox_y) in full-res voxel space."""
        for t in self._tiles:
            if t.channel_idx != channel_idx:
                continue
            if (t.world_offset_x <= vox_x < t.world_offset_x + t.tile_width and
                    t.world_offset_y <= vox_y < t.world_offset_y + t.tile_height):
                return t
        return None

    def get_tile_world_center(self, tile: 'MeshTileInfo') -> Tuple[float, float, float]:
        """Get world-space center of a mesh tile."""
        cx = (tile.world_offset_x + tile.tile_width / 2.0) * self.base_sx
        cy = (tile.world_offset_y + tile.tile_height / 2.0) * self.base_sy
        cz = (tile.tile_depth / 2.0) * self.base_sz
        return (cx, cy, cz)

    def channel_idx_for_name(self, channel_name: str) -> Optional[int]:
        """Map a human-readable channel name to its manifest channel_idx.

        manifest channel_idx (0, 1, 2…) is NOT the same as the state channel ID.
        Returns None if the name is not found in the manifest.
        """
        for t in self._tiles:
            if t.channel_name == channel_name:
                return t.channel_idx
        return None

    def get_channel_actor(self, channel_idx: int):
        """Return the first VTK actor for a channel, or None if not active."""
        actors = self._actors.get(channel_idx)
        if actors:
            return actors[0][1]
        return None

    def get_channel_polydata(self, channel_idx: int):
        """Return the cached world-space vtkPolyData for a channel, or None."""
        if channel_idx in self._world_polydatas:
            return self._world_polydatas[channel_idx]
        # Fallback: try to extract from the actor's mapper
        actor = self.get_channel_actor(channel_idx)
        if actor is None:
            return None
        mapper = actor.GetMapper()
        if mapper is None:
            return None
        pd = mapper.GetInput()
        if pd is not None:
            self._world_polydatas[channel_idx] = pd
        return pd


# for debug
def create_red_cube(center=(0.0, 0.0, 0.0), size=1.0, opacity=1.0):
    import vtk
    cube = vtk.vtkCubeSource()
    cube.SetCenter(*center)
    cube.SetXLength(size)
    cube.SetYLength(size)
    cube.SetZLength(size)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(cube.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(1.0, 0.0, 0.0)
    actor.GetProperty().SetOpacity(opacity)
    return actor