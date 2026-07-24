from __future__ import annotations

import math
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Callable

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonDataModel import vtkImageData
from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction
from vtkmodules.vtkRenderingCore import vtkColorTransferFunction, vtkVolume, vtkVolumeProperty
from vtkmodules.vtkRenderingVolume import vtkGPUVolumeRayCastMapper, vtkMultiVolume

from .chunk_cache import ChunkCache, PageTable
from .chunk_grid import ChunkGrid
from .lod import (
    ROI,
    camera_distance_to_focal,
    choose_component,
    compute_visible_xy_roi_vox,
    derive_distance_rules,
    scale_roi_to_component,
)
from .profiling import StageTimer, fmt_bytes, log as plog
from .zarr_source import ZarrMultiscaleSource
from ..scene.volumes import SpacingConfig, color_name_to_rgb, build_histogram_tf, build_tf_with_range


@dataclass
class ChannelState:
    component: int
    roi: ROI


@dataclass
class LoadRequest:
    """Represents a pending load operation.

    gen is a monotonically increasing generation id. A request is stale the
    moment a newer one is created, which lets in-flight work bail early and
    lets the apply step drop results that no longer match the viewport.
    """
    component: int
    roi: ROI
    timestamp: float
    gen: int = 0


@dataclass
class LoadedData:
    """Result built in the worker thread, ready for a cheap main-thread swap.

    The heavy numpy->vtk conversion is done in the worker, so channel_images
    already hold fully-built vtkImageData. The main thread only does
    SetInputData + Render.
    """
    component: int
    roi: ROI
    channel_images: Dict[int, "vtkImageData"]
    timestamp: float
    gen: int
    was_capped: bool = False   # this frame's texture was downsized for speed
    hires: bool = False        # this frame is the idle full-resolution upgrade


class VolumeStreamer:
    DEBOUNCE_DELAY = 0.15
    # Initial camera distance: 1.0 = VTK default. Use e.g. 0.7 to move camera a little closer, 0.5 for more zoom.
    INITIAL_CAMERA_ZOOM = 1.0

    _executor: ThreadPoolExecutor = None

    def __init__(self, *, cfg, renderer, render_window):
        self.cfg = cfg
        self.renderer = renderer
        self.render_window = render_window
        self.render_callback: Optional[Callable] = None

        if VolumeStreamer._executor is None:
            VolumeStreamer._executor = ThreadPoolExecutor(max_workers=2)

        # Dedicated pool so the channels of a single viewport load concurrently
        # (one slow/remote channel no longer blocks the fast cached ones). Kept
        # separate from the shared _executor (which also serves NOV) to avoid
        # a worker waiting on the same pool it occupies.
        self._channel_executor = ThreadPoolExecutor(
            max_workers=6, thread_name_prefix="channel_load")

        max_bytes = int(cfg.cache_size_gb * (1024**3))
        self.zsrc = ZarrMultiscaleSource(
            url=cfg.zarr_url,
            cache_enabled=cfg.cache_enabled,
            cache_dir=cfg.cache_dir,
            cache_size_bytes=max_bytes,
            globus_client_id=getattr(cfg, "globus_client_id", None),
            globus_collection_id=getattr(cfg, "globus_collection_id", None),
            globus_https_base=getattr(cfg, "globus_https_base", None),
            globus_token_file=getattr(cfg, "globus_token_file", "~/.bioset/globus_token.json"),
        )

        self.volumes: Dict[int, vtkVolume] = {}
        self.mappers: Dict[int, vtkGPUVolumeRayCastMapper] = {}
        self.state: Dict[int, ChannelState] = {}

        # Multi-channel blending: all active channels share ONE GPU mapper via a
        # vtkMultiVolume (one input port per channel), so the ray-caster composites
        # them per sample instead of the last-added volume occluding the others.
        # Each port is still an independently-updated texture, so per-channel
        # streaming (base swap, coalescing, ROI refine) is unchanged.
        self._blend_mode = "composite"
        # A single real volume triggers a vtkMultiVolume bug that ignores the
        # image origin (content renders shifted); >=2 volumes render correctly.
        # A permanent transparent 1^3 dummy keeps the count >=2 for a lone channel.
        self._dummy_volume, self._dummy_image = self._make_dummy_volume()
        self._multi_mapper = self._new_multi_mapper()
        self._multi_volume = self._new_multi_volume()
        self._multi_volume.SetMapper(self._multi_mapper)
        self.renderer.AddVolume(self._multi_volume)
        self._channel_port: Dict[int, int] = {}          # ch -> port index
        self._current_input: Dict[int, "vtkImageData"] = {}  # last texture per ch (for rebuilds)

        self._channel_tfs: Dict[int,
                                Tuple[vtkColorTransferFunction, vtkPiecewiseFunction]] = {}
        self._channel_percentile_bounds: Dict[int, Tuple[float, float, float]] = {}

        # --- Latest-wins async loader state ---
        # on_interaction_end stores the newest desired (comp, roi) here; the
        # main-thread poll loop (service_loads) debounces and dispatches it.
        self._req_lock = threading.Lock()
        self._latest_request: Optional[LoadRequest] = None
        self._max_gen = 0                       # newest generation handed out
        self._inflight = False                  # a worker load is running

        # Idle-refine: after the user stops, re-render once at full quality.
        self._needs_still_render = False
        self._idle_ticks = 0

        # Interactive resolution cap: while loading/interacting we upload a
        # small (fast) texture; once idle we rebuild full-res from cached numpy
        # and swap it in. _capped_channels = channels currently shown downsized.
        self._capped_channels: set[int] = set()
        self._hires_inflight = False

        self._loaded_data_queue: queue.Queue[LoadedData] = queue.Queue()

        # --- Chunk page-table cache (replaces the exact-ROI array cache) ---
        # Decoded chunks keyed by (comp, ch, cyi, cxi), byte-budget LRU, over the
        # on-disk compressed CacheStore. Overlapping pans/zooms reuse tiles
        # instead of re-downloading whole ROIs.
        chunk_budget = int(getattr(cfg, "chunk_cache_gb", 1.0) * (1024**3))
        self._chunk_cache = ChunkCache(budget_bytes=chunk_budget)
        # Dedicated cache for the coarsest LOD's tiles. Fine-tile churn (which can
        # fill the main cache fast when zooming in) must never evict these, or the
        # blurry base disappears and the volume blanks to the edges on zoom-out.
        # Sized to comfortably hold the whole coarsest level for all channels;
        # it only evicts (LRU) if that budget is genuinely exceeded.
        lowres_budget = int(getattr(cfg, "lowres_cache_gb", 0.5) * (1024**3))
        self._lowres_cache = ChunkCache(budget_bytes=lowres_budget)
        self._page_table = PageTable()
        self._grids: Dict[int, ChunkGrid] = {}     # comp -> ChunkGrid (lazy)
        self._grids_lock = threading.Lock()
        # Concurrent per-chunk fetch (tiles); separate from _channel_executor
        # (channels) so a channel task never waits on the pool it occupies.
        self._chunk_pool = ThreadPoolExecutor(
            max_workers=12, thread_name_prefix="chunk_fetch")

        # Per-channel full-volume coarsest texture, shown instantly while the user
        # interacts (never empty, cheap to render) and replaced by the sharp ROI
        # texture once they settle. _showing_base = channels currently on the base.
        self._base_images: Dict[int, "vtkImageData"] = {}
        self._showing_base: set[int] = set()
        # Per-channel last SHARP (fine ROI) texture, retained so an interaction
        # can toggle a port back to it (fine<->base) without re-streaming. Used
        # by the adaptive interaction LOD (keep sharp while the viewport stays
        # inside the loaded ROI; drop to the coarse base only when it doesn't).
        self._fine_images: Dict[int, "vtkImageData"] = {}
        self._interaction_move_last = 0.0  # throttle for on_interaction_move
        # Total bytes fetched from the store (decoded), for profiling. The TF is
        # built once from real activation data and reused per frame (see
        # _apply_loaded_data_on_main_thread), so a zero/blurry seed frame renders
        # transparent through the frozen TF — no solid-block, no per-frame flicker.
        self._bytes_fetched = 0

        self._last_component: Optional[int] = None
        self._initial_camera: Optional[dict] = None  # position, focalPoint, viewUp after first load

        self._active_channels: set[int] = set() 
        self._channel_colors: Dict[int, Tuple[float, float, float]] = {}

        self._channel_data_range: Dict[int, Tuple[float, float]] = {}
        self._channel_histograms: Dict[int, list] = {}

        # NOV box clip: when set, only voxels inside the box are shown in the NOV popup view (main view stays full)
        self._nov_lens_clip: Optional[Tuple[Tuple[float, float, float], float, float, float]] = None
        # NOV scoring: cached 3D arrays for entropy+occlusion scoring (prepare_nov_scoring / sample_nov_ray_channels)
        self._nov_scoring_cache: Optional[dict] = None
        self.nov_renderer = None
        self.nov_render_window = None
        self.nov_render_callback = None  # optional: called after each NOV window render (e.g. update scale bar)
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
        max_bytes = int(self.cfg.cache_size_gb * (1024**3))
        self.zsrc = ZarrMultiscaleSource(
            url=url,
            cache_enabled=self.cfg.cache_enabled,
            cache_dir=self.cfg.cache_dir,
            cache_size_bytes=max_bytes,
            globus_client_id=getattr(self.cfg, "globus_client_id", None),
            globus_collection_id=getattr(self.cfg, "globus_collection_id", None),
            globus_https_base=getattr(self.cfg, "globus_https_base", None),
            globus_token_file=getattr(self.cfg, "globus_token_file", "~/.bioset/globus_token.json"),
        )
        self._chunk_cache.clear()
        self._lowres_cache.clear()
        self._page_table.clear()
        self._base_images.clear()
        self._fine_images.clear()
        self._showing_base.clear()
        self._level0_dims = None
        with self._grids_lock:
            self._grids.clear()
        self._active_channels.clear()
        self._channel_colors.clear()
        self._channel_tfs.clear()
        self.volumes.clear()
        self.mappers.clear()
        self._current_input.clear()
        self._rebuild_multivolume()   # drops all ports (volumes now empty)
        self.state.clear()
        self._initial_camera = None

    def set_spacing(self, sx: float, sy: float, sz: float):
        """Update the base spacing."""
        self.cfg = self.cfg.__class__(**{**self.cfg.__dict__,
                                        "base_sx": sx,
                                        "base_sy": sy,
                                        "base_sz": sz})
        print(f"[stream] Updated spacing: ({sx}, {sy}, {sz})")

    def configure_lod_from_source(self):
        """Derive the LOD component range and zoom thresholds from the store.

        Must run after `set_zarr_url` and `set_spacing`, since it reads the
        pyramid depth from the zarr and scales the distance rules by the
        volume's physical extent. Leaves the configured defaults in place if
        the store can't be inspected.
        """
        try:
            levels = self.zsrc.level_count()
        except Exception as e:
            print(f"[stream] Could not detect pyramid depth, keeping defaults: {e}")
            return

        max_component = max(0, levels - 1)
        updates = {
            "min_component": 0,
            "max_component": max_component,
            # Start coarse: the first frame should be cheap, LOD refines after.
            "start_component": max_component,
        }

        try:
            z, y, x = self._dims_for_component(0)
            diagonal = math.sqrt(
                (x * self.cfg.base_sx) ** 2
                + (y * self.cfg.base_sy) ** 2
                + (z * self.cfg.base_sz) ** 2
            )
            rules = derive_distance_rules(diagonal, max_component)
            if rules:
                updates["distance_rules"] = rules
                print(f"[stream] World diagonal: {diagonal:.1f} "
                      f"-> LOD distances: {[round(d) for d, _ in rules[:-1]]}")
        except Exception as e:
            print(f"[stream] Could not derive distance rules, keeping defaults: {e}")

        self.cfg = self.cfg.__class__(**{**self.cfg.__dict__, **updates})
        print(f"[stream] Detected {levels} resolution levels "
              f"(components 0..{max_component})")

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

        # Safety net: never replace the live texture with a degenerate (needle)
        # ROI — that would blank the volume (the edge-on vanish bug). Keep the
        # currently displayed frame instead.
        if self._roi_is_degenerate(roi):
            print(f"[stream] Skipping degenerate ROI {roi} (keeping current frame)")
            return

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

        simple = is_first_volume or self._last_component is None
        if simple:
            self._load_and_display_channel(channel_id, comp, roi, reset_camera=is_first_volume)
        else:
            # Paint the new channel instantly at the FULL-volume coarsest LOD
            # (served from the pinned/warmed low-res cache = no fine fetch on the
            # main thread), which also builds its transfer function and covers the
            # whole volume (never empty). Then refine to the current viewport
            # asynchronously (progressive, centre-out) alongside the others.
            coarse = self.cfg.max_component
            _, ydim, xdim = self._dims_for_component(coarse)
            self._load_and_display_channel(
                channel_id, coarse, ROI(0, xdim, 0, ydim), reset_camera=False)
            if comp < coarse:
                self._set_latest_request(comp, roi)

        # The channel is showing a full-volume coarse texture; cache it as the
        # interaction "base" and warm the low-res cache so it's instantly
        # available while moving / on re-add.
        self._showing_base.add(channel_id)
        self._ensure_base_image(channel_id)
        VolumeStreamer._executor.submit(self._prefetch_lowres, channel_id)

    def deactivate_channel(self, channel_id: int):
        """
        Deactivate a channel - remove from rendering.
        """
        if channel_id not in self._active_channels:
            print(f"[stream] Channel {channel_id} not active")
            return
        
        print(f"[stream] Deactivating channel {channel_id}")
        
        self._active_channels.discard(channel_id)

        # Drop the channel's volume and re-pack the multi-volume ports.
        self.volumes.pop(channel_id, None)
        self.mappers.pop(channel_id, None)
        self._current_input.pop(channel_id, None)
        self._fine_images.pop(channel_id, None)
        self._rebuild_multivolume()

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

        self._base_images.pop(channel_id, None)
        self._showing_base.discard(channel_id)
        self._capped_channels.discard(channel_id)

        self._render()

    @staticmethod
    def _compute_histogram(np_arr: np.ndarray, data_range: tuple, n_bins: int = 32) -> list:
        """Compute a normalized histogram from a numpy volume array.
        Bins span data_range=(r0, r1) to match the slider's linear mapping.
        Uses log1p scaling to handle heavily skewed distributions."""
        flat = np_arr.ravel()
        counts, _ = np.histogram(flat, bins=n_bins, range=data_range)
        log_counts = np.log1p(counts.astype(np.float64))
        max_val = log_counts.max()
        if max_val == 0:
            return [0.0] * n_bins
        return (log_counts / max_val).tolist()

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
        
        # Compute histogram using the same range as the slider
        self._channel_histograms[channel_id] = self._compute_histogram(np_arr, (r0, r1))

        vol, mapper = self._get_or_create_volume(channel_id)
        self._set_channel_input(channel_id, img)

        tint_rgb = self._channel_colors.get(channel_id, (1.0, 1.0, 1.0))
        color_tf, opacity_tf, pct_range = build_histogram_tf(img, tint_rgb=tint_rgb)
        self._channel_tfs[channel_id] = (color_tf, opacity_tf)
        self._channel_percentile_bounds[channel_id] = pct_range

        prop = vol.GetProperty()
        prop.SetColor(color_tf)
        prop.SetScalarOpacity(opacity_tf)
        # Membership is handled by the shared multi-volume (no per-channel AddVolume).

        self.state[channel_id] = ChannelState(component=component, roi=roi)
        self._last_component = component
        
        if reset_camera:
            print(f"[stream] Framing default view for first volume")
            self.frame_default_view()
        else:
            self.renderer.ResetCameraClippingRange()
            self._render()

        print(f"[stream] Channel {channel_id} displayed")

    def frame_default_view(self) -> None:
        """Position the camera at the canonical default view of the CURRENT
        volume: top-down (looking along -Z, +Y up), framed to the live world
        bounds and pulled in by INITIAL_CAMERA_ZOOM.

        Computed from the renderer's live bounds every call, so it is correct
        regardless of dataset physical size, spacing, load order, or how the
        camera was oriented beforehand — no dataset-specific magic distance and
        no reliance on a snapshot captured at some earlier moment. Also records
        the result as the reset target for any code that reads `_initial_camera`.
        """
        if not self.renderer:
            return
        cam = self.renderer.GetActiveCamera()
        # Set the canonical direction BEFORE ResetCamera so ResetCamera only
        # fits the distance/centre along it (it preserves the view direction).
        fp = cam.GetFocalPoint()
        cam.SetPosition(fp[0], fp[1], fp[2] + 1.0)  # camera above -> looks down -Z
        cam.SetViewUp(0.0, 1.0, 0.0)
        cam.OrthogonalizeViewUp()
        # Fit the current visible bounds along that direction (dataset-agnostic).
        self.renderer.ResetCamera()
        zoom = getattr(self.__class__, "INITIAL_CAMERA_ZOOM", 1.0)
        if zoom != 1.0 and 0 < zoom <= 1.0:
            pos = list(cam.GetPosition())
            fpz = list(cam.GetFocalPoint())
            cam.SetPosition(*[fpz[i] + (pos[i] - fpz[i]) * zoom for i in range(3)])
        self._initial_camera = {
            "position": list(cam.GetPosition()),
            "focalPoint": list(cam.GetFocalPoint()),
            "viewUp": list(cam.GetViewUp()),
        }
        self.renderer.ResetCameraClippingRange()
        self._render()

    def reset_camera_to_initial(self) -> None:
        """Return the camera to the canonical default view of the current volume
        (top-down, framed to bounds). Recomputed from live bounds each call so it
        is always correct — used by the Reset Camera button and to return to the
        default view after opening a bookmark."""
        self.frame_default_view()

    def channel_same_lod_roi(self, channel_id: int, component: int, roi_dict: dict) -> bool:
        """True if this channel is already shown at the same component and XY ROI (bookmark restore fast path)."""
        roi = ROI(
            x0=int(roi_dict.get("x0", 0)),
            x1=int(roi_dict.get("x1", 1)),
            y0=int(roi_dict.get("y0", 0)),
            y1=int(roi_dict.get("y1", 1)),
        )
        if channel_id not in self._active_channels:
            return False
        if channel_id not in self.state or channel_id not in self.volumes:
            return False
        st = self.state[channel_id]
        return st.component == component and st.roi == roi

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
        if self.channel_same_lod_roi(channel_id, component, roi_dict):
            self.renderer.ResetCameraClippingRange()
            self._render()
            return
        self._load_and_display_channel(channel_id, component, roi, reset_camera=reset_camera)

    def get_active_channels(self) -> set[int]:
        """Return the set of currently active channel IDs."""
        return self._active_channels.copy()

    def set_nov_renderer(self, renderer, render_window) -> None:
        """Set the optional NOV popup renderer/window. When set, clipped volumes are pushed here."""
        self.nov_renderer = renderer
        self.nov_render_window = render_window

    def set_nov_lens_clip(self, center: Tuple[float, float, float], length: float, width: float, depth: float) -> None:
        """Clip NOV popup view to inside lens. Main view stays full. Call sync_nov_volumes() after to update popup."""
        self._nov_lens_clip = (
            (float(center[0]), float(center[1]), float(center[2])),
            float(length), float(width), float(depth),
        )

    def clear_nov_lens_clip(self) -> None:
        """Remove NOV lens clip. Call clear_nov_view() to remove popup volumes."""
        self._nov_lens_clip = None

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

    def _nov_lens_roi_at_component(self, component: int) -> Optional[ROI]:
        """Voxel ROI (x0, x1, y0, y1) that contains the NOV lens in world space at the given component. Adds 2-voxel margin."""
        clip = self._nov_lens_clip
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
        if not self.nov_renderer or not self._nov_lens_clip or not self._active_channels:
            return
        try:
            for ch in list(self._active_channels):
                roi_nov = self._nov_lens_roi_at_component(component)
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
                        prop.SetScalarOpacityUnitDistance(self._opacity_unit_distance())
                    if not self.nov_renderer.HasViewProp(vol):
                        self.nov_renderer.AddVolume(vol)
                except Exception:
                    continue
            self.nov_renderer.ResetCameraClippingRange()
            if self.nov_render_window:
                self.nov_render_window.Render()
            if self.render_callback is not None:
                self.render_callback()
            if self.nov_render_callback is not None:
                try:
                    self.nov_render_callback()
                except Exception:
                    pass
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
                prop.SetScalarOpacityUnitDistance(self._opacity_unit_distance())
            if self.nov_render_window:
                self.nov_render_window.Render()
            if self.render_callback:
                self.render_callback()
            if self.nov_render_callback is not None:
                try:
                    self.nov_render_callback()
                except Exception:
                    pass
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
        if self.nov_render_callback is not None:
            try:
                self.nov_render_callback()
            except Exception:
                pass

    def sync_nov_volumes(self) -> None:
        """Update NOV popup: show first frame at coarsest level for speed, then progressively load comp-1 down to min_component."""
        if not self.nov_renderer or not self._nov_lens_clip or not self._active_channels:
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
        if not self._nov_lens_clip or not self._active_channels:
            return
        try:
            for comp in range(self.cfg.max_component, self.cfg.min_component - 1, -1):
                roi_nov = self._nov_lens_roi_at_component(comp)
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
        if not self.nov_renderer or not self._nov_lens_clip:
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
                        prop.SetScalarOpacityUnitDistance(self._opacity_unit_distance())
                    if not self.nov_renderer.HasViewProp(vol):
                        self.nov_renderer.AddVolume(vol)
                except Exception:
                    continue
            self.nov_renderer.ResetCameraClippingRange()
            if self.nov_render_window:
                self.nov_render_window.Render()
            if self.nov_render_callback is not None:
                try:
                    self.nov_render_callback()
                except Exception:
                    pass
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

    def _roi_is_degenerate(self, roi: ROI) -> bool:
        """True if an ROI is too thin to be a real viewport (a 'needle').
        Uploading such an ROI would blank the volume edge-on (the vanish bug).
        `compute_visible_xy_roi_vox` already clamps to >=1 voxel per axis, so
        this only trips on sub-2-voxel slivers — never a realistic zoomed view.
        """
        return (roi.x1 - roi.x0) < 2 or (roi.y1 - roi.y0) < 2

    def _spacing_for_component(self, component: int) -> SpacingConfig:
        # Derive per-axis spacing from the REAL level-0/level shape ratios rather
        # than assuming 2**component. Level dims aren't exact powers of two (e.g.
        # x: 10908 -> 170 at comp 6, and 170*64 != 10908), so a 2**component scale
        # gives each component a slightly different world extent and the volume
        # visibly shifts when the LOD changes on zoom. Real ratios make every
        # component span the identical physical extent (xdim*sx == x0*base_sx),
        # and they correctly handle both xy-only (z0/zc==1) and isotropic (z halves)
        # pyramids. Dataset-agnostic.
        # Use (dim-1) ratios, not dim ratios: VTK renders a volume's extent as
        # (dim-1)*spacing (voxels are point samples), so to make every LOD span
        # the identical physical extent — and not shift/scale when the LOD changes
        # on zoom — we need (dimc-1)*sx == (dim0-1)*base_sx. This matters most for
        # z on the isotropic Globus store (zc as small as 3): with dim ratios the
        # base's z-extent was (3-1)*sz vs the fine's (24-1)*sz, a big jump.
        try:
            z0, y0, x0 = self._level0_dims_zyx()
            zc, yc, xc = self._dims_for_component(component)
            sx = self.cfg.base_sx * (x0 - 1) / (xc - 1) if (xc > 1 and x0 > 1) else self.cfg.base_sx
            sy = self.cfg.base_sy * (y0 - 1) / (yc - 1) if (yc > 1 and y0 > 1) else self.cfg.base_sy
            sz = self.cfg.base_sz * (z0 - 1) / (zc - 1) if (zc > 1 and z0 > 1) else self.cfg.base_sz
        except Exception:
            scale = float(2 ** component)
            sx, sy, sz = self.cfg.base_sx * scale, self.cfg.base_sy * scale, self.cfg.base_sz
        return SpacingConfig(sx=sx, sy=sy, sz=sz)

    def _opacity_unit_distance(self) -> float:
        """LOD-independent opacity unit distance = the finest (level-0) voxel
        spacing. Opacity-per-distance is a property of the tissue, not of the
        current sampling, so it must NOT use the current LOD's voxel size: for an
        isotropic pyramid that grows at coarse levels and washes the volume out
        (few cells visible across the thickness). Derived from base spacing only,
        so it's consistent across datasets rather than tuned to one store."""
        return max(1e-6, min(self.cfg.base_sx, self.cfg.base_sy, self.cfg.base_sz))

    def _level0_dims_zyx(self) -> Tuple[int, int, int]:
        """Cached level-0 (component 0) dims; used to derive per-level z spacing."""
        dims = getattr(self, "_level0_dims", None)
        if dims is None:
            dims = self._dims_for_component(0)
            self._level0_dims = dims
        return dims

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
        
        pct_bounds = self._channel_percentile_bounds.get(channel_id, (data_range[0], data_range[1], data_range[1]))
        color_tf, opacity_tf = build_tf_with_range(data_range, pct_bounds, range_pct, tint_rgb)
        self._channel_tfs[channel_id] = (color_tf, opacity_tf)
        
        if channel_id in self.volumes:
            vol = self.volumes[channel_id]
            prop = vol.GetProperty()
            prop.SetColor(color_tf)
            prop.SetScalarOpacity(opacity_tf)
        
        self._render()

    # vtkMultiVolume caps at ~10 input ports; refuse extra channels gracefully.
    MAX_BLEND_CHANNELS = 10

    def _get_or_create_volume(self, ch: int) -> Tuple[vtkVolume, vtkGPUVolumeRayCastMapper]:
        """Get or create the channel's vtkVolume and register it as a port on the
        shared multi-volume mapper. The mapper is shared across all channels so
        they blend per-sample; the returned mapper is that shared one."""
        if ch in self.volumes:
            return self.volumes[ch], self._multi_mapper

        prop = vtkVolumeProperty()
        if self.cfg.linear_interpolation:
            prop.SetInterpolationTypeToLinear()
        else:
            prop.SetInterpolationTypeToNearest()
        # vtkMultiVolume does not support per-volume gradient shading; force off.
        prop.ShadeOff()

        vol = vtkVolume()
        # Note: no per-channel mapper — the vtkMultiVolume drives one shared mapper.
        vol.SetProperty(prop)

        self.volumes[ch] = vol
        self.mappers[ch] = self._multi_mapper   # back-compat for callers reading .mappers
        # Registration (port assignment) happens on the channel's first
        # _set_channel_input, once it actually has a texture.
        return vol, self._multi_mapper

    def _new_multi_mapper(self):
        m = vtkGPUVolumeRayCastMapper()
        m.SetAutoAdjustSampleDistances(True)
        if self._blend_mode == "additive":
            m.SetBlendModeToAdditive()
        else:
            m.SetBlendModeToComposite()
        return m

    def _new_multi_volume(self):
        return vtkMultiVolume()

    def _make_dummy_volume(self):
        """A transparent 1^3 volume used to keep the multi-volume at >=2 inputs
        (a lone real volume hits a vtkMultiVolume origin bug -> shifted render)."""
        arr = np.zeros((1, 1, 1), dtype=np.uint16)
        vtk_arr = numpy_to_vtk(arr.ravel(order="C"), deep=True)
        img = vtkImageData()
        img.SetDimensions(1, 1, 1)
        img.SetExtent(0, 0, 0, 0, 0, 0)
        img.SetSpacing(1.0, 1.0, 1.0)
        img.SetOrigin(0.0, 0.0, 0.0)
        img.GetPointData().SetScalars(vtk_arr)
        img.Modified()
        prop = vtkVolumeProperty()
        prop.ShadeOff()
        ctf = vtkColorTransferFunction(); ctf.AddRGBPoint(0.0, 0.0, 0.0, 0.0)
        otf = vtkPiecewiseFunction(); otf.AddPoint(0.0, 0.0)
        prop.SetColor(ctf); prop.SetScalarOpacity(otf)
        vol = vtkVolume(); vol.SetProperty(prop)
        return vol, img

    def _channel_image(self, ch: int) -> Optional["vtkImageData"]:
        return self._current_input.get(ch) or self._base_images.get(ch)

    def _rebuild_multivolume(self) -> None:
        """Rebuild the shared multi-volume from scratch on a membership change
        (channel add/remove — rare, a user action).

        We recreate BOTH the vtkMultiVolume and its GPU mapper rather than
        mutating ports in place: VTK does NOT reliably drop a mapper input port
        (RemoveAllInputs leaves it), which leaves a port with an input but no
        volume and makes vtkMultiVolume abort at render ("Failed to query
        vtkVolume instance for port N"). Verified empirically. Re-setting the
        <=10 active textures here is cheap enough for a rare event. Only channels
        that already have a texture are registered (contiguous ports 0..N-1)."""
        try:
            self.renderer.RemoveVolume(self._multi_volume)
        except Exception:
            pass
        self._multi_mapper = self._new_multi_mapper()
        self._multi_volume = self._new_multi_volume()
        self._multi_volume.SetMapper(self._multi_mapper)
        self.renderer.AddVolume(self._multi_volume)
        self._channel_port.clear()
        ready = [c for c in self.volumes.keys() if self._channel_image(c) is not None]
        if len(ready) > self.MAX_BLEND_CHANNELS:
            print(f"[stream] blend cap {self.MAX_BLEND_CHANNELS}; "
                  f"{len(ready) - self.MAX_BLEND_CHANNELS} channel(s) not rendered")
            ready = ready[:self.MAX_BLEND_CHANNELS]
        for port, ch in enumerate(ready):
            self._multi_volume.SetVolume(self.volumes[ch], port)
            self._multi_mapper.SetInputDataObject(port, self._channel_image(ch))
            self._channel_port[ch] = port
            self.mappers[ch] = self._multi_mapper
        # Keep the multi-volume at >=2 inputs (see _make_dummy_volume): with 0 or
        # 1 real channel, append the transparent dummy so a lone real volume
        # doesn't hit the origin-shift bug.
        if len(ready) <= 1:
            dport = len(ready)
            self._multi_volume.SetVolume(self._dummy_volume, dport)
            self._multi_mapper.SetInputDataObject(dport, self._dummy_image)

    def _set_channel_input(self, ch: int, img: "vtkImageData") -> None:
        """Point a channel's port at a new texture (the per-frame hot path)."""
        self._current_input[ch] = img
        port = self._channel_port.get(ch)
        if port is None:
            # First texture for this channel → it can now be registered.
            if ch in self.volumes:
                self._rebuild_multivolume()
            return
        self._multi_mapper.SetInputDataObject(port, img)

    # ------------------------------------------------------------------
    # Full-volume coarse "base" texture: shown instantly while interacting so
    # the volume is never empty and motion stays cheap; the sharp ROI texture
    # replaces it once the user settles.
    # ------------------------------------------------------------------
    def _ensure_base_image(self, ch: int) -> Optional["vtkImageData"]:
        """Build (once) the whole-volume coarsest-LOD texture for a channel.

        Served from the pinned low-res cache, so this is cheap; it also warms
        that cache for the seed. Returns None if the channel has no data yet.
        """
        img = self._base_images.get(ch)
        if img is not None:
            return img
        comp = self.cfg.max_component
        try:
            _, ydim, xdim = self._dims_for_component(comp)
            arr = self._load_channel_data(comp, ch, ROI(0, xdim, 0, ydim))
            if arr.size == 0 or any(s <= 0 for s in arr.shape):
                return None
            spacing = self._spacing_for_component(comp)
            img = self._create_vtk_image(arr, spacing, (0.0, 0.0, 0.0))
        except Exception as e:
            print(f"[stream] base image build failed ch={ch}: {e}")
            return None
        self._base_images[ch] = img
        return img

    def _display_base_only(self, ch: int) -> bool:
        """Put the full-volume coarse base texture on a channel's mapper (main
        thread). Used for instant, non-blocking channel activation and as the
        while-interacting fallback. Returns True if the base was shown."""
        img = self._ensure_base_image(ch)
        if img is None:
            return False
        vol, mapper = self._get_or_create_volume(ch)
        self._set_channel_input(ch, img)
        if ch in self._channel_tfs:
            color_tf, opacity_tf = self._channel_tfs[ch]
            prop = vol.GetProperty()
            prop.SetColor(color_tf)
            prop.SetScalarOpacity(opacity_tf)
        self._showing_base.add(ch)
        return True

    # How far the interaction texture selector may fire (Hz). The camera has to
    # move a lot to cross the loaded-ROI boundary, so a coarse cadence is plenty
    # and keeps the per-move containment check off the hot path.
    INTERACTION_MOVE_HZ = 15.0

    def _viewport_within_loaded(self, component: int, roi: ROI) -> bool:
        """True when the currently visible XY region fits inside the already-
        loaded `roi` at `component` — i.e. the sharp texture still covers the
        whole viewport, so we can keep showing it during interaction.

        Rotate-in-place and zoom-in keep the viewport inside the loaded ROI (→
        True, stay sharp); zoom-out, a large pan, or rotating to a very oblique
        angle grow it past the ROI (→ False, fall back to the coarse base). The
        loaded ROI already carries `roi_margin_vox` + chunk snapping, so small
        pans stay within the margin without flip-flopping."""
        try:
            spacing = self._spacing_for_component(component)
            _, ydim, xdim = self._dims_for_component(component)
            bounds = self._volume_bounds_world(component)
            vis = compute_visible_xy_roi_vox(
                self.renderer, bounds_world=bounds,
                sx=spacing.sx, sy=spacing.sy, x_dim=xdim, y_dim=ydim,
                margin_vox=0, display_samples=5,
            )
        except Exception:
            return False
        return (vis.x0 >= roi.x0 and vis.x1 <= roi.x1
                and vis.y0 >= roi.y0 and vis.y1 <= roi.y1)

    def _update_interaction_textures(self) -> bool:
        """Point each active channel's port at its SHARP texture while the
        viewport stays inside the loaded ROI, or at its coarse full-volume base
        when the viewport has moved past it. Renders once (interactive) if any
        port changed. Returns whether anything swapped."""
        swapped = False
        for ch in list(self._active_channels):
            if ch not in self._channel_port:
                continue
            st = self.state.get(ch)
            fine = self._fine_images.get(ch)
            base = self._base_images.get(ch)
            keep_fine = (
                st is not None and fine is not None
                and self._viewport_within_loaded(st.component, st.roi)
            )
            if keep_fine:
                target, on_base = fine, False
            elif base is not None:
                target, on_base = base, True
            else:
                continue  # no base yet (nothing better) — leave the port as is
            if self._current_input.get(ch) is not target:
                self._set_channel_input(ch, target)
                swapped = True
            if on_base:
                self._showing_base.add(ch)
            else:
                self._showing_base.discard(ch)
        if swapped:
            try:
                self.renderer.ResetCameraClippingRange()
            except Exception:
                pass
            self._render_interactive()
        return swapped

    def on_interaction_start(self) -> None:
        """Called on StartInteractionEvent (main thread). Keep each channel's
        sharp texture (the viewport still matches the loaded ROI at the very
        start); the per-move handler drops to the coarse base only once an
        interaction actually pushes the viewport past the loaded region."""
        self._update_interaction_textures()

    def on_interaction_move(self) -> None:
        """Called on InteractionEvent (main thread, high frequency). Throttled
        re-evaluation of the sharp/base texture per channel so a swap to the
        coarse base happens the moment a zoom-out or large pan crosses the
        loaded-ROI boundary — and back to sharp if it re-enters."""
        now = time.time()
        if now - self._interaction_move_last < 1.0 / self.INTERACTION_MOVE_HZ:
            return
        self._interaction_move_last = now
        self._update_interaction_textures()

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
    # While interacting we cap the texture much smaller so the GPU upload+render
    # (the only main-thread cost we can't move off-thread) is fast; full res is
    # rebuilt from cached numpy once the user goes idle (see tick_idle).
    INTERACTIVE_MAX_DIM = 512

    def _create_vtk_image(
        self,
        np_vol_zyx: np.ndarray,
        spacing: SpacingConfig,
        origin_xyz: Tuple[float, float, float],
            for_nov_view: bool = False,
            max_dim: Optional[int] = None,
    ) -> vtkImageData:
        """Create vtkImageData from numpy array. When for_nov_view=True and NOV box is set, clip to box (for popup only).
        max_dim caps the largest texture axis (defaults to the OpenGL limit); pass a smaller value for a fast interactive texture."""
        cap = int(max_dim) if max_dim is not None else self.MAX_TEXTURE_DIM
        timer = StageTimer("vtk-image", view=("nov" if for_nov_view else "main"))
        downsampled = False
        with timer.stage("ascontiguous"):
            np_vol_zyx = np.ascontiguousarray(np_vol_zyx, dtype=np.uint16)
        z, y, x = np_vol_zyx.shape
        if x <= 0 or y <= 0 or z <= 0:
            raise ValueError(
                f"Invalid volume shape ({z}, {y}, {x}): cannot create 3D texture. "
                "ROI may be out of bounds for this LOD level."
            )
        sx, sy, sz = spacing.sx, spacing.sy, spacing.sz

        if max(x, y, z) > cap:
            downsampled = True
            with timer.stage("downsample"):
                scale = cap / max(x, y, z)
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
            print(f"[stream] Downsampled volume to ({z},{y},{x}) for texture cap {cap}")

        # Apply NOV lens clip only for NOV popup view (main view always shows full volume)
        clip = getattr(self, "_nov_lens_clip", None)
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

        with timer.stage("numpy_to_vtk"):
            vtk_arr = numpy_to_vtk(np_vol_zyx.ravel(order="C"), deep=True)
            vtk_arr.SetName("scalars")

            img = vtkImageData()
            img.SetDimensions(x, y, z)
            img.SetExtent(0, x - 1, 0, y - 1, 0, z - 1)
            img.SetOrigin(*origin_xyz)
            img.SetSpacing(spacing.sx, spacing.sy, spacing.sz)
            img.GetPointData().SetScalars(vtk_arr)
            img.Modified()

        timer.log(shape=(z, y, x), bytes=fmt_bytes(np_vol_zyx.nbytes),
                  downsampled=downsampled)
        return img

    # ------------------------------------------------------------------
    # Chunk page-table: tile addressing, per-chunk fetch, and assembly.
    # ------------------------------------------------------------------
    def _grid(self, comp: int) -> ChunkGrid:
        """ChunkGrid for a component (lazily built, thread-safe)."""
        g = self._grids.get(comp)
        if g is None:
            with self._grids_lock:
                g = self._grids.get(comp)
                if g is None:
                    g = ChunkGrid(self.zsrc.raw_array(comp))
                    self._grids[comp] = g
        return g

    def _cache_for(self, comp: int) -> ChunkCache:
        """Coarsest-level tiles live in the pinned low-res cache (never evicted by
        fine-tile churn); every finer level uses the main byte-budget LRU."""
        return self._lowres_cache if comp >= self.cfg.max_component else self._chunk_cache

    def _read_tile(self, comp: int, ch: int, cyi: int, cxi: int, grid: ChunkGrid) -> np.ndarray:
        """Fetch one tile's full-z column (one remote request) and cache it.

        In-memory decoded-chunk cache first; on miss, read through the cached
        zarr store (which layers the on-disk compressed CacheStore beneath).
        """
        key = (comp, ch, cyi, cxi)
        cache = self._cache_for(comp)
        hit = cache.get(key)
        if hit is not None:
            return hit
        b = grid.tile_bounds(cyi, cxi)
        arr = np.ascontiguousarray(
            self.zsrc.raw_array(comp)[self.cfg.zarr_time_index, ch, :, b.y0:b.y1, b.x0:b.x1],
            dtype=np.uint16,
        )  # (z, ty, tx)
        cache.put(key, arr)
        self._bytes_fetched += arr.nbytes
        return arr

    def _prefetch_lowres(self, ch: int) -> None:
        """Background-warm the entire coarsest LOD for a channel into the pinned
        low-res cache, so a complete blurry base is instantly available for any
        viewport (zoom-out to the edges, or a newly added channel) without a
        blocking fetch on the interaction path."""
        try:
            comp = self.cfg.max_component
            grid = self._grid(comp)
            full = ROI(0, grid.X, 0, grid.Y)
            for (cyi, cxi) in grid.covering_tiles(full):
                try:
                    self._read_tile(comp, ch, cyi, cxi, grid)
                except Exception:
                    continue
        except Exception:
            pass

    @staticmethod
    def _place(out: np.ndarray, tile: np.ndarray, b: ROI, snapped: ROI) -> None:
        """Copy a tile into its slot in the assembled `out` array."""
        out[:, b.y0 - snapped.y0:b.y1 - snapped.y0,
            b.x0 - snapped.x0:b.x1 - snapped.x0] = tile

    def _place_upsampled(self, out: np.ndarray, up: np.ndarray, b: ROI,
                         f: int, snapped: ROI) -> None:
        """Place a nearest-neighbour-upsampled coarse tile into `out`, clipped
        to the assembled ROI. `b` is the coarse tile's bounds; it maps to the
        fine grid by f = 2**(coarse-comp)."""
        # Fine-grid extent this coarse tile covers.
        fy0, fx0 = b.y0 * f, b.x0 * f
        # Destination window inside `out`, clipped to the snapped ROI.
        dy0 = max(0, fy0 - snapped.y0)
        dx0 = max(0, fx0 - snapped.x0)
        dy1 = min(out.shape[1], fy0 + up.shape[1] - snapped.y0)
        dx1 = min(out.shape[2], fx0 + up.shape[2] - snapped.x0)
        if dy1 <= dy0 or dx1 <= dx0:
            return
        # Corresponding source window inside the upsampled tile.
        sy0 = dy0 - (fy0 - snapped.y0)
        sx0 = dx0 - (fx0 - snapped.x0)
        out[:, dy0:dy1, dx0:dx1] = up[:, sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]

    def _seed_from_coarser(self, out: np.ndarray, comp: int, ch: int,
                           snapped: ROI, grid: ChunkGrid) -> bool:
        """Fill `out` with a COMPLETE blurry base from coarser LODs so no emitted
        frame ever has holes; the centre-out fine tiles later only sharpen it.

        Levels are applied coarsest-first, so finer detail overlays the coarser
        base where it's cached. Full coverage is guaranteed by *fetching* the
        coarsest level's covering tiles when they aren't resident (that level is
        tiny, so this is cheap). Returns True if a complete base was produced;
        False only when `comp` is already the coarsest level (nothing coarser to
        seed from — the caller must then avoid emitting a holey frame).
        """
        covered = False
        for coarse in range(self.cfg.max_component, comp, -1):
            cs = self._roi_at_component(snapped, comp, coarse)
            cg = self._grid(coarse)
            f = 2 ** (coarse - comp)
            guarantee = (coarse == self.cfg.max_component)  # coarsest: ensure full cover
            for (cyi, cxi) in cg.covering_tiles(cg.snap_roi(cs)):
                a = self._cache_for(coarse).get((coarse, ch, cyi, cxi))
                if a is None:
                    if not guarantee:
                        continue          # finer-coarse level: overlay only if cached
                    try:
                        a = self._read_tile(coarse, ch, cyi, cxi, cg)  # cheap coarsest fetch
                    except Exception:
                        continue
                # Upsample the coarse tile in XY by f. For an ISOTROPIC pyramid
                # the coarse level also has fewer z-slices, so resample z to the
                # fine grid too (nearest-neighbour); for an xy-only pyramid the z
                # dims already match and this is a no-op.
                up = np.repeat(np.repeat(a, f, axis=2), f, axis=1)
                if up.shape[0] != out.shape[0]:
                    iz = np.linspace(0, up.shape[0] - 1, out.shape[0]).round().astype(np.intp)
                    up = up[iz]
                self._place_upsampled(out, up, cg.tile_bounds(cyi, cxi), f, snapped)
            if guarantee:
                covered = True
        return covered

    def _chunk_cache_summary(self) -> str:
        """One-line summary of both decoded-chunk caches for profiling logs."""
        s = self._chunk_cache.stats()
        lo = self._lowres_cache.stats()
        return (
            f"chunk-cache[hi:hits={s['hits']} miss={s['misses']} "
            f"rate={s['hit_rate'] * 100:.0f}% evict={s['evictions']} "
            f"entries={s['entries']} size={fmt_bytes(s['bytes'])} | "
            f"lo:entries={lo['entries']} size={fmt_bytes(lo['bytes'])} "
            f"evict={lo['evictions']}] fetched={fmt_bytes(self._bytes_fetched)}]"
        )

    def _disk_cache_summary(self) -> str:
        """One-line summary of on-disk zarr CacheStore state for profiling logs."""
        info = self.zsrc.cache_info()
        stats = self.zsrc.cache_stats()
        if info is None and stats is None:
            return "disk-cache[disabled]"
        parts = []
        if stats is not None:
            parts.append(
                f"hits={stats.get('hits', 0)} misses(remote)={stats.get('misses', 0)} "
                f"rate={stats.get('hit_rate', 0.0) * 100:.0f}% evict={stats.get('evictions', 0)}"
            )
        if info is not None:
            parts.append(
                f"size={fmt_bytes(info.get('current_size', 0))}/"
                f"{fmt_bytes(info.get('max_size', 0))} keys={info.get('cached_keys', 0)}"
            )
        return "disk-cache[" + " ".join(parts) + "]"

    def log_cache_summary(self) -> None:
        """Print a snapshot of both cache layers. Safe to call from anywhere."""
        plog("cache", f"{self._chunk_cache_summary()} {self._disk_cache_summary()}")

    def _assemble_roi(self, component: int, ch: int, snapped: ROI,
                      grid: ChunkGrid) -> np.ndarray:
        """Assemble the snapped (tile-aligned) ROI by fetching its covering
        tiles concurrently and placing them. Used by both the blocking path and
        the progressive stream (which seeds/emits around this)."""
        z = self.zsrc.raw_array(component).shape[2]
        out = np.zeros((z, snapped.y1 - snapped.y0, snapped.x1 - snapped.x0),
                       dtype=np.uint16)
        tiles = grid.covering_tiles(snapped)
        futs = {
            self._chunk_pool.submit(self._read_tile, component, ch, cyi, cxi, grid):
                (cyi, cxi)
            for (cyi, cxi) in tiles
        }
        for fut in as_completed(futs):
            cyi, cxi = futs[fut]
            self._place(out, fut.result(), grid.tile_bounds(cyi, cxi), snapped)
        return out

    def _load_channel_data(self, component: int, ch: int, roi: ROI) -> np.ndarray:
        """Load a channel's data for `roi` (blocking) via the chunk cache.

        Assembles the tile-aligned (snapped) region from concurrent per-chunk
        reads, then crops back to the exact requested `roi` so every existing
        caller (NOV sync/scoring, bookmark restore, per-channel display) keeps
        its original contract: shape == (z, roi_h, roi_w), origin at roi.x0/y0.
        Overlapping ROIs now reuse cached tiles instead of re-downloading.
        """
        timer = StageTimer("load", comp=component, ch=ch,
                           roi=f"({roi.x0}:{roi.x1},{roi.y0}:{roi.y1})")
        grid = self._grid(component)
        snapped = grid.snap_roi(roi)
        with timer.stage("assemble"):
            out = self._assemble_roi(component, ch, snapped, grid)
        # Crop the snapped assembly back to the exact requested ROI.
        y0 = roi.y0 - snapped.y0
        x0 = roi.x0 - snapped.x0
        cropped = np.ascontiguousarray(
            out[:, y0:y0 + (roi.y1 - roi.y0), x0:x0 + (roi.x1 - roi.x0)])
        timer.log(src="CHUNK", bytes=fmt_bytes(cropped.nbytes),
                  shape=tuple(cropped.shape),
                  chunk=self._chunk_cache_summary(), disk=self._disk_cache_summary())
        return cropped

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
            self._set_channel_input(ch, img)

            prop = vol.GetProperty()
            prop.SetColor(color_tf)
            prop.SetScalarOpacity(opacity_tf)
            prop.SetScalarOpacityUnitDistance(self._opacity_unit_distance())

            self.state[ch] = ChannelState(
                component=comp, roi=ROI(0, int(x), 0, int(y)))

            color_name = self.cfg.channel_colors[i % len(
                self.cfg.channel_colors)]
            print(
                f"[stream] added ch={ch} color={color_name} dims=(z={z},y={y},x={x})")

        self._last_component = comp
        self.renderer.ResetCameraClippingRange()
        self.renderer.ResetCamera()
        self._render()

        # Warm the pinned coarsest LOD for these channels in the background.
        for ch in self.cfg.channels:
            VolumeStreamer._executor.submit(self._prefetch_lowres, int(ch))

    # Update rates for the GPU ray-cast mapper (AutoAdjustSampleDistances on):
    # high rate -> coarse sampling -> fast/interactive; low rate -> best quality.
    INTERACTIVE_UPDATE_RATE = 15.0
    STILL_UPDATE_RATE = 0.001

    def _render(self):
        """Trigger render (best quality). Used for one-off main-thread renders."""
        self.render_window.Render()
        if self.render_callback is not None:
            self.render_callback()

    def _render_interactive(self):
        """Fast, lower-quality render so swaps/interactions never stall the UI.

        Flags that a full-quality 'still' render is owed once the user is idle.
        """
        timer = StageTimer("render", kind="interactive")
        try:
            self.render_window.SetDesiredUpdateRate(self.INTERACTIVE_UPDATE_RATE)
        except Exception:
            pass
        with timer.stage("gpu"):
            self.render_window.Render()
        if self.render_callback is not None:
            self.render_callback()
        self._needs_still_render = True
        self._idle_ticks = 0
        timer.log()

    def _render_still(self):
        """Full-quality render. Called from the poll loop after the user goes idle."""
        timer = StageTimer("render", kind="still")
        try:
            self.render_window.SetDesiredUpdateRate(self.STILL_UPDATE_RATE)
        except Exception:
            pass
        with timer.stage("gpu"):
            self.render_window.Render()
        if self.render_callback is not None:
            self.render_callback()
        self._needs_still_render = False
        timer.log()

    def tick_idle(self) -> bool:
        """Main-thread poll-loop hook: once the user goes idle, (1) upgrade any
        interactive (downsized) textures to full resolution, then (2) re-render
        at full quality.

        Returns True if anything was pushed to the client.
        """
        if not self._needs_still_render and not self._capped_channels:
            return False
        # Don't refine while work is pending or a new viewport is queued.
        with self._req_lock:
            busy = self._inflight or self._latest_request is not None
        if busy:
            self._idle_ticks = 0
            return False
        self._idle_ticks += 1
        if self._idle_ticks < 3:  # ~300ms of quiet at the 100ms poll cadence
            return False

        # Step 1: full-resolution upgrade. Build off the main thread from cached
        # numpy; the resulting hires frames are applied by check_and_apply, which
        # clears _capped_channels and re-arms the still render.
        if self._capped_channels and not self._hires_inflight:
            self._hires_inflight = True
            with self._req_lock:
                gen = self._max_gen
            VolumeStreamer._executor.submit(self._build_hires_upgrade, gen)
            self._idle_ticks = 0
            return False
        if self._capped_channels:
            return False  # upgrade in flight; wait for it before the still render

        # Step 2: full-quality render once textures are full-res.
        if self._needs_still_render:
            self._render_still()
            return True
        return False

    def _emit_hires(self, ch: int, st: ChannelState, out: np.ndarray, gen: int) -> None:
        """Build a full-resolution vtkImageData from the assembled array and
        enqueue it as a hires frame (origin from the snapped ROI)."""
        spacing = self._spacing_for_component(st.component)
        origin_xyz = (st.roi.x0 * spacing.sx, st.roi.y0 * spacing.sy, 0.0)
        try:
            img = self._create_vtk_image(out, spacing, origin_xyz)  # full res (2048 cap)
        except ValueError:
            return
        self._loaded_data_queue.put(LoadedData(
            component=st.component, roi=st.roi,
            channel_images={int(ch): img},
            timestamp=time.time(), gen=gen, hires=True,
        ))

    def _build_hires_upgrade(self, gen: int) -> None:
        """Worker thread: rebuild full-resolution vtkImageData for the channels
        currently shown downsized, re-assembling from the chunk cache CENTRE-OUT
        so the sharp centre lands before the periphery. Full-res re-uploads are
        expensive, so re-uploads are bounded (centre-half, then full) rather than
        per-tile. Channels with tiles not yet resident are emitted at whatever
        coverage they reached and retried on the next idle tick.
        """
        try:
            for ch in list(self._capped_channels):
                if self._is_superseded_gen(gen):
                    break
                st = self.state.get(int(ch))
                if st is None:
                    continue
                grid = self._grid(st.component)
                out = np.zeros(
                    (self.zsrc.raw_array(st.component).shape[2],
                     st.roi.y1 - st.roi.y0, st.roi.x1 - st.roi.x0),
                    dtype=np.uint16)
                # Complete blurry base so any not-yet-placed tile isn't a hole.
                covered = self._seed_from_coarser(out, st.component, ch, st.roi, grid)

                tiles = grid.covering_tiles(st.roi)   # centre-out
                n = len(tiles)
                # Bounded re-uploads: centre-half + full. Skip the half-way emit
                # if we have no complete base (it would show holes); emit only
                # the final full frame in that case.
                emit_at = {max(1, n // 2), n} if covered else {n}
                for i, (cyi, cxi) in enumerate(tiles, start=1):
                    if self._is_superseded_gen(gen):
                        break
                    self._place(out, self._read_tile(st.component, ch, cyi, cxi, grid),
                                grid.tile_bounds(cyi, cxi), st.roi)
                    if i in emit_at and not self._is_superseded_gen(gen):
                        self._emit_hires(ch, st, out, gen)
        except Exception as e:
            import traceback
            print(f"[hires] upgrade error: {e}")
            traceback.print_exc()
        finally:
            self._hires_inflight = False

    def _is_superseded_gen(self, gen: int) -> bool:
        with self._req_lock:
            return self._max_gen > gen

    def _apply_loaded_data_on_main_thread(self, loaded: LoadedData):
        """
        Swap pre-built vtkImageData onto the mappers and render.
        MUST be called on main thread where OpenGL context exists.

        Cheap by design: the numpy->vtk conversion already happened in the
        worker, so this only does SetInputData + transfer-function. It does NOT
        render — the drain loop renders once after applying everything queued
        (see check_and_apply_loaded_data), so N per-channel frames cost one
        render, not N.
        """
        component = loaded.component
        roi = loaded.roi
        spacing = self._spacing_for_component(component)

        timer = StageTimer("apply-main", comp=component,
                           channels=len(loaded.channel_images))

        for ch, img in loaded.channel_images.items():
            # A late frame for a channel the user has since deactivated must not
            # resurrect it (that would desync the multi-volume ports).
            if ch not in self._active_channels:
                continue
            vol, mapper = self._get_or_create_volume(ch)
            with timer.stage(f"gpu-upload[ch{ch}]"):
                self._set_channel_input(ch, img)
            # Retain this sharp texture so an interaction can toggle the port
            # back to it (fine<->base) without re-streaming.
            self._fine_images[ch] = img

            if ch in self._channel_tfs:
                color_tf, opacity_tf = self._channel_tfs[ch]
                prop = vol.GetProperty()
                prop.SetColor(color_tf)
                prop.SetScalarOpacity(opacity_tf)
                prop.SetScalarOpacityUnitDistance(self._opacity_unit_distance())

            self.state[ch] = ChannelState(component=component, roi=roi)

            # A sharp ROI texture is now on this channel: it's no longer showing
            # the coarse full-volume base.
            self._showing_base.discard(ch)

            # Track which channels are showing a downsized (interactive) texture
            # so the idle loop knows what to upgrade to full resolution.
            if loaded.hires or not loaded.was_capped:
                self._capped_channels.discard(ch)
            else:
                self._capped_channels.add(ch)

        self._last_component = component

        # Latency from when the user finished interacting to this swap.
        latency = time.time() - loaded.timestamp
        timer.log(end_to_end_latency=f"{latency * 1000:.0f}ms",
                  hires=loaded.hires, capped=loaded.was_capped)

    # ------------------------------------------------------------------
    # Latest-wins async loader
    #
    # Flow (all main-thread hops driven by app.py's 100ms poll loop):
    #   on_interaction_end  -> stores newest LoadRequest (_set_latest_request)
    #   service_loads       -> debounces + dispatches one load at a time
    #   _background_load     -> worker: builds vtkImageData, coarse->fine,
    #                          bails early if a newer request arrives
    #   check_and_apply_loaded_data -> drains queue, drops stale frames, swaps
    # ------------------------------------------------------------------

    def _is_superseded(self, request: LoadRequest) -> bool:
        """True if a newer request has been created since `request`."""
        with self._req_lock:
            return self._max_gen > request.gen

    def _roi_at_component(self, roi_target: ROI, target_comp: int, comp: int) -> ROI:
        """Scale a target-component ROI to another component's voxel grid."""
        if comp == target_comp:
            return roi_target
        _, ydim, xdim = self._dims_for_component(comp)
        d = scale_roi_to_component(
            {"x0": roi_target.x0, "x1": roi_target.x1,
             "y0": roi_target.y0, "y1": roi_target.y1},
            target_comp, comp, x_dim=xdim, y_dim=ydim,
        )
        return ROI(d["x0"], d["x1"], d["y0"], d["y1"])

    def _emit(self, request: LoadRequest, comp: int, ch: int, snapped: ROI,
              out: np.ndarray) -> None:
        """Build a capped (interactive) vtkImageData off-thread from the current
        assembly and enqueue it. Origin/extent come from the SNAPPED ROI so
        channels stay aligned in world space. `was_capped` flags frames the idle
        loop should later re-upload at full resolution."""
        spacing = self._spacing_for_component(comp)
        origin_xyz = (snapped.x0 * spacing.sx, snapped.y0 * spacing.sy, 0.0)
        was_capped = max(out.shape) > self.INTERACTIVE_MAX_DIM
        try:
            img = self._create_vtk_image(
                out, spacing, origin_xyz, max_dim=self.INTERACTIVE_MAX_DIM)
        except ValueError:
            return
        self._loaded_data_queue.put(LoadedData(
            component=comp, roi=snapped, channel_images={ch: img},
            timestamp=request.timestamp, gen=request.gen, was_capped=was_capped))

    def _stream_channel(self, request: LoadRequest, comp: int, ch: int,
                        snapped: ROI, grid: ChunkGrid) -> None:
        """Assemble one channel's snapped ROI progressively (worker thread):
        seed a blurry base from a coarser LOD, paint already-resident tiles and
        emit immediately, then fetch missing tiles CENTRE-OUT and re-emit
        throttled as they land. On supersede it returns at once; tiles already
        fetched stay in the cache (not wasted)."""
        try:
            z = self.zsrc.raw_array(comp).shape[2]
            out = np.zeros((z, snapped.y1 - snapped.y0, snapped.x1 - snapped.x0),
                           dtype=np.uint16)
            covered = self._seed_from_coarser(out, comp, ch, snapped, grid)

            missing = []
            cache = self._cache_for(comp)
            for (cyi, cxi) in grid.covering_tiles(snapped):   # centre-out
                a = cache.get((comp, ch, cyi, cxi))
                if a is not None:
                    self._place(out, a, grid.tile_bounds(cyi, cxi), snapped)
                else:
                    missing.append((cyi, cxi))

            if self._is_superseded(request):
                return
            # Only paint a frame that is COMPLETE (a full blurry base, or every
            # tile already resident). Otherwise keep the current texture on
            # screen so the volume never blanks or shows holes; we'll emit once
            # the final full frame is assembled. This is what makes it "fill
            # everything, then sharpen centre-out" instead of flickering.
            complete = covered or not missing
            if complete:
                self._emit(request, comp, ch, snapped, out)

            if not missing:
                return

            futs = {}
            for (cyi, cxi) in missing:
                if self._is_superseded(request):
                    break
                # Page table records residency (also the mixed-res hook); one
                # request is active at a time so each key is submitted once.
                self._page_table.mark_inflight((comp, ch, cyi, cxi), request.gen, comp)
                futs[self._chunk_pool.submit(
                    self._read_tile, comp, ch, cyi, cxi, grid)] = (cyi, cxi)

            last = 0.0
            for fut in as_completed(futs):
                cyi, cxi = futs[fut]
                try:
                    arr = fut.result()
                except Exception:
                    continue
                self._page_table.mark_resident((comp, ch, cyi, cxi), request.gen, comp)
                if self._is_superseded(request):
                    return   # fetched tiles remain cached; not wasted
                self._place(out, arr, grid.tile_bounds(cyi, cxi), snapped)
                # Throttled sharpening emits are only safe once we have a
                # complete base (each is still a hole-free frame). Without one
                # (comp is the coarsest level) we wait for the final full frame.
                if complete:
                    now = time.time()
                    # Motion already shows the coarse base, so the fine texture
                    # only needs to sharpen as it settles: emit sparingly (~7 Hz)
                    # to keep main-thread GPU uploads down.
                    if now - last >= 0.15:
                        self._emit(request, comp, ch, snapped, out)
                        last = now
            if not self._is_superseded(request):
                self._emit(request, comp, ch, snapped, out)   # final complete frame
        except Exception as e:
            import traceback
            print(f"[stream] channel {ch} error: {e}")
            traceback.print_exc()

    def _background_load(self, request: LoadRequest):
        """Worker thread: load coarse->fine for the request, building VTK images
        off the main thread and enqueuing each stage for a cheap main-thread swap.
        Bails out as soon as a newer request supersedes this one.
        """
        try:
            target = request.component
            cur = self._last_component
            # Progressive: when changing resolution, show a cheap coarse version
            # of the new viewport first, then refine to the target. Plain pans at
            # the same component skip the coarse stage, and so does the case where
            # what's already on screen is at least as coarse as the intermediate
            # (it already serves as the placeholder).
            do_progressive = (cur is None) or (target != cur)
            stages: List[int] = []
            if do_progressive and target <= self.cfg.max_component - 2:
                inter = min(self.cfg.max_component, target + 2)
                if inter != target and (cur is None or inter < cur):
                    stages.append(inter)
            stages.append(target)

            for idx, stage_comp in enumerate(stages):
                if self._is_superseded(request):
                    break
                stage_roi = self._roi_at_component(request.roi, target, stage_comp)
                grid = self._grid(stage_comp)
                snapped = grid.snap_roi(stage_roi)
                channels = [int(c) for c in list(self._active_channels)]
                if not channels:
                    break
                timer = StageTimer("background-load", comp=stage_comp, gen=request.gen)
                queue_wait = time.time() - request.timestamp

                # Stream all channels concurrently; each _stream_channel seeds,
                # paints resident tiles, and emits progressively centre-out so a
                # fast cached channel paints immediately and the centre sharpens
                # first. We only await here for lifecycle (emits happen inside).
                futures = {
                    self._channel_executor.submit(
                        self._stream_channel, request, stage_comp, ch, snapped, grid): ch
                    for ch in channels
                }
                for fut in as_completed(futures):
                    if self._is_superseded(request):
                        break
                    try:
                        fut.result()
                    except Exception:
                        pass

                timer.log(channels=len(channels),
                          stage=f"{idx + 1}/{len(stages)}",
                          queue_wait=f"{queue_wait * 1000:.0f}ms",
                          chunk=self._chunk_cache_summary())
                if self._is_superseded(request):
                    break
        except Exception as e:
            import traceback
            print(f"[background] load error: {e}")
            traceback.print_exc()
        finally:
            with self._req_lock:
                self._inflight = False

    def _set_latest_request(self, component: int, roi: ROI) -> None:
        """Record the newest desired viewport; the poll loop dispatches it."""
        with self._req_lock:
            self._max_gen += 1
            self._latest_request = LoadRequest(
                component=component, roi=roi, timestamp=time.time(), gen=self._max_gen)

    def _schedule_load(self, request: LoadRequest):
        """Compat shim for callers that build a LoadRequest directly
        (e.g. _trigger_lod_update_for_new_channel): route through latest-wins."""
        self._set_latest_request(request.component, request.roi)

    def _displayed_matches(self, req: LoadRequest) -> bool:
        """True if every active channel is already shown at req's comp and ROI.

        The interactive loader stores the tile-aligned (snapped) ROI in state, so
        compare against the snapped request ROI or an unchanged viewport would be
        seen as different and reloaded every settle.
        """
        if not self._active_channels:
            return False
        try:
            target = self._grid(req.component).snap_roi(req.roi)
        except Exception:
            target = req.roi
        for ch in self._active_channels:
            st = self.state.get(int(ch))
            if st is None or st.component != req.component or st.roi != target:
                return False
        return True

    def service_loads(self) -> None:
        """Main-thread poll-loop hook: debounce and dispatch at most one load.

        Single-flight + latest-wins: only the newest request is ever dispatched,
        and only once the current load finishes, so a backlog can't build up.
        """
        with self._req_lock:
            if self._inflight or self._latest_request is None:
                return
            req = self._latest_request
            if (time.time() - req.timestamp) < self.DEBOUNCE_DELAY:
                return  # still settling; wait for quiet
            if self._displayed_matches(req):
                self._latest_request = None
                return
            self._latest_request = None
            self._inflight = True
        VolumeStreamer._executor.submit(self._background_load, req)

    def check_and_apply_loaded_data(self):
        """Main thread: drain finished loads and swap them in.

        Frames older than the newest generation are dropped (never flash a stale
        viewport). Frames are also COALESCED to the newest one per channel, so a
        burst of progressive intermediates costs one GPU upload per channel per
        tick instead of N — the single-thread server can't afford N big uploads.
        """
        latest: Dict[int, LoadedData] = {}
        while True:
            try:
                loaded = self._loaded_data_queue.get_nowait()
            except queue.Empty:
                break
            with self._req_lock:
                stale = loaded.gen < self._max_gen
            if stale:
                continue
            # Queue is FIFO, so later frames are newer/higher-quality (more tiles,
            # or the idle hires upgrade); last write per channel wins.
            for ch, img in loaded.channel_images.items():
                latest[ch] = LoadedData(
                    component=loaded.component, roi=loaded.roi,
                    channel_images={ch: img}, timestamp=loaded.timestamp,
                    gen=loaded.gen, was_capped=loaded.was_capped, hires=loaded.hires)
        if not latest:
            return False
        for ld in latest.values():
            self._apply_loaded_data_on_main_thread(ld)
        # One render for the whole batch of swaps (keeps the event loop free).
        self.renderer.ResetCameraClippingRange()
        self._render_interactive()
        return True

    def on_interaction_end(self) -> int:
        """
        Called on EndInteractionEvent (main thread). Records the new desired
        viewport for the async loader and returns the desired component (for
        heatmap LOD to consume). Does NOT block on loading or full-quality render.
        """
        # Defensive: some zoom/scroll interactions can push the camera clipping
        # range into an invalid state (everything clipped => black screen).
        # Reset it, but do NOT render here — the interactor already rendered this
        # frame, and rendering again on every scroll-stop is pure main-thread
        # downtime. Instead flag a full-quality repaint for the idle loop, which
        # fires ~300ms after the user stops and also reapplies the clipping fix.
        try:
            self.renderer.ResetCameraClippingRange()
            self._needs_still_render = True
            self._idle_ticks = 0
        except Exception:
            pass

        cam = self.renderer.GetActiveCamera()
        dist = camera_distance_to_focal(cam)
        try:
            import math
            if dist is None or (isinstance(dist, (int, float)) and (not math.isfinite(dist) or dist <= 1e-6)):
                # If distance becomes degenerate, recover by resetting camera.
                self.renderer.ResetCamera()
                self.renderer.ResetCameraClippingRange()
                self._render_interactive()
                dist = camera_distance_to_focal(self.renderer.GetActiveCamera())
        except Exception:
            pass

        desired_comp = choose_component(
            dist,
            self.cfg.distance_rules,
            min_component=self.cfg.min_component,
            max_component=self.cfg.max_component,
        )

        if not self._active_channels:
            return desired_comp

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

        # Safety net: never replace the live texture with a degenerate (needle)
        # ROI — that would blank the volume (the edge-on vanish bug). Keep the
        # currently displayed frame instead.
        if self._roi_is_degenerate(roi):
            print(f"[interaction] skipping degenerate ROI {roi} (keeping current frame)")
            return desired_comp

        request = LoadRequest(component=desired_comp, roi=roi, timestamp=time.time())
        if self._displayed_matches(request):
            return desired_comp

        self._set_latest_request(desired_comp, roi)
        return desired_comp
