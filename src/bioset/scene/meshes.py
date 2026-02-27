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
    ):
        self.mesh_dir = Path(mesh_dir)
        self.renderer = renderer
        self.base_sx, self.base_sy, self.base_sz = base_spacing

        self._manifest: Optional[dict] = None
        self._tiles: List[MeshTileInfo] = []

        self._actors: Dict[int, List[Tuple[str, vtkActor]]] = {}
        self._channel_colors: Dict[int, Tuple[float, float, float]] = {}

        self._load_manifest()

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

        print(f"[meshes] Added mesh {tile_key}: "
              f"offset=({tile_info.world_offset_x}, {tile_info.world_offset_y}) "
              f"depth={tile_info.tile_depth}")

    def deactivate_channel_mesh(self, channel_idx: int):
        """Remove all mesh actors for a channel from the scene."""
        if channel_idx not in self._actors:
            return
        for tile_key, actor in self._actors[channel_idx]:
            self.renderer.RemoveActor(actor)
            print(f"[meshes] Removed mesh {tile_key}")
        del self._actors[channel_idx]
        self._channel_colors.pop(channel_idx, None)

    def update_channel_color(self, channel_idx: int, color_rgb: Tuple[float, float, float]):
        """Update the color of all mesh actors for a channel."""
        if channel_idx not in self._actors:
            return
        self._channel_colors[channel_idx] = color_rgb
        for _, actor in self._actors[channel_idx]:
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
        self._actors.clear()
        self._channel_colors.clear()

    def get_active_channels(self) -> set:
        return set(self._actors.keys())


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