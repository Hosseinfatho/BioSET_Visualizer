from __future__ import annotations

import threading
import time
import queue
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Callable
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
        self.nov_renderer = None
        self.nov_render_window = None
        self.nov_volumes: Dict[int, vtkVolume] = {}
        self.nov_mappers: Dict[int, vtkGPUVolumeRayCastMapper] = {}

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
        for ch, vol in list(self.nov_volumes.items()):
            if self.nov_renderer.HasViewProp(vol):
                self.nov_renderer.RemoveVolume(vol)
        self.nov_volumes.clear()
        self.nov_mappers.clear()
        if self.nov_render_window:
            self.nov_render_window.Render()
        if self.render_callback is not None:
            self.render_callback()

    def sync_nov_volumes(self) -> None:
        """Update NOV popup volumes with clipped data from current state. No-op if no nov_renderer or no clip."""
        if not self.nov_renderer or not self._nov_box_clip or not self._active_channels:
            return
        for ch in list(self._active_channels):
            st = self.state.get(ch)
            if st is None:
                continue
            try:
                np_arr = self._load_channel_data(st.component, ch, st.roi)
            except Exception:
                continue
            if np_arr.size == 0 or any(s <= 0 for s in np_arr.shape):
                continue
            spacing = self._spacing_for_component(st.component)
            origin_xyz = (st.roi.x0 * spacing.sx, st.roi.y0 * spacing.sy, 0.0)
            try:
                img = self._create_vtk_image(np_arr, spacing, origin_xyz, for_nov_view=True)
            except ValueError:
                continue
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
        self.nov_renderer.ResetCameraClippingRange()
        if self.nov_render_window:
            self.nov_render_window.Render()
        if self.render_callback is not None:
            self.render_callback()

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
