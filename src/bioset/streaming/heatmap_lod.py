"""Automatic heatmap LOD (cell size) selection driven by camera distance.

Threading model mirrors VolumeStreamer:
  - on_camera_moved() called on main thread → debounce → executor
  - _bg_load() runs in a worker thread, computing the heatmap field from the
    analysis loader (zarr EDT reads + reductions; the loader is thread-safe)
  - check_and_apply() called from the main-thread asyncio poll loop
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor

from .lod import choose_heatmap_level


@dataclass
class HeatmapRequest:
    level: int
    loader: object                # AnalysisLoader (read-only, internally locked)
    channels: list[str]
    dilation: float
    timestamp: float
    roi_vox: object = None        # (x0, x1, y0, y1) viewport in voxels, or None


@dataclass
class HeatmapResult:
    level: int
    field: object                 # analysis.HeatmapField or None
    roi_vox: object = None        # viewport ROI the field was cropped for
    cropped: bool = False


class HeatmapLOD:
    """Drives automatic heatmap cell-size selection based on camera zoom.

    At the fine levels (`CROP_LEVELS`) the computed field is cropped to the
    visible viewport expanded by `CROP_MARGIN_FRAC` per side — off-screen
    glyph instances are pure render cost, and fine levels only activate when
    zoomed in. A recompute is triggered when the camera pans/zooms beyond
    half the crop margin.
    """

    DEBOUNCE_DELAY = 0.15
    CROP_LEVELS = (0, 1)
    CROP_MARGIN_FRAC = 1.0  # expand viewport by 100% of its extent per side

    def __init__(self, distance_rules=None):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="heatmap_lod")
        self._queue: queue.Queue[HeatmapResult] = queue.Queue()
        self._debounce_lock = threading.Lock()
        self._pending: Optional[HeatmapRequest] = None
        self._current_level: int = 3

        self._loader = None
        self._channel_order: List[str] = []
        self._z_depth: int = 1
        self._channels: List[str] = []
        self._dilation: float = 0.0

        # Crop bookkeeping: viewport ROI (voxels) of the last applied field and
        # whether that field was actually cropped.
        self._last_roi_vox = None
        self._last_cropped = False

        # Distance rules from config: [(distance_threshold, level), ...]
        self._distance_rules = distance_rules or (
            (1000.0, 3),
            (300.0,  2),
            (120.0,  1),
            (-100.0, 0),
        )
        self._auto_mode: bool = True  # controlled by UI toggle

    def set_analysis(self, loader, channel_order: List[str], z_depth: int):
        """Called after analysis is loaded. `loader` is the AnalysisLoader."""
        self._loader = loader
        self._channel_order = channel_order
        self._z_depth = z_depth
        self._current_level = 3
        print(f"[heatmap_lod] Analysis context set (zarr backend), "
              f"{len(channel_order)} channels, z_depth={z_depth}")

    def clear_analysis(self):
        """Called when analysis is cleared."""
        self._loader = None
        self._channels = []
        self._channel_order = []

    def update_channels(self, channel_names: List[str]):
        """Update the list of active channel names."""
        self._channels = list(channel_names)

    def update_dilation(self, dilation: float):
        """Update current radius so the next load uses the correct value."""
        self._dilation = dilation

    def set_auto_mode(self, enabled: bool):
        """Enable or disable automatic level selection."""
        self._auto_mode = enabled
        print(f"[heatmap_lod] Auto mode: {enabled}")

    # ── Camera movement ──

    def _roi_within_last(self, roi_vox) -> bool:
        """Is the new viewport ROI still inside the last crop's inner margin?"""
        if self._last_roi_vox is None:
            return False
        lx0, lx1, ly0, ly1 = self._last_roi_vox
        mx = (lx1 - lx0) * self.CROP_MARGIN_FRAC * 0.5
        my = (ly1 - ly0) * self.CROP_MARGIN_FRAC * 0.5
        x0, x1, y0, y1 = roi_vox
        return (x0 >= lx0 - mx and x1 <= lx1 + mx
                and y0 >= ly0 - my and y1 <= ly1 + my)

    def note_applied_crop(self, roi_vox, cropped: bool):
        """Record the crop state of a field applied outside this class
        (the synchronous update_heatmap path)."""
        self._last_roi_vox = roi_vox
        self._last_cropped = bool(cropped)

    def on_camera_moved(self, distance: float, roi_vox=None):
        """Called from the EndInteractionEvent handler (main thread).
        Schedules a background heatmap recompute when the level should change,
        or when a cropped fine-level field no longer covers the viewport."""
        if not self._auto_mode:
            return
        if self._loader is None or not self._channels:
            return

        desired_level = choose_heatmap_level(distance, self._distance_rules)
        need = desired_level != self._current_level
        if not need and desired_level in self.CROP_LEVELS and roi_vox is not None:
            # Same level, but a cropped field may have been panned/zoomed out of.
            need = self._last_cropped and not self._roi_within_last(roi_vox)
        if not need:
            return

        req = HeatmapRequest(
            level=desired_level,
            loader=self._loader,
            channels=list(self._channels),
            dilation=self._dilation,
            timestamp=time.monotonic(),
            roi_vox=roi_vox if desired_level in self.CROP_LEVELS else None,
        )

        with self._debounce_lock:
            self._pending = req

        threading.Thread(target=self._debounce_cb, args=(req,), daemon=True).start()
        print(f"[heatmap_lod] Recompute queued: level {self._current_level} -> "
              f"{desired_level}{' (crop)' if req.roi_vox else ''}")

    def _debounce_cb(self, req: HeatmapRequest):
        time.sleep(self.DEBOUNCE_DELAY)
        with self._debounce_lock:
            if self._pending and self._pending.timestamp == req.timestamp:
                self._pending = None
                self._executor.submit(self._bg_load, req)

    def _bg_load(self, req: HeatmapRequest):
        """Runs in worker thread: compute the field from the loader, cropped
        to the viewport at fine levels."""
        print(f"[heatmap_lod] Computing level {req.level} for {req.channels}")
        try:
            field = req.loader.get_heatmap_field(
                channels=req.channels,
                dilation=req.dilation,
                hierarchy_level=req.level,
            )
            cropped = False
            if field is not None and req.roi_vox is not None:
                from bioset.analysis import crop_field_to_roi
                sub = crop_field_to_roi(field, req.roi_vox, self.CROP_MARGIN_FRAC)
                cropped = sub is not field
                field = sub
            n = field.counts.size if field is not None else 0
            print(f"[heatmap_lod] Level {req.level}: {n} cells"
                  f"{' (viewport-cropped)' if cropped else ''}")
            self._queue.put(HeatmapResult(
                level=req.level, field=field, roi_vox=req.roi_vox, cropped=cropped))
        except Exception as e:
            import traceback
            print(f"[heatmap_lod] Background compute failed: {e}")
            traceback.print_exc()

    # ── Main thread ──

    def check_and_apply(self, heatmap_renderer, state) -> bool:
        """Drain results and apply the newest one. Returns True if the scene
        changed (caller flushes state and updates the view)."""
        result = None
        while True:
            try:
                result = self._queue.get_nowait()
            except queue.Empty:
                break

        if result is None:
            return False

        self._current_level = result.level
        self._last_roi_vox = result.roi_vox
        self._last_cropped = result.cropped
        state.current_hierarchy_level = result.level

        if not getattr(state, "heatmap_visible", True):
            print(f"[heatmap_lod] Level {result.level} ready but heatmap hidden — discarded")
            return False

        if heatmap_renderer is None:
            return False

        spacing = (
            getattr(state, "physical_size_x", None) or 1.0,
            getattr(state, "physical_size_y", None) or 1.0,
            getattr(state, "physical_size_z", None) or 1.0,
        )
        from bioset.scene.heatmap import hex_to_rgb
        color = hex_to_rgb(getattr(state, "heatmap_color", "#FFFFFF"))
        outline_only = getattr(state, "heatmap_outline_only", "filled") == "outline"

        heatmap_renderer.update_field(
            result.field, spacing=spacing, color=color, outline_only=outline_only,
        )
        state.heatmap_tile_count = heatmap_renderer.tile_count
        print(f"[heatmap_lod] Applied level {result.level}: "
              f"{heatmap_renderer.tile_count} cells")
        return True

    @property
    def current_level(self) -> int:
        return self._current_level
