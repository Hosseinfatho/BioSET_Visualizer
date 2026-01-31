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

from .lod import (
    camera_distance_to_focal,
    choose_component,
    compute_visible_xy_roi_vox,
    ROI,
)
from .zarr_source import ZarrMultiscaleSource
from ..scene.volumes import (
    SpacingConfig,
    color_name_to_rgb,
    build_histogram_tf,
)

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
    """
    Improved volume streamer with async loading and debouncing.

    IMPORTANT: OpenGL/VTK updates must happen on the main thread.
    We load data in background threads, then apply to VTK on main thread.
    """

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

        self._init_low_res_full()

    def set_render_callback(self, fn: Callable):
        self.render_callback = fn

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

    def _create_vtk_image(
        self,
        np_vol_zyx: np.ndarray,
        spacing: SpacingConfig,
        origin_xyz: Tuple[float, float, float],
    ) -> vtkImageData:
        """Create vtkImageData from numpy array"""
        np_vol_zyx = np.ascontiguousarray(np_vol_zyx, dtype=np.uint16)
        z, y, x = np_vol_zyx.shape

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
            img = self._create_vtk_image(np_arr, spacing, origin_xyz)

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
            for ch in self.cfg.channels:
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
        for ch in self.cfg.channels:
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
