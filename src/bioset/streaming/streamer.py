from __future__ import annotations

import threading
import time
import queue
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Callable
import numpy as np

import dask.array as da

from vtkmodules.vtkCommonDataModel import vtkImageData
from vtkmodules.util.numpy_support import numpy_to_vtk

from .lod import ROI, camera_distance_to_focal, choose_component, compute_visible_xy_roi_vox
from .zarr_source import ZarrMultiscaleSource
from ..scene.volumes import SpacingConfig, color_name_to_rgb, build_histogram_tf, build_tf_with_range

from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction
from vtkmodules.vtkRenderingCore import vtkColorTransferFunction, vtkVolume, vtkVolumeProperty
from vtkmodules.vtkRenderingVolume import vtkGPUVolumeRayCastMapper


@dataclass
class ChannelState:
    component: int
    roi: ROI


@dataclass
class LoadRequest:
    """Represents a pending load operation"""
    component: int
    roi: ROI
    timestamp: float


@dataclass
class LoadedData:
    """Data loaded in background thread, ready for VTK update on main thread"""
    component: int
    roi: ROI
    channel_arrays: Dict[int, np.ndarray]
    timestamp: float


class VolumeStreamer:
    DEBOUNCE_DELAY = 0.15

    _executor: ThreadPoolExecutor = None

    def __init__(self, *, cfg, renderer, render_window):
        self.cfg = cfg
        self.renderer = renderer
        self.render_window = render_window
        self.render_callback: Optional[Callable] = None

        if VolumeStreamer._executor is None:
            VolumeStreamer._executor = ThreadPoolExecutor(max_workers=2)

        max_bytes = int(cfg.cache_size_gb * (1024**3))
        self.zsrc = ZarrMultiscaleSource(
            url=cfg.zarr_url,
            cache_enabled=cfg.cache_enabled,
            cache_dir=cfg.cache_dir,
            cache_size_bytes=max_bytes,
        )

        self.volumes: Dict[int, vtkVolume] = {}
        self.mappers: Dict[int, vtkGPUVolumeRayCastMapper] = {}
        self.state: Dict[int, ChannelState] = {}

        self._channel_tfs: Dict[int,
                                Tuple[vtkColorTransferFunction, vtkPiecewiseFunction]] = {}

        self._pending_request: Optional[LoadRequest] = None
        self._debounce_lock = threading.Lock()
        self._loading_lock = threading.Lock()
        self._is_loading = False

        self._loaded_data_queue: queue.Queue[LoadedData] = queue.Queue()

        self._array_cache: Dict[Tuple[int, int, ROI], np.ndarray] = {}
        self._cache_max_entries = 32

        self._last_component: Optional[int] = None
        self._initial_camera: Optional[dict] = None  # position, focalPoint, viewUp after first load

        self._active_channels: set[int] = set() 
        self._channel_colors: Dict[int, Tuple[float, float, float]] = {} 
        
        self._channel_data_range: Dict[int, Tuple[float, float]] = {}

        # NOV box clip: when set, only voxels inside the box are shown in the NOV popup view (main view stays full)
        self._nov_box_clip: Optional[Tuple[Tuple[float, float, float], float, float, float]] = None
        # NOV scoring: cached 3D arrays for entropy+occlusion scoring (prepare_nov_scoring / sample_nov_ray_channels)
        self._nov_scoring_cache: Optional[dict] = None
        self.nov_renderer = None
        self.nov_render_window = None
        self.nov_volumes: Dict[int, vtkVolume] = {}
        self.nov_mappers: Dict[int, vtkGPUVolumeRayCastMapper] = {}
        # Progressive NOV: queue of (component, {ch_id: (np_arr, roi)}) loaded in background, applied on main thread
        self._nov_progressive_queue: queue.Queue = queue.Queue()

        if self.cfg.channels:
            self._init_low_res_full()
        else:
            print("[stream] No channels configured - waiting for dynamic load")

    def set_render_callback(self, fn: Callable):
        self.render_callback = fn

    def set_zarr_url(self, url: str):
        """Update the zarr URL and reinitialize the source."""
        print(f"[stream] Setting zarr URL: {url}")
        for vol in self.volumes.values():
            self.renderer.RemoveVolume(vol)
        max_bytes = int(self.cfg.cache_size_gb * (1024**3))
        self.zsrc = ZarrMultiscaleSource(
            url=url,
            cache_enabled=self.cfg.cache_enabled,
            cache_dir=self.cfg.cache_dir,
            cache_size_bytes=max_bytes,
        )
        self._array_cache.clear()
        self._active_channels.clear()
        self._channel_colors.clear()
        self._channel_tfs.clear()
        self.volumes.clear()
        self.mappers.clear()
        self.state.clear()
        self._initial_camera = None

    def set_spacing(self, sx: float, sy: float, sz: float):
        """Update the base spacing."""
        self.cfg = self.cfg.__class__(**{**self.cfg.__dict__,
                                        "base_sx": sx,
                                        "base_sy": sy,
                                        "base_sz": sz})
        print(f"[stream] Updated spacing: ({sx}, {sy}, {sz})")

    def _hex_to_rgb(self, color_hex: str) -> Tuple[float, float, float]:
        """Convert hex color to RGB tuple (0-1 range)."""
        color_hex = color_hex.lstrip('#')
        r = int(color_hex[0:2], 16) / 255.0
        g = int(color_hex[2:4], 16) / 255.0
        b = int(color_hex[4:6], 16) / 255.0
        return (r, g, b)

    # def activate_channel(self, channel_id: int, color_hex: str):
    #     """
    #     Activate a channel for rendering.
    #     Always loads at start_component first (fast), then LOD system upgrades.
    #     """
    #     if channel_id in self._active_channels:
    #         print(f"[stream] Channel {channel_id} already active")
    #         return
        
    #     print(f"[stream] Activating channel {channel_id} with color {color_hex}")
        
    #     self._channel_colors[channel_id] = self._hex_to_rgb(color_hex)
    #     is_first_volume = len(self._active_channels) == 0 
    #     self._active_channels.add(channel_id)
        
    #     comp = self.cfg.start_component
        
    #     try:
    #         z, y, x = self._dims_for_component(comp)
    #     except Exception as e:
    #         print(f"[stream] Error getting dims: {e}")
    #         self._active_channels.discard(channel_id)  
    #         return
        
    #     roi = ROI(0, x, 0, y)
        
    #     print(f"[stream] Initial load at low-res comp={comp} (dims: {x}x{y}x{z})")
        
    #     self._load_and_display_channel(channel_id, comp, roi, reset_camera=is_first_volume)
        
    #     if self._last_component is not None and self._last_component < comp:
    #         print(f"[stream] Scheduling upgrade from comp={comp} to current view")
    #         self._trigger_lod_update_for_new_channel()

    def _trigger_lod_update_for_new_channel(self):
        """Trigger an async LOD update after adding a new channel."""
        if not self._active_channels:
            return
        
        cam = self.renderer.GetActiveCamera()
        dist = camera_distance_to_focal(cam)
        
        desired_comp = choose_component(
            dist,
            self.cfg.distance_rules,
            min_component=self.cfg.min_component,
            max_component=self.cfg.max_component,
        )
        
        spacing = self._spacing_for_component(desired_comp)
        zdim, ydim, xdim = self._dims_for_component(desired_comp)
        bounds = self._volume_bounds_world(desired_comp)
        
        roi = compute_visible_xy_roi_vox(
            self.renderer,
            bounds_world=bounds,
            sx=spacing.sx,
            sy=spacing.sy,
            x_dim=xdim,
            y_dim=ydim,
            margin_vox=self.cfg.roi_margin_vox,
        )
        
        print(f"[stream] Scheduling LOD update: comp={desired_comp} roi={roi}")
        
        request = LoadRequest(
            component=desired_comp,
            roi=roi,
            timestamp=time.time(),
        )
        
        self._schedule_load(request)

    def activate_channel(self, channel_id: int, color_hex: str):
        """
        Activate a channel for rendering.
        Loads at the appropriate resolution based on current camera zoom.
        """
        if channel_id in self._active_channels:
            print(f"[stream] Channel {channel_id} already active")
            return
        
        print(f"[stream] Activating channel {channel_id} with color {color_hex}")
        
        self._channel_colors[channel_id] = self._hex_to_rgb(color_hex)
        is_first_volume = len(self._active_channels) == 0 
        self._active_channels.add(channel_id)
        
        if is_first_volume or self._last_component is None:
            comp = self.cfg.start_component
            try:
                z, y, x = self._dims_for_component(comp)
            except Exception as e:
                print(f"[stream] Error getting dims: {e}")
                self._active_channels.discard(channel_id)  
                return
            roi = ROI(0, x, 0, y)
            print(f"[stream] First volume - loading at start_component={comp}")
        else:
            cam = self.renderer.GetActiveCamera()
            dist = camera_distance_to_focal(cam)
            
            comp = choose_component(
                dist,
                self.cfg.distance_rules,
                min_component=self.cfg.min_component,
                max_component=self.cfg.max_component,
            )
            
            existing_state = None
            for ch_id in self._active_channels:
                if ch_id != channel_id and ch_id in self.state:
                    existing_state = self.state[ch_id]
                    break
            
            if existing_state and existing_state.component == comp:
                roi = existing_state.roi
                print(f"[stream] Matching existing view: comp={comp} roi={roi}")
            else:
                spacing = self._spacing_for_component(comp)
                zdim, ydim, xdim = self._dims_for_component(comp)
                bounds = self._volume_bounds_world(comp)
                
                roi = compute_visible_xy_roi_vox(
                    self.renderer,
                    bounds_world=bounds,
                    sx=spacing.sx,
                    sy=spacing.sy,
                    x_dim=xdim,
                    y_dim=ydim,
                    margin_vox=self.cfg.roi_margin_vox,
                )
                print(f"[stream] Computed view: comp={comp} roi={roi}")
        
        self._load_and_display_channel(channel_id, comp, roi, reset_camera=is_first_volume)
        
    def deactivate_channel(self, channel_id: int):
        """
        Deactivate a channel - remove from rendering.
        """
        if channel_id not in self._active_channels:
            print(f"[stream] Channel {channel_id} not active")
            return
        
        print(f"[stream] Deactivating channel {channel_id}")
        
        self._active_channels.discard(channel_id)
        
        if channel_id in self.volumes:
            vol = self.volumes[channel_id]
            self.renderer.RemoveVolume(vol)
            del self.volumes[channel_id]
        
        if channel_id in self.mappers:
            del self.mappers[channel_id]
        if channel_id in self.nov_volumes and self.nov_renderer:
            self.nov_renderer.RemoveVolume(self.nov_volumes[channel_id])
        if channel_id in self.nov_volumes:
            del self.nov_volumes[channel_id]
        if channel_id in self.nov_mappers:
            del self.nov_mappers[channel_id]
        
        if channel_id in self.state:
            del self.state[channel_id]
        
        if channel_id in self._channel_tfs:
            del self._channel_tfs[channel_id]
        
        self._render()

    def _load_and_display_channel(self, channel_id: int, component: int, roi: ROI, reset_camera: bool = False):
        """Load a single channel and add to display."""
        spacing = self._spacing_for_component(component)
        
        print(f"[stream] Loading channel {channel_id} at comp={component} roi={roi}")
        
        try:
            np_arr = self._load_channel_data(component, channel_id, roi)
        except Exception as e:
            print(f"[stream] Error loading channel {channel_id}: {e}")
            return

        if np_arr.size == 0 or any(s <= 0 for s in np_arr.shape):
            print(
                f"[stream] Skipping channel {channel_id}: loaded shape {np_arr.shape} has zero size. "
                "ROI may be out of bounds for this LOD."
            )
            return

        try:
            origin_xyz = (roi.x0 * spacing.sx, roi.y0 * spacing.sy, 0.0)
            img = self._create_vtk_image(np_arr, spacing, origin_xyz)
        except ValueError as e:
            print(f"[stream] Skipping channel {channel_id}: {e}")
            return
        
        r0, r1 = img.GetScalarRange()
        self._channel_data_range[channel_id] = (r0, r1)
        
        vol, mapper = self._get_or_create_volume(channel_id)
        mapper.SetInputData(img)
        
        tint_rgb = self._channel_colors.get(channel_id, (1.0, 1.0, 1.0))
        color_tf, opacity_tf = build_histogram_tf(img, tint_rgb=tint_rgb)
        self._channel_tfs[channel_id] = (color_tf, opacity_tf)
        
        prop = vol.GetProperty()
        prop.SetColor(color_tf)
        prop.SetScalarOpacity(opacity_tf)
        # prop.SetScalarOpacityUnitDistance(
        #     max(1e-6, 1.0 * min(spacing.sx, spacing.sy, spacing.sz))
        # )
        
        if not self.renderer.HasViewProp(vol):
            self.renderer.AddVolume(vol)
        
        self.state[channel_id] = ChannelState(component=component, roi=roi)
        self._last_component = component
        
        if reset_camera:
            print(f"[stream] Resetting camera for first volume")
            self.renderer.ResetCamera()
            cam = self.renderer.GetActiveCamera()
            self._initial_camera = {
                "position": list(cam.GetPosition()),
                "focalPoint": list(cam.GetFocalPoint()),
                "viewUp": list(cam.GetViewUp()),
            }
        
        self.renderer.ResetCameraClippingRange()
        self._render()
        
        print(f"[stream] Channel {channel_id} displayed")

    def reset_camera_to_initial(self) -> None:
        """Restore camera to initial position (saved when data was first loaded). If none saved, call ResetCamera()."""
        if not self.renderer:
            return
        cam = self.renderer.GetActiveCamera()
        if self._initial_camera:
            pos = self._initial_camera.get("position")
            fp = self._initial_camera.get("focalPoint")
            vup = self._initial_camera.get("viewUp")
            if pos and len(pos) >= 3:
                cam.SetPosition(pos[0], pos[1], pos[2])
            if fp and len(fp) >= 3:
                cam.SetFocalPoint(fp[0], fp[1], fp[2])
            if vup and len(vup) >= 3:
                cam.SetViewUp(vup[0], vup[1], vup[2])
            cam.Modified()
        else:
            self.renderer.ResetCamera()
        self.renderer.ResetCameraClippingRange()
        self._render()

    def load_channel_at_lod(
        self,
        channel_id: int,
        color_hex: str,
        component: int,
        roi_dict: dict,
        reset_camera: bool = False,
    ) -> None:
        """Load one channel at exact LOD (component and roi). Used when restoring a bookmark snapshot."""
        roi = ROI(
            x0=int(roi_dict.get("x0", 0)),
            x1=int(roi_dict.get("x1", 1)),
            y0=int(roi_dict.get("y0", 0)),
            y1=int(roi_dict.get("y1", 1)),
        )
        self._channel_colors[channel_id] = self._hex_to_rgb(color_hex)
        self._active_channels.add(channel_id)
        self._load_and_display_channel(channel_id, component, roi, reset_camera=reset_camera)

    def get_active_channels(self) -> set[int]:
        """Return the set of currently active channel IDs."""
        return self._active_channels.copy()

    def set_nov_renderer(self, renderer, render_window) -> None:
        """Set the optional NOV popup renderer/window. When set, clipped volumes are pushed here."""
        self.nov_renderer = renderer
        self.nov_render_window = render_window

    def set_nov_box_clip(self, center: Tuple[float, float, float], length: float, width: float, depth: float) -> None:
        """Clip NOV popup view to inside box. Main view stays full. Call sync_nov_volumes() after to update popup."""
        self._nov_box_clip = (
            (float(center[0]), float(center[1]), float(center[2])),
            float(length), float(width), float(depth),
        )

    def clear_nov_box_clip(self) -> None:
        """Remove NOV box clip. Call clear_nov_view() to remove popup volumes."""
        self._nov_box_clip = None

    def clear_nov_view(self) -> None:
        """Remove all volumes from NOV popup renderer and clear NOV volume caches."""
        if self.nov_renderer is None:
            return
        # Drain progressive queue so no stale updates apply after popup is closed
        try:
            while True:
                self._nov_progressive_queue.get_nowait()
        except queue.Empty:
            pass
        for ch, vol in list(self.nov_volumes.items()):
            if self.nov_renderer.HasViewProp(vol):
                self.nov_renderer.RemoveVolume(vol)
        self.nov_volumes.clear()
        self.nov_mappers.clear()
        if self.nov_render_window:
            self.nov_render_window.Render()
        if self.render_callback is not None:
            self.render_callback()

    def _nov_box_roi_at_component(self, component: int) -> Optional[ROI]:
        """Voxel ROI (x0, x1, y0, y1) that contains the NOV box in world space at the given component. Adds 2-voxel margin."""
        clip = self._nov_box_clip
        if not clip:
            return None
        (cx, cy, cz), length, width, depth = clip
        hL, hW, hD = length / 2.0, width / 2.0, depth / 2.0
        xmin_w = cx - hL
        xmax_w = cx + hL
        ymin_w = cy - hW
        ymax_w = cy + hW
        spacing = self._spacing_for_component(component)
        sx, sy = spacing.sx, spacing.sy
        z_dim, y_dim, x_dim = self._dims_for_component(component)
        margin = 2
        x0 = max(0, int(np.floor(xmin_w / sx)) - margin)
        x1 = min(x_dim, int(np.ceil(xmax_w / sx)) + margin)
        y0 = max(0, int(np.floor(ymin_w / sy)) - margin)
        y1 = min(y_dim, int(np.ceil(ymax_w / sy)) + margin)
        if x1 <= x0 or y1 <= y0:
            return None
        return ROI(x0, x1, y0, y1)

    def sync_nov_volumes_at_component(self, component: int) -> None:
        """Update NOV popup volumes with clipped data at exactly the given component (for one resolution level)."""
        if not self.nov_renderer or not self._nov_box_clip or not self._active_channels:
            return
        try:
            for ch in list(self._active_channels):
                roi_nov = self._nov_box_roi_at_component(component)
                if roi_nov is None:
                    continue
                try:
                    np_arr = self._load_channel_data(component, ch, roi_nov)
                except Exception:
                    continue
                if np_arr.size == 0 or any(s <= 0 for s in np_arr.shape):
                    continue
                spacing = self._spacing_for_component(component)
                origin_xyz = (roi_nov.x0 * spacing.sx, roi_nov.y0 * spacing.sy, 0.0)
                try:
                    img = self._create_vtk_image(np_arr, spacing, origin_xyz, for_nov_view=True)
                except ValueError:
                    continue
                try:
                    vol, mapper = self._get_or_create_nov_volume(ch)
                    mapper.SetInputData(img)
                    mapper.Modified()
                    if ch in self._channel_tfs:
                        color_tf, opacity_tf = self._channel_tfs[ch]
                        prop = vol.GetProperty()
                        prop.SetColor(color_tf)
                        prop.SetScalarOpacity(opacity_tf)
                        prop.SetScalarOpacityUnitDistance(
                            max(1e-6, 1.0 * min(spacing.sx, spacing.sy, spacing.sz)))
                    if not self.nov_renderer.HasViewProp(vol):
                        self.nov_renderer.AddVolume(vol)
                except Exception:
                    continue
            self.nov_renderer.ResetCameraClippingRange()
            if self.nov_render_window:
                self.nov_render_window.Render()
            if self.render_callback is not None:
                self.render_callback()
        except Exception as e:
            import traceback
            print(f"[nov] sync_nov_volumes_at_component error: {e}")
            traceback.print_exc()

    def apply_main_channel_to_nov(self, channel_id: int) -> None:
        """Apply main-scene channel color and transfer function (filter) to the NOV popup volume for this channel."""
        if self.nov_renderer is None:
            return
        if channel_id not in self.nov_volumes or channel_id not in self._channel_tfs:
            return
        try:
            vol = self.nov_volumes[channel_id]
            color_tf, opacity_tf = self._channel_tfs[channel_id]
            prop = vol.GetProperty()
            prop.SetColor(color_tf)
            prop.SetScalarOpacity(opacity_tf)
            if channel_id in self.nov_mappers and self.nov_mappers[channel_id].GetInput():
                spacing = self.nov_mappers[channel_id].GetInput().GetSpacing()
                prop.SetScalarOpacityUnitDistance(
                    max(1e-6, 1.0 * min(spacing[0], spacing[1], spacing[2])))
            if self.nov_render_window:
                self.nov_render_window.Render()
            if self.render_callback:
                self.render_callback()
        except Exception as e:
            import traceback
            print(f"[nov] apply_main_channel_to_nov error: {e}")
            traceback.print_exc()

    def set_nov_channel_visibility(self, selected_channel_ids: List[int]) -> None:
        """Show only NOV volumes whose channel id is in selected_channel_ids; hide others. Does not remove volumes."""
        if not self.nov_renderer:
            return
        sel = set(selected_channel_ids) if selected_channel_ids else set()
        for ch, vol in self.nov_volumes.items():
            vol.SetVisibility(1 if ch in sel else 0)
        if self.nov_render_window:
            self.nov_render_window.Render()
        if self.render_callback is not None:
            self.render_callback()

    def sync_nov_volumes(self) -> None:
        """Update NOV popup: show first frame at coarsest level for speed, then progressively load comp-1 down to min_component."""
        if not self.nov_renderer or not self._nov_box_clip or not self._active_channels:
            return
        try:
            start_comp = self.cfg.max_component
            self.sync_nov_volumes_at_component(start_comp)
            VolumeStreamer._executor.submit(self._run_nov_progressive_load)
        except Exception as e:
            import traceback
            print(f"[nov] sync_nov_volumes error: {e}")
            traceback.print_exc()

    def _run_nov_progressive_load(self) -> None:
        """Background thread: load NOV box at each component from coarse to fine and put in queue for main thread."""
        if not self._nov_box_clip or not self._active_channels:
            return
        try:
            for comp in range(self.cfg.max_component, self.cfg.min_component - 1, -1):
                roi_nov = self._nov_box_roi_at_component(comp)
                if roi_nov is None:
                    continue
                channel_arrays: Dict[int, Tuple[np.ndarray, ROI]] = {}
                for ch in list(self._active_channels):
                    try:
                        np_arr = self._load_channel_data(comp, ch, roi_nov)
                        if np_arr.size > 0 and all(s > 0 for s in np_arr.shape):
                            channel_arrays[ch] = (np_arr, roi_nov)
                    except Exception:
                        continue
                if channel_arrays:
                    self._nov_progressive_queue.put((comp, channel_arrays))
        except Exception as e:
            import traceback
            print(f"[nov] progressive load error: {e}")
            traceback.print_exc()

    def process_nov_progressive_queue(self) -> bool:
        """Main thread: apply one resolution level from the queue (coarse→fine). Returns True if view was updated."""
        try:
            item = self._nov_progressive_queue.get_nowait()
        except queue.Empty:
            return False
        if not self.nov_renderer or not self._nov_box_clip:
            return False
        try:
            comp, channel_arrays = item
            for ch, (np_arr, roi_nov) in channel_arrays.items():
                try:
                    spacing = self._spacing_for_component(comp)
                    origin_xyz = (roi_nov.x0 * spacing.sx, roi_nov.y0 * spacing.sy, 0.0)
                    img = self._create_vtk_image(np_arr, spacing, origin_xyz, for_nov_view=True)
                    vol, mapper = self._get_or_create_nov_volume(ch)
                    mapper.SetInputData(img)
                    mapper.Modified()
                    if ch in self._channel_tfs:
                        color_tf, opacity_tf = self._channel_tfs[ch]
                        prop = vol.GetProperty()
                        prop.SetColor(color_tf)
                        prop.SetScalarOpacity(opacity_tf)
                        prop.SetScalarOpacityUnitDistance(
                            max(1e-6, 1.0 * min(spacing.sx, spacing.sy, spacing.sz)))
                    if not self.nov_renderer.HasViewProp(vol):
                        self.nov_renderer.AddVolume(vol)
                except Exception:
                    continue
            self.nov_renderer.ResetCameraClippingRange()
            if self.nov_render_window:
                self.nov_render_window.Render()
            return True
        except Exception as e:
            import traceback
            print(f"[nov] process_nov_progressive_queue error: {e}")
            traceback.print_exc()
            return False

    def prepare_nov_scoring(
        self,
        bounds_world: Tuple[float, float, float, float, float, float],
        component: int,
        channel_ids: Optional[List[int]] = None,
    ) -> None:
        """Load the 3D region given by bounds_world at component and cache for sample_nov_ray_channels / sample_nov_ray_channels_occlusion.
        World bounds = (xmin, xmax, ymin, ymax, zmin, zmax). Per-channel p10/p99 for normalization.
        If channel_ids is None, uses all active channels; otherwise uses the intersection with active channels."""
        if not self._active_channels:
            self._nov_scoring_cache = None
            return
        xmin, xmax, ymin, ymax, zmin, zmax = bounds_world
        if xmax <= xmin or ymax <= ymin or zmax <= zmin:
            self._nov_scoring_cache = None
            return
        spacing = self._spacing_for_component(component)
        sx, sy, sz = spacing.sx, spacing.sy, spacing.sz
        z_dim, y_dim, x_dim = self._dims_for_component(component)
        vx0 = max(0, int(xmin / sx))
        vx1 = min(x_dim, max(vx0 + 1, int(np.ceil(xmax / sx))))
        vy0 = max(0, int(ymin / sy))
        vy1 = min(y_dim, max(vy0 + 1, int(np.ceil(ymax / sy))))
        vz0 = max(0, int(zmin / sz))
        vz1 = min(z_dim, max(vz0 + 1, int(np.ceil(zmax / sz))))
        if vx1 <= vx0 or vy1 <= vy0 or vz1 <= vz0:
            self._nov_scoring_cache = None
            return
        roi = ROI(vx0, vx1, vy0, vy1)
        if channel_ids is not None:
            channel_ids = sorted(c for c in channel_ids if c in self._active_channels)
        else:
            channel_ids = sorted(self._active_channels)
        if not channel_ids:
            self._nov_scoring_cache = None
            return
        arrays: Dict[int, np.ndarray] = {}
        p10_p99: Dict[int, Tuple[float, float]] = {}
        for ch in channel_ids:
            try:
                arr = self._load_channel_data(component, ch, roi)
            except Exception:
                continue
            if arr.size == 0 or arr.ndim != 3:
                continue
            # arr shape (Z, Y, X); slice z to [vz0:vz1]
            arr = np.asarray(arr[vz0:vz1, :, :], dtype=np.float64)
            if arr.size == 0:
                continue
            arrays[ch] = arr
            flat = arr.ravel()
            if flat.size > 0:
                p10, p99 = float(np.percentile(flat, 10)), float(np.percentile(flat, 99))
                p10_p99[ch] = (p10, p99)
            else:
                p10_p99[ch] = (0.0, 1.0)
        if not arrays:
            self._nov_scoring_cache = None
            return
        self._nov_scoring_cache = {
            "bounds_world": bounds_world,
            "component": component,
            "channel_ids": channel_ids,
            "arrays": arrays,
            "voxel_origin": (vx0, vy0, vz0),
            "spacing": (sx, sy, sz),
            "p10_p99": p10_p99,
        }

    @staticmethod
    def _ray_aabb_tnear_tfar(
        ray_origin: Tuple[float, float, float],
        ray_dir: Tuple[float, float, float],
        bounds: Tuple[float, float, float, float, float, float],
    ) -> Tuple[Optional[float], Optional[float]]:
        """Ray-AABB intersection. Returns (t_near, t_far) for ray P(t)=ray_origin+t*ray_dir, or (None, None) if no hit."""
        x0, x1, y0, y1, z0, z1 = bounds
        ox, oy, oz = ray_origin[0], ray_origin[1], ray_origin[2]
        dx, dy, dz = ray_dir[0], ray_dir[1], ray_dir[2]
        eps = 1e-12
        tnear = -np.inf
        tfar = np.inf
        for (lo, hi, o, d) in [(x0, x1, ox, dx), (y0, y1, oy, dy), (z0, z1, oz, dz)]:
            if abs(d) < eps:
                if o < lo or o > hi:
                    return (None, None)
                continue
            t0 = (lo - o) / d
            t1 = (hi - o) / d
            if t0 > t1:
                t0, t1 = t1, t0
            tnear = max(tnear, t0)
            tfar = min(tfar, t1)
            if tnear > tfar:
                return (None, None)
        if tfar < 0:
            return (None, None)
        tnear = max(0.0, tnear)
        return (tnear, tfar)

    def sample_nov_ray_channels(
        self,
        ray_origin: Tuple[float, float, float],
        ray_dir: Tuple[float, float, float],
        bounds: Tuple[float, float, float, float, float, float],
        num_samples: int = 64,
    ) -> List[float]:
        """Integrate along ray inside bounds; return per-channel energy (normalized with p10/p99, bottom 10% set to 0).
        Returns list of length len(active_channels) in same order as prepare_nov_scoring cache."""
        cache = self._nov_scoring_cache
        if cache is None or not cache.get("arrays"):
            channel_ids = sorted(self._active_channels) if self._active_channels else []
            return [0.0] * len(channel_ids)
        channel_ids = cache["channel_ids"]
        arrays = cache["arrays"]
        vx0, vy0, vz0 = cache["voxel_origin"]
        sx, sy, sz = cache["spacing"]
        p10_p99 = cache["p10_p99"]
        tnear, tfar = self._ray_aabb_tnear_tfar(ray_origin, ray_dir, bounds)
        if tnear is None or tfar is None or tfar <= tnear:
            return [0.0] * len(channel_ids)
        nch = len(channel_ids)
        energies = [0.0] * nch
        for k in range(num_samples):
            t = tnear + (tfar - tnear) * (k + 0.5) / num_samples
            wx = ray_origin[0] + t * ray_dir[0]
            wy = ray_origin[1] + t * ray_dir[1]
            wz = ray_origin[2] + t * ray_dir[2]
            vx = int((wx / sx) - vx0)
            vy = int((wy / sy) - vy0)
            vz = int((wz / sz) - vz0)
            for c, ch in enumerate(channel_ids):
                arr = arrays.get(ch)
                if arr is None:
                    continue
                nz, ny, nx = arr.shape
                if 0 <= vz < nz and 0 <= vy < ny and 0 <= vx < nx:
                    val = float(arr[vz, vy, vx])
                    p10, p99 = p10_p99.get(ch, (0.0, 1.0))
                    if p99 > p10:
                        norm = (val - p10) / (p99 - p10)
                        if val <= p10:
                            norm = 0.0
                        else:
                            norm = max(0.0, min(1.0, norm))
                    else:
                        norm = 0.0 if val <= p10 else 1.0
                    energies[c] += norm
        return energies

    def sample_nov_ray_channels_occlusion(
        self,
        ray_origin: Tuple[float, float, float],
        ray_dir: Tuple[float, float, float],
        bounds: Tuple[float, float, float, float, float, float],
        channel_ids: Optional[List[int]] = None,
        presence_thresh: float = 0.05,
        num_samples: int = 64,
        step_scale: float = 0.15,
        eps: float = 1e-12,
    ) -> List[float]:
        """Front-to-back ray march with occlusion; return per-object visibility Vis(o) for each channel (object).
        Normalized intensity below presence_thresh is treated as 0. T(r,i) = prod(1-alpha(r,j)) for j<i;
        delta_vis(r,i) = T(r,i)*alpha(r,i); contribution to object o = delta_vis * m_o(x) with m_o = normalized intensity (0 if below threshold).
        Returns list of length len(channel_ids) from cache, or len(active_channels) if channel_ids is None."""
        cache = self._nov_scoring_cache
        if cache is None or not cache.get("arrays"):
            cids = channel_ids if channel_ids is not None else sorted(self._active_channels)
            return [0.0] * len(cids)
        cids = cache["channel_ids"]
        arrays = cache["arrays"]
        vx0, vy0, vz0 = cache["voxel_origin"]
        sx, sy, sz = cache["spacing"]
        p10_p99 = cache["p10_p99"]
        tnear, tfar = self._ray_aabb_tnear_tfar(ray_origin, ray_dir, bounds)
        if tnear is None or tfar is None or tfar <= tnear:
            return [0.0] * len(cids)
        step = (tfar - tnear) / num_samples
        nch = len(cids)
        vis = [0.0] * nch
        T = 1.0
        for k in range(num_samples):
            t = tnear + (tfar - tnear) * (k + 0.5) / num_samples
            wx = ray_origin[0] + t * ray_dir[0]
            wy = ray_origin[1] + t * ray_dir[1]
            wz = ray_origin[2] + t * ray_dir[2]
            vx = int((wx / sx) - vx0)
            vy = int((wy / sy) - vy0)
            vz = int((wz / sz) - vz0)
            norms = []
            for ch in cids:
                arr = arrays.get(ch)
                if arr is None:
                    norms.append(0.0)
                    continue
                nz, ny, nx = arr.shape
                if 0 <= vz < nz and 0 <= vy < ny and 0 <= vx < nx:
                    val = float(arr[vz, vy, vx])
                    p10, p99 = p10_p99.get(ch, (0.0, 1.0))
                    if p99 > p10 and val > p10:
                        norm = max(0.0, min(1.0, (val - p10) / (p99 - p10)))
                    else:
                        norm = 0.0 if val <= p10 else 1.0
                    if norm < presence_thresh:
                        norm = 0.0
                else:
                    norm = 0.0
                norms.append(norm)
            density = sum(norms) + eps
            alpha = 1.0 - np.exp(-density * step * step_scale)
            alpha = min(1.0, alpha)
            delta_vis = T * alpha
            T = T * (1.0 - alpha)
            if delta_vis > eps and density > eps:
                for c in range(nch):
                    vis[c] += delta_vis * (norms[c] / density)
            if T <= eps:
                break
        return vis

    def reload_current_volumes(self) -> None:
        """Reload and redisplay all active channels with current component/ROI (e.g. after NOV box clip change)."""
        for ch in list(self._active_channels):
            st = self.state.get(ch)
            if st is None:
                continue
            self._load_and_display_channel(ch, st.component, st.roi, reset_camera=False)

    def _spacing_for_component(self, component: int) -> SpacingConfig:
        scale = float(2 ** component)
        return SpacingConfig(
            sx=self.cfg.base_sx * scale,
            sy=self.cfg.base_sy * scale,
            sz=self.cfg.base_sz,
        )

    def _dims_for_component(self, component: int) -> Tuple[int, int, int]:
        shape = self.zsrc.shape_tczyx(component)
        _, _, z, y, x = shape
        return (z, y, x)

    def _volume_bounds_world(self, component: int):
        spacing = self._spacing_for_component(component)
        z, y, x = self._dims_for_component(component)
        return (0.0, x * spacing.sx, 0.0, y * spacing.sy, 0.0, z * spacing.sz)

    def _precompute_transfer_function(self, ch: int, sample_image: vtkImageData):
        """Compute and cache transfer function for a channel."""
        color_name = self.cfg.channel_colors[
            list(self.cfg.channels).index(ch) % len(self.cfg.channel_colors)
        ]
        tint = color_name_to_rgb(color_name)
        color_tf, opacity_tf = build_histogram_tf(sample_image, tint_rgb=tint)
        self._channel_tfs[ch] = (color_tf, opacity_tf)
        return color_tf, opacity_tf
    
    def update_channel_intensity_range(self, channel_id: int, range_pct: Tuple[float, float]):
        """
        Update the intensity range for a channel.
        
        Args:
            channel_id: Channel to update
            range_pct: [low%, high%] from slider (0-100)
        """
        if channel_id not in self._active_channels:
            return
        
        if channel_id not in self._channel_data_range:
            print(f"[stream] No data range stored for channel {channel_id}")
            return
        
        data_range = self._channel_data_range[channel_id]
        tint_rgb = self._channel_colors.get(channel_id, (1.0, 1.0, 1.0))
        
        color_tf, opacity_tf = build_tf_with_range(data_range, range_pct, tint_rgb)
        self._channel_tfs[channel_id] = (color_tf, opacity_tf)
        
        if channel_id in self.volumes:
            vol = self.volumes[channel_id]
            prop = vol.GetProperty()
            prop.SetColor(color_tf)
            prop.SetScalarOpacity(opacity_tf)
        
        self._render()

    def _get_or_create_volume(self, ch: int) -> Tuple[vtkVolume, vtkGPUVolumeRayCastMapper]:
        """Get existing VTK volume/mapper or create new ones."""
        if ch in self.volumes:
            return self.volumes[ch], self.mappers[ch]

        mapper = vtkGPUVolumeRayCastMapper()
        mapper.SetAutoAdjustSampleDistances(True)

        prop = vtkVolumeProperty()
        if self.cfg.linear_interpolation:
            prop.SetInterpolationTypeToLinear()
        else:
            prop.SetInterpolationTypeToNearest()

        if self.cfg.shade:
            prop.ShadeOn()
            prop.SetAmbient(0.5)
            prop.SetDiffuse(0.8)
            prop.SetSpecular(0.1)
            prop.SetSpecularPower(8.0)
        else:
            prop.ShadeOff()

        vol = vtkVolume()
        vol.SetMapper(mapper)
        vol.SetProperty(prop)

        self.volumes[ch] = vol
        self.mappers[ch] = mapper
        return vol, mapper

    def _get_or_create_nov_volume(self, ch: int) -> Tuple[vtkVolume, vtkGPUVolumeRayCastMapper]:
        """Get or create volume/mapper for the NOV popup renderer."""
        if self.nov_renderer is None:
            raise RuntimeError("NOV renderer not set")
        if ch in self.nov_volumes:
            return self.nov_volumes[ch], self.nov_mappers[ch]
        mapper = vtkGPUVolumeRayCastMapper()
        mapper.SetAutoAdjustSampleDistances(True)
        prop = vtkVolumeProperty()
        if self.cfg.linear_interpolation:
            prop.SetInterpolationTypeToLinear()
        else:
            prop.SetInterpolationTypeToNearest()
        if self.cfg.shade:
            prop.ShadeOn()
            prop.SetAmbient(0.5)
            prop.SetDiffuse(0.8)
            prop.SetSpecular(0.1)
            prop.SetSpecularPower(8.0)
        else:
            prop.ShadeOff()
        vol = vtkVolume()
        vol.SetMapper(mapper)
        vol.SetProperty(prop)
        self.nov_volumes[ch] = vol
        self.nov_mappers[ch] = mapper
        return vol, mapper

    # OpenGL 3D texture limit (avoid "Invalid texture dimensions" / MAX_3D_TEXTURE_SIZE 2048)
    MAX_TEXTURE_DIM = 2048

    def _create_vtk_image(
        self,
        np_vol_zyx: np.ndarray,
        spacing: SpacingConfig,
        origin_xyz: Tuple[float, float, float],
        for_nov_view: bool = False,
    ) -> vtkImageData:
        """Create vtkImageData from numpy array. When for_nov_view=True and NOV box is set, clip to box (for popup only)."""
        np_vol_zyx = np.ascontiguousarray(np_vol_zyx, dtype=np.uint16)
        z, y, x = np_vol_zyx.shape
        if x <= 0 or y <= 0 or z <= 0:
            raise ValueError(
                f"Invalid volume shape ({z}, {y}, {x}): cannot create 3D texture. "
                "ROI may be out of bounds for this LOD level."
            )
        sx, sy, sz = spacing.sx, spacing.sy, spacing.sz

        if max(x, y, z) > self.MAX_TEXTURE_DIM:
            scale = self.MAX_TEXTURE_DIM / max(x, y, z)
            oz, oy, ox = z, y, x
            nz = max(1, int(round(z * scale)))
            ny = max(1, int(round(y * scale)))
            nx = max(1, int(round(x * scale)))
            iz = np.linspace(0, z - 1, nz).round().astype(np.intp)
            iy = np.linspace(0, y - 1, ny).round().astype(np.intp)
            ix = np.linspace(0, x - 1, nx).round().astype(np.intp)
            np_vol_zyx = np_vol_zyx[np.ix_(iz, iy, ix)]
            z, y, x = nz, ny, nx
            spacing = SpacingConfig(
                sx=sx * (ox / nx) if nx else sx,
                sy=sy * (oy / ny) if ny else sy,
                sz=sz * (oz / nz) if nz else sz,
            )
            print(f"[stream] Downsampled volume to ({z},{y},{x}) for OpenGL 2048 limit")

        # Apply NOV box clip only for NOV popup view (main view always shows full volume)
        clip = getattr(self, "_nov_box_clip", None)
        if for_nov_view and clip is not None:
            (cx, cy, cz), length, width, depth = clip
            hL, hW, hD = length / 2.0, width / 2.0, depth / 2.0
            ox, oy, oz = origin_xyz[0], origin_xyz[1], origin_xyz[2]
            sx, sy, sz = spacing.sx, spacing.sy, spacing.sz
            np_vol_zyx = np_vol_zyx.copy()
            wz = oz + np.arange(z, dtype=np.float64) * sz
            wy = oy + np.arange(y, dtype=np.float64) * sy
            wx = ox + np.arange(x, dtype=np.float64) * sx
            in_x = (wx >= cx - hL) & (wx <= cx + hL)
            in_y = (wy >= cy - hW) & (wy <= cy + hW)
            in_z = (wz >= cz - hD) & (wz <= cz + hD)
            inside = in_z.reshape(-1, 1, 1) & in_y.reshape(1, -1, 1) & in_x.reshape(1, 1, -1)
            np_vol_zyx[~inside] = 0

        vtk_arr = numpy_to_vtk(np_vol_zyx.ravel(order="C"), deep=True)
        vtk_arr.SetName("scalars")

        img = vtkImageData()
        img.SetDimensions(x, y, z)
        img.SetExtent(0, x - 1, 0, y - 1, 0, z - 1)
        img.SetOrigin(*origin_xyz)
        img.SetSpacing(spacing.sx, spacing.sy, spacing.sz)
        img.GetPointData().SetScalars(vtk_arr)
        img.Modified()
        return img

    def _cache_key(self, component: int, ch: int, roi: ROI) -> Tuple[int, int, ROI]:
        return (component, ch, roi)

    def _get_cached_array(self, component: int, ch: int, roi: ROI) -> Optional[np.ndarray]:
        key = self._cache_key(component, ch, roi)
        return self._array_cache.get(key)

    def _put_cached_array(self, component: int, ch: int, roi: ROI, arr: np.ndarray):
        key = self._cache_key(component, ch, roi)

        if len(self._array_cache) >= self._cache_max_entries:
            keys_to_remove = list(self._array_cache.keys())[
                :self._cache_max_entries // 2]
            for k in keys_to_remove:
                del self._array_cache[k]

        self._array_cache[key] = arr

    def _load_channel_data(self, component: int, ch: int, roi: ROI) -> np.ndarray:
        """Load channel data - runs in background thread."""
        cached = self._get_cached_array(component, ch, roi)
        if cached is not None:
            print(f"[cache hit] comp={component} ch={ch} roi={roi}")
            return cached

        print(
            f"[loading] comp={component} ch={ch} roi=({roi.x0}:{roi.x1}, {roi.y0}:{roi.y1})")
        t0 = time.perf_counter()

        darr = self.zsrc.array(component)
        vol_zyx = darr[self.cfg.zarr_time_index,
                       ch, :, roi.y0:roi.y1, roi.x0:roi.x1]
        np_arr = vol_zyx.compute()

        t1 = time.perf_counter()
        print(
            f"[loaded] comp={component} ch={ch} in {t1-t0:.2f}s, shape={np_arr.shape}")

        self._put_cached_array(component, ch, roi, np_arr)
        return np_arr

    def _init_low_res_full(self):
        """Initialize with full low-res volumes - runs on main thread"""
        if not self.cfg.channels:
            print("[stream] No channels to initialize")
            return
    
        comp = self.cfg.start_component
        spacing = self._spacing_for_component(comp)

        print(f"[stream] init: loading FULL volumes at component={comp}")

        darr = self.zsrc.array(comp)
        _, _, z, y, x = darr.shape

        for i, ch in enumerate(self.cfg.channels):
            ch = int(ch)

            np_vol = darr[self.cfg.zarr_time_index, ch, :, :, :].compute()
            img = self._create_vtk_image(np_vol, spacing, (0.0, 0.0, 0.0))

            color_tf, opacity_tf = self._precompute_transfer_function(ch, img)

            vol, mapper = self._get_or_create_volume(ch)
            mapper.SetInputData(img)

            prop = vol.GetProperty()
            prop.SetColor(color_tf)
            prop.SetScalarOpacity(opacity_tf)
            prop.SetScalarOpacityUnitDistance(
                max(1e-6, 1.0 * min(spacing.sx, spacing.sy, spacing.sz)))

            self.renderer.AddVolume(vol)
            self.state[ch] = ChannelState(
                component=comp, roi=ROI(0, int(x), 0, int(y)))
            self._put_cached_array(comp, ch, ROI(0, int(x), 0, int(y)), np_vol)

            color_name = self.cfg.channel_colors[i % len(
                self.cfg.channel_colors)]
            print(
                f"[stream] added ch={ch} color={color_name} dims=(z={z},y={y},x={x})")

        self._last_component = comp
        self.renderer.ResetCameraClippingRange()
        self.renderer.ResetCamera()
        self._render()

    def _render(self):
        """Trigger render"""
        self.render_window.Render()
        if self.render_callback is not None:
            self.render_callback()

    def _apply_loaded_data_on_main_thread(self, loaded: LoadedData):
        """
        Apply loaded numpy arrays to VTK objects.
        MUST be called on main thread where OpenGL context exists.
        """
        component = loaded.component
        roi = loaded.roi
        spacing = self._spacing_for_component(component)
        origin_xyz = (roi.x0 * spacing.sx, roi.y0 * spacing.sy, 0.0)

        for ch, np_arr in loaded.channel_arrays.items():
            if np_arr.size == 0 or any(s <= 0 for s in np_arr.shape):
                print(f"[stream] Skipping channel {ch}: loaded shape {np_arr.shape} has zero size.")
                continue
            try:
                img = self._create_vtk_image(np_arr, spacing, origin_xyz)
            except ValueError as e:
                print(f"[stream] Skipping channel {ch}: {e}")
                continue

            vol, mapper = self._get_or_create_volume(ch)
            mapper.SetInputData(img)
            mapper.Modified()

            if ch in self._channel_tfs:
                color_tf, opacity_tf = self._channel_tfs[ch]
                prop = vol.GetProperty()
                prop.SetColor(color_tf)
                prop.SetScalarOpacity(opacity_tf)
                prop.SetScalarOpacityUnitDistance(
                    max(1e-6, 1.0 * min(spacing.sx, spacing.sy, spacing.sz))
                )

            self.state[ch] = ChannelState(component=component, roi=roi)

        self._last_component = component
        self.renderer.ResetCameraClippingRange()
        self._render()

        print(
            f"[stream] applied {len(loaded.channel_arrays)} channels at comp={component}")

    def _background_load(self, request: LoadRequest):
        """
        Background thread: Load data from zarr, then queue for main thread update.
        """
        with self._loading_lock:
            if self._is_loading:
                return
            self._is_loading = True

        try:
            component = request.component
            roi = request.roi

            channel_arrays: Dict[int, np.ndarray] = {}
            for ch in self._active_channels: 
                ch = int(ch)
                prev = self.state.get(ch)
                need_update = (prev is None) or (
                    prev.component != component) or (prev.roi != roi)

                if need_update:
                    channel_arrays[ch] = self._load_channel_data(
                        component, ch, roi)

            if channel_arrays:
                loaded = LoadedData(
                    component=component,
                    roi=roi,
                    channel_arrays=channel_arrays,
                    timestamp=request.timestamp,
                )
                self._loaded_data_queue.put(loaded)
                print(
                    f"[background] queued {len(channel_arrays)} channels for main thread")

        finally:
            with self._loading_lock:
                self._is_loading = False

            with self._debounce_lock:
                if self._pending_request and self._pending_request.timestamp > request.timestamp:
                    self._schedule_load(self._pending_request)
                    self._pending_request = None

    def _schedule_load(self, request: LoadRequest):
        """Submit load request to thread pool"""
        VolumeStreamer._executor.submit(self._background_load, request)

    def _debounce_callback(self, request: LoadRequest):
        """Called after debounce delay"""
        time.sleep(self.DEBOUNCE_DELAY)

        with self._debounce_lock:
            if self._pending_request and self._pending_request.timestamp == request.timestamp:
                self._pending_request = None
                self._schedule_load(request)

    def check_and_apply_loaded_data(self):
        """
        Call this from main thread (e.g., in a timer or after interaction).
        Applies any data that was loaded in background threads.
        """
        applied_any = False
        while True:
            try:
                loaded = self._loaded_data_queue.get_nowait()
                self._apply_loaded_data_on_main_thread(loaded)
                applied_any = True
            except queue.Empty:
                break
        return applied_any

    def on_interaction_end(self):
        """
        Called on EndInteractionEvent - debounced and async.
        This runs on main thread.
        """
        if not self._active_channels:  
            return

        self.check_and_apply_loaded_data()

        cam = self.renderer.GetActiveCamera()
        dist = camera_distance_to_focal(cam)

        desired_comp = choose_component(
            dist,
            self.cfg.distance_rules,
            min_component=self.cfg.min_component,
            max_component=self.cfg.max_component,
        )

        spacing = self._spacing_for_component(desired_comp)
        zdim, ydim, xdim = self._dims_for_component(desired_comp)
        bounds = self._volume_bounds_world(desired_comp)

        roi = compute_visible_xy_roi_vox(
            self.renderer,
            bounds_world=bounds,
            sx=spacing.sx,
            sy=spacing.sy,
            x_dim=xdim,
            y_dim=ydim,
            margin_vox=self.cfg.roi_margin_vox,
        )

        print(
            f"[interaction] dist={dist:.1f} -> comp={desired_comp} roi=({roi.x0}:{roi.x1}, {roi.y0}:{roi.y1})")

        needs_update = False
        for ch in self._active_channels:  
            ch = int(ch)
            prev = self.state.get(ch)
            if prev is None or prev.component != desired_comp or prev.roi != roi:
                needs_update = True
                break

        if not needs_update:
            print(f"[interaction] no update needed")
            return

        request = LoadRequest(
            component=desired_comp,
            roi=roi,
            timestamp=time.time(),
        )

        with self._debounce_lock:
            self._pending_request = request

        threading.Thread(
            target=self._debounce_callback,
            args=(request,),
            daemon=True,
        ).start()
