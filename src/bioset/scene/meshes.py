"""Mesh tile surfaces from the preprocessing output.

The pipeline writes one GLB per (channel, tile) at r = 0. On the reference run
that is 7,543 tiles totalling 157M triangles, so the scene cannot simply hold
them: tiles are streamed for the current viewport, for channels the user has
explicitly enabled, under a triangle budget, with an LRU of live actors.

Coordinates: mesh vertices are tile-local RAW VOXELS. World position is
``(local + world_offset) * voxel_size_um``. Every tile spans the full depth
(``world_offset_z`` is 0 throughout), so z never takes part in culling.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from vtkmodules.vtkIOGeometry import vtkGLTFReader
from vtkmodules.vtkFiltersGeometry import vtkCompositeDataGeometryFilter
from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkPolyDataMapper,
    vtkRenderer,
)
from vtkmodules.vtkRenderingLOD import vtkLODActor


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
    """Streams mesh tile actors into the VTK scene.

    A channel shows surfaces only after `enable_channel`; enabled channels then
    track the viewport. Loading (file read, normals, world transform) is pure
    data work and safe on a worker thread — actor creation and `AddActor` are
    not, and stay on the main thread via `apply_loaded`.
    """

    # Out-of-memory guard, NOT a display budget: every tile intersecting the
    # viewport is shown. Only a pathological view trips this — all 49 channels
    # over the whole slide is 157M faces — and when it does, it says so.
    HARD_FACE_CEILING = 60_000_000
    # Tiles kept alive beyond the visible set, so a small pan does not re-read
    # what it just dropped. Sized as a fraction of the ceiling.
    LRU_FACE_BUDGET = 24_000_000
    # Points VTK falls back to per tile on drag frames when even the decimated
    # representation is too slow. ~2% of a median tile's vertices.
    LOD_CLOUD_POINTS = 400

    def __init__(
        self,
        mesh_dir: Path | str | None,
        renderer: vtkRenderer,
        base_spacing: Tuple[float, float, float] = (0.14, 0.14, 0.28),
        face_budget: int = HARD_FACE_CEILING,
        nov_renderer: Optional[vtkRenderer] = None,
    ):
        self.renderer = renderer
        self.nov_renderer = nov_renderer
        self.base_sx, self.base_sy, self.base_sz = base_spacing
        self.hard_face_ceiling = int(face_budget)

        self.mesh_dir: Optional[Path] = None
        self._manifest: Optional[dict] = None
        self._tiles: List[MeshTileInfo] = []
        # Vectorized tile index — the viewport test runs on every camera settle
        # and a Python loop over 7.5k records per query is not free.
        self._t_chan = np.zeros(0, dtype=np.int32)
        self._t_x0 = np.zeros(0, dtype=np.int64)
        self._t_x1 = np.zeros(0, dtype=np.int64)
        self._t_y0 = np.zeros(0, dtype=np.int64)
        self._t_y1 = np.zeros(0, dtype=np.int64)
        self._t_faces = np.zeros(0, dtype=np.int64)

        self._enabled: Dict[int, Tuple[float, float, float]] = {}
        self._actors: "OrderedDict[tuple, vtkActor]" = OrderedDict()
        self._actor_faces: Dict[tuple, int] = {}
        self._live_faces = 0
        self._channel_colors: Dict[int, Tuple[float, float, float]] = {}
        # Optimal View mirrors the main scene's mesh actors into its own
        # renderer, keyed identically so the two stay in step as tiles stream
        # in and out. None in `_nov_visible` means "every enabled channel".
        self._nov_actors: "OrderedDict[tuple, vtkActor]" = OrderedDict()
        self._nov_visible: Optional[set] = None
        self._polydata_cache: Dict[int, object] = {}
        self._lock = threading.Lock()
        self.shading_enabled = True
        self.last_truncated = False

        if mesh_dir:
            self.set_mesh_dir(mesh_dir)

    # ── manifest ───────────────────────────────────────────

    def set_mesh_dir(self, mesh_dir: Path | str):
        """Point at a new mesh directory, dropping anything already in the scene.

        The meshes now ship inside the analysis results, so this is called when
        a results directory is loaded rather than configured up front.
        """
        new_dir = Path(mesh_dir)
        if self.mesh_dir is not None and new_dir == self.mesh_dir:
            return
        self.clear()
        self.mesh_dir = new_dir
        self._manifest = None
        self._tiles = []
        self._load_manifest()

    def _load_manifest(self):
        manifest_path = self.mesh_dir / "manifest.json"
        if not manifest_path.exists():
            print(f"[meshes] No manifest.json in {self.mesh_dir} — mesh overlay disabled")
            self._build_index()
            return

        with open(manifest_path) as f:
            self._manifest = json.load(f)

        for t in self._manifest.get("tiles", []):
            # Selects only known keys, so manifest additions pass through.
            self._tiles.append(MeshTileInfo(**{
                k: t[k] for k in MeshTileInfo.__dataclass_fields__
            }))
        self._build_index()

        # The manifest records the voxel size the vertices were written against;
        # prefer it over whatever spacing the image metadata happens to carry.
        vs = self._manifest.get("voxel_size_um")
        if vs and len(vs) == 3:
            self.base_sz, self.base_sy, self.base_sx = (float(v) for v in vs)

        print(f"[meshes] Loaded manifest: {len(self._tiles)} tiles, "
              f"{len(self.manifest_channels)} channels, "
              f"{int(self._t_faces.sum()):,} faces total")

    def _build_index(self):
        n = len(self._tiles)
        self._t_chan = np.array([t.channel_idx for t in self._tiles], dtype=np.int32)
        self._t_x0 = np.array([t.world_offset_x for t in self._tiles], dtype=np.int64)
        self._t_y0 = np.array([t.world_offset_y for t in self._tiles], dtype=np.int64)
        self._t_x1 = self._t_x0 + np.array([t.tile_width for t in self._tiles], dtype=np.int64)
        self._t_y1 = self._t_y0 + np.array([t.tile_height for t in self._tiles], dtype=np.int64)
        self._t_faces = np.array([t.face_count for t in self._tiles], dtype=np.int64)
        if n == 0:
            for a in ("_t_x0", "_t_y0", "_t_x1", "_t_y1", "_t_faces"):
                setattr(self, a, np.zeros(0, dtype=np.int64))

    @property
    def is_available(self) -> bool:
        return self._manifest is not None and len(self._tiles) > 0

    @property
    def manifest_channels(self) -> List[dict]:
        return self._manifest.get("channels", []) if self._manifest else []

    def get_tiles_for_channel(self, channel_idx: int) -> List[MeshTileInfo]:
        return [t for t in self._tiles if t.channel_idx == channel_idx]

    def get_tile(self, channel_idx: int, tile_x: int, tile_y: int) -> Optional[MeshTileInfo]:
        for t in self._tiles:
            if t.channel_idx == channel_idx and t.tile_x == tile_x and t.tile_y == tile_y:
                return t
        return None

    def find_tile_at_voxel(self, channel_idx: int, vox_x: float, vox_y: float
                           ) -> Optional[MeshTileInfo]:
        """The tile whose footprint contains (vox_x, vox_y) in full-res voxels."""
        if not self._tiles:
            return None
        hit = ((self._t_chan == channel_idx)
               & (self._t_x0 <= vox_x) & (vox_x < self._t_x1)
               & (self._t_y0 <= vox_y) & (vox_y < self._t_y1))
        idx = np.flatnonzero(hit)
        return self._tiles[int(idx[0])] if idx.size else None

    def channel_idx_for_name(self, channel_name: str) -> Optional[int]:
        """Map a display name to its manifest channel_idx.

        manifest channel_idx (the acquisition index) is NOT a dense 0..N-1
        counter and need not equal the UI's channel id.
        """
        for c in self.manifest_channels:
            if c.get("name") == channel_name:
                return int(c["index"])
        for t in self._tiles:
            if t.channel_name == channel_name:
                return t.channel_idx
        return None

    # ── viewport selection ─────────────────────────────────

    def visible_tiles(
        self,
        roi_vox: Optional[Tuple[int, int, int, int]],
        channels: Optional[Sequence[int]] = None,
        face_budget: Optional[int] = None,
    ) -> List[MeshTileInfo]:
        """Tiles intersecting `roi_vox` = (x0, x1, y0, y1) for enabled channels.

        Sorted by distance from the ROI centre and cut off at the triangle
        Every intersecting tile is returned, ordered nearest-first so the
        streamer fills in from the middle of the view outwards.
        """
        self.last_truncated = False
        if not self._tiles:
            return []
        chans = list(self._enabled) if channels is None else list(channels)
        if not chans:
            return []

        keep = np.isin(self._t_chan, np.asarray(chans, dtype=np.int32))
        if roi_vox is not None:
            x0, x1, y0, y1 = roi_vox
            keep &= (self._t_x0 < x1) & (self._t_x1 > x0)
            keep &= (self._t_y0 < y1) & (self._t_y1 > y0)
            cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        else:
            cx = float((self._t_x0 + self._t_x1).mean()) * 0.5
            cy = float((self._t_y0 + self._t_y1).mean()) * 0.5

        idx = np.flatnonzero(keep)
        if idx.size == 0:
            return []
        mx = (self._t_x0[idx] + self._t_x1[idx]) * 0.5 - cx
        my = (self._t_y0[idx] + self._t_y1[idx]) * 0.5 - cy
        idx = idx[np.argsort(mx * mx + my * my)]

        # EVERY tile intersecting the viewport, not a budgeted subset. The
        # budget used to cut this list at 3M faces, which silently hid most of
        # the surfaces whenever the view was wide: one channel at full slide
        # showed 80 of 153 tiles, three channels showed 94 of 471. Tiles are
        # still ordered nearest-first so streaming fills in from the centre of
        # what the user is looking at.
        #
        # `hard_face_ceiling` remains only as an out-of-memory guard for the
        # pathological case (all 49 channels at full slide is 157M faces). When
        # it bites it is recorded on `last_truncated` and logged, never silent.
        ceiling = int(face_budget if face_budget is not None
                      else self.hard_face_ceiling)
        total = np.cumsum(self._t_faces[idx])
        if total.size and total[-1] > ceiling:
            n = max(1, int(np.searchsorted(total, ceiling, side="right")))
            self.last_truncated = True
            print(f"[meshes] {idx.size} tiles in view need {int(total[-1]):,} "
                  f"faces, over the {ceiling:,} ceiling — showing {n}")
            idx = idx[:n]
        return [self._tiles[int(i)] for i in idx]

    # ── opt-in ─────────────────────────────────────────────

    def enable_channel(self, channel_idx: int,
                       color_rgb: Tuple[float, float, float] = (1.0, 1.0, 1.0)):
        """Opt this channel's surfaces in. Tiles arrive on the next refresh."""
        self._enabled[int(channel_idx)] = tuple(color_rgb)
        self._channel_colors[int(channel_idx)] = tuple(color_rgb)

    def disable_channel(self, channel_idx: int):
        """Opt out and drop this channel's actors immediately."""
        self._enabled.pop(int(channel_idx), None)
        self._drop(lambda key: key[0] == int(channel_idx))
        self._polydata_cache.pop(int(channel_idx), None)

    def is_enabled(self, channel_idx: int) -> bool:
        return int(channel_idx) in self._enabled

    def get_active_channels(self) -> set:
        return set(self._enabled)

    # ── loading (worker-thread safe) ───────────────────────

    def tile_key(self, tile: MeshTileInfo) -> tuple:
        return (tile.channel_idx, tile.tile_y, tile.tile_x)

    def is_loaded(self, tile: MeshTileInfo) -> bool:
        with self._lock:
            return self.tile_key(tile) in self._actors

    def load_tile_polydata(self, tile: MeshTileInfo):
        """Read a GLB and return world-space vtkPolyData. Pure data work.

        Generates normals: the GLBs carry POSITION and indices only, so without
        this the Phong material has nothing to shade with and surfaces read as
        faceted.
        """
        if self.mesh_dir is None:
            return None
        path = self.mesh_dir / tile.file
        if not path.exists():
            print(f"[meshes] GLB not found: {path}")
            return None

        reader = vtkGLTFReader()
        reader.SetFileName(str(path))
        reader.Update()
        geom = vtkCompositeDataGeometryFilter()
        geom.SetInputConnection(reader.GetOutputPort())
        geom.Update()
        pd = geom.GetOutput()
        if pd is None or pd.GetNumberOfPoints() == 0:
            return None

        world = self._transform_to_world(
            pd, tile.world_offset_x, tile.world_offset_y, tile.world_offset_z)

        normals = vtkPolyDataNormals()
        normals.SetInputData(world)
        normals.SplittingOff()          # keep the tile's own vertex sharing
        normals.ConsistencyOff()        # the pipeline already emits consistent winding
        normals.AutoOrientNormalsOff()  # not closed surfaces — "outside" is undefined
        normals.ComputePointNormalsOn()
        normals.ComputeCellNormalsOff()
        normals.Update()
        return normals.GetOutput()

    def _transform_to_world(self, polydata, offset_x: int, offset_y: int, offset_z: int):
        from vtkmodules.util.numpy_support import vtk_to_numpy, numpy_to_vtk
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkPolyData

        coords = vtk_to_numpy(polydata.GetPoints().GetData()).copy().astype(np.float64)
        coords[:, 0] = (coords[:, 0] + offset_x) * self.base_sx
        coords[:, 1] = (coords[:, 1] + offset_y) * self.base_sy
        coords[:, 2] = (coords[:, 2] + offset_z) * self.base_sz

        pts = vtkPoints()
        pts.SetData(numpy_to_vtk(coords, deep=True))
        out = vtkPolyData()
        out.DeepCopy(polydata)
        out.SetPoints(pts)
        return out

    # ── applying (main thread only) ────────────────────────

    def apply_loaded(self, tile: MeshTileInfo, polydata) -> bool:
        """Add a loaded tile to the scene. Main thread — touches the renderer."""
        if polydata is None or not self.is_enabled(tile.channel_idx):
            return False
        key = self.tile_key(tile)
        if key in self._actors:
            return False
        color = self._enabled.get(tile.channel_idx, (1.0, 1.0, 1.0))
        actor = self._make_actor(polydata, color)
        self.renderer.AddActor(actor)
        with self._lock:
            self._actors[key] = actor
            self._actor_faces[key] = int(tile.face_count)
            self._live_faces += int(tile.face_count)
        # Mirror into Optimal View as the tile arrives. Master's version only
        # had to mirror whole-channel activations; tiles stream now, so the
        # mirroring hooks the streaming lifecycle instead.
        self._add_nov_actor(key, polydata, color)
        self._polydata_cache.pop(tile.channel_idx, None)
        return True

    def evict_outside(self, keep: set) -> int:
        """Drop every live tile that is NOT in `keep` (the current viewport).

        This is what "remove them as they move out" means, and the LRU below
        never did it: it only fired once the live set exceeded a face budget,
        so tiles behind the camera stayed resident indefinitely and the budget
        they occupied was denied to tiles actually in view.
        """
        gone = 0
        for key in list(self._actors):
            if key not in keep:
                self._remove_key(key)
                gone += 1
        return gone

    def evict_to_budget(self, keep: Optional[set] = None):
        """Backstop only: drop least-recently-used tiles if the live set is
        still over budget after `evict_outside` has run."""
        keep = keep or set()
        while self._live_faces > self.LRU_FACE_BUDGET and self._actors:
            for key in list(self._actors):
                if key not in keep:
                    self._remove_key(key)
                    break
            else:
                return

    def touch(self, tile: MeshTileInfo):
        """Mark a tile as recently used, so eviction passes over it."""
        key = self.tile_key(tile)
        with self._lock:
            if key in self._actors:
                self._actors.move_to_end(key)

    def _make_actor(self, polydata, color: Tuple[float, float, float],
                    opacity: float = 1.0):
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(polydata)
        # vtkLODActor, not vtkActor: a full viewport can carry millions of
        # triangles, and VTK raises the desired update rate while the camera is
        # being dragged. The LOD actor answers that by falling back to a
        # decimated or point representation for drag frames and returning to
        # full geometry once the view settles — which is the difference between
        # a smooth drag and a slideshow. Still frames are unaffected.
        actor = vtkLODActor()
        actor.SetMapper(mapper)
        actor.SetNumberOfCloudPoints(int(self.LOD_CLOUD_POINTS))
        prop = actor.GetProperty()
        prop.SetColor(*color)
        prop.SetOpacity(opacity)
        prop.SetInterpolationToPhong()
        prop.SetAmbient(0.4)
        prop.SetDiffuse(0.8)
        prop.SetSpecular(0.0)
        prop.SetSpecularPower(1.0)
        actor.SetPickable(False)
        return actor

    def _remove_key(self, key):
        actor = self._actors.pop(key, None)
        if actor is not None:
            self.renderer.RemoveActor(actor)
            self._live_faces -= self._actor_faces.pop(key, 0)
        self._remove_nov_key(key)

    def _drop(self, predicate):
        for key in [k for k in self._actors if predicate(k)]:
            self._remove_key(key)

    # ── colours / spacing / teardown ───────────────────────

    def update_channel_color(self, channel_idx: int, color_rgb: Tuple[float, float, float]):
        ci = int(channel_idx)
        self._channel_colors[ci] = tuple(color_rgb)
        if ci in self._enabled:
            self._enabled[ci] = tuple(color_rgb)
        for key, actor in self._actors.items():
            if key[0] == ci:
                actor.GetProperty().SetColor(*color_rgb)
        for key, actor in self._nov_actors.items():
            if key[0] == ci:
                actor.GetProperty().SetColor(*color_rgb)

    def update_spacing(self, sx: float, sy: float, sz: float):
        """Update world spacing. Ignored once the manifest has supplied its own,
        which is the value the vertices were actually written against."""
        if self._manifest and self._manifest.get("voxel_size_um"):
            return
        self.base_sx, self.base_sy, self.base_sz = sx, sy, sz
        print(f"[meshes] Spacing updated: ({sx}, {sy}, {sz})")

    def clear(self):
        for key in list(self._actors):
            self._remove_key(key)
        self._actors.clear()
        self._actor_faces.clear()
        self._live_faces = 0
        self._enabled.clear()
        self._channel_colors.clear()
        self._polydata_cache.clear()
        self.clear_nov_meshes()

    # ── Optimal View (NOV) mirroring ───────────────────────

    def set_nov_renderer(self, nov_renderer: Optional[vtkRenderer]) -> None:
        """Point the mirror at a renderer, dropping any actors held in the old
        one first — they belong to a renderer that is going away."""
        if self.nov_renderer is not None and nov_renderer is not self.nov_renderer:
            self.clear_nov_meshes()
        self.nov_renderer = nov_renderer

    def _nov_shows(self, channel_idx: int) -> bool:
        return self._nov_visible is None or int(channel_idx) in self._nov_visible

    def _add_nov_actor(self, key, polydata, color) -> None:
        """Twin one main-scene tile actor into the NOV renderer."""
        if self.nov_renderer is None or polydata is None:
            return
        if key in self._nov_actors:
            return
        actor = self._make_actor(polydata, tuple(color))
        actor.SetVisibility(1 if self._nov_shows(key[0]) else 0)
        self.nov_renderer.AddActor(actor)
        self._nov_actors[key] = actor

    def _remove_nov_key(self, key) -> None:
        actor = self._nov_actors.pop(key, None)
        if actor is not None and self.nov_renderer is not None:
            self.nov_renderer.RemoveActor(actor)

    def sync_nov_meshes(self, visible_channel_ids: Optional[List[int]] = None) -> None:
        """Rebuild the mirror from whatever is live in the main scene.

        Called when the popup opens, so it has to work from the actors rather
        than from a tile list: the polydata comes back off each mapper.
        """
        if self.nov_renderer is None:
            return
        if visible_channel_ids is not None:
            self._nov_visible = {int(c) for c in visible_channel_ids}
        self.clear_nov_meshes()
        for key, actor in list(self._actors.items()):
            mapper = actor.GetMapper()
            polydata = mapper.GetInput() if mapper is not None else None
            self._add_nov_actor(key, polydata, actor.GetProperty().GetColor())
        print(f"[meshes] Synced {len(self._nov_actors)} mesh actor(s) to NOV")

    def clear_nov_meshes(self) -> None:
        for key in list(self._nov_actors):
            self._remove_nov_key(key)
        self._nov_actors.clear()

    def set_nov_mesh_visibility(self, selected_channel_ids: List[int]) -> None:
        """Show/hide mirrored actors to match the Optimal View channel picks."""
        self._nov_visible = {int(c) for c in (selected_channel_ids or [])}
        # The popup may have just opened, before anything was mirrored.
        if self.nov_renderer is not None and self._actors and not self._nov_actors:
            self.sync_nov_meshes()
            return
        for key, actor in self._nov_actors.items():
            actor.SetVisibility(1 if self._nov_shows(key[0]) else 0)

    # ── geometry for the label layer ───────────────────────

    def get_channel_actor(self, channel_idx: int):
        for key, actor in self._actors.items():
            if key[0] == int(channel_idx):
                return actor
        return None

    def get_channel_polydata(self, channel_idx: int):
        """One vtkPolyData over the channel's currently loaded tiles.

        Cached and invalidated whenever the tile set changes, so the label
        layer can key its (expensive) re-placement on the geometry actually
        changing rather than on every interaction ending.
        """
        ci = int(channel_idx)
        cached = self._polydata_cache.get(ci)
        if cached is not None:
            return cached
        from vtkmodules.vtkFiltersCore import vtkAppendPolyData
        parts = [a.GetMapper().GetInput() for k, a in self._actors.items() if k[0] == ci]
        parts = [p for p in parts if p is not None and p.GetNumberOfPoints() > 0]
        if not parts:
            return None
        if len(parts) == 1:
            self._polydata_cache[ci] = parts[0]
            return parts[0]
        append = vtkAppendPolyData()
        for p in parts:
            append.AddInputData(p)
        append.Update()
        self._polydata_cache[ci] = append.GetOutput()
        return self._polydata_cache[ci]

    def get_tile_world_center(self, tile: MeshTileInfo) -> Tuple[float, float, float]:
        return (
            (tile.world_offset_x + tile.tile_width / 2.0) * self.base_sx,
            (tile.world_offset_y + tile.tile_height / 2.0) * self.base_sy,
            (tile.tile_depth / 2.0) * self.base_sz,
        )

    # ── back-compat shims ──────────────────────────────────

    def activate_channel_mesh(self, channel_idx: int,
                              color_rgb: Tuple[float, float, float] = (1.0, 1.0, 1.0),
                              tile_x: Optional[int] = None,
                              tile_y: Optional[int] = None,
                              opacity: float = 1.0):
        """Enable a channel, optionally loading one specific tile immediately.

        The tile arguments exist for the right-click drill-down, which wants a
        single named tile now rather than whatever the viewport covers.
        """
        if not self.is_available:
            return
        self.enable_channel(channel_idx, color_rgb)
        if tile_x is None or tile_y is None:
            return
        tile = self.get_tile(channel_idx, tile_x, tile_y)
        if tile is None:
            print(f"[meshes] No tile ch{channel_idx}_{tile_y}_{tile_x} in manifest")
            return
        self.apply_loaded(tile, self.load_tile_polydata(tile))

    def deactivate_channel_mesh(self, channel_idx: int):
        self.disable_channel(channel_idx)


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
