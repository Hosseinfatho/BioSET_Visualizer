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


@dataclass
class HeatmapResult:
    level: int
    field: object                 # analysis.HeatmapField or None


class HeatmapLOD:
    """Drives automatic heatmap cell-size selection based on camera zoom."""

    DEBOUNCE_DELAY = 0.15

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

    def on_camera_moved(self, distance: float):
        """Called from the EndInteractionEvent handler (main thread).
        Schedules a background heatmap recompute if the level should change."""
        if not self._auto_mode:
            return
        if self._loader is None or not self._channels:
            return

        desired_level = choose_heatmap_level(distance, self._distance_rules)
        if desired_level == self._current_level:
            return

        req = HeatmapRequest(
            level=desired_level,
            loader=self._loader,
            channels=list(self._channels),
            dilation=self._dilation,
            timestamp=time.monotonic(),
        )

        with self._debounce_lock:
            self._pending = req

        threading.Thread(target=self._debounce_cb, args=(req,), daemon=True).start()
        print(f"[heatmap_lod] Level change queued: {self._current_level} -> {desired_level}")

    def _debounce_cb(self, req: HeatmapRequest):
        time.sleep(self.DEBOUNCE_DELAY)
        with self._debounce_lock:
            if self._pending and self._pending.timestamp == req.timestamp:
                self._pending = None
                self._executor.submit(self._bg_load, req)

    def _bg_load(self, req: HeatmapRequest):
        """Runs in worker thread: compute the field from the loader."""
        print(f"[heatmap_lod] Computing level {req.level} for {req.channels}")
        try:
            field = req.loader.get_heatmap_field(
                channels=req.channels,
                dilation=req.dilation,
                hierarchy_level=req.level,
            )
            n = field.counts.size if field is not None else 0
            print(f"[heatmap_lod] Level {req.level}: {n} cells computed")
            self._queue.put(HeatmapResult(level=req.level, field=field))
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
