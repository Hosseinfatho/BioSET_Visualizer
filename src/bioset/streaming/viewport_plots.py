"""Background computation of plot metrics restricted to viewport-visible blocks.

Viewport ranges arrive in 128-voxel block units (the tally block size), matching
`tally.parquet`'s block_y/block_x. At tallied radii the numbers come straight
from the block tally / channel_stats (exact); at arbitrary radii they are
computed from the EDT field restricted to the viewport.
"""
from __future__ import annotations

import queue
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ViewportPlotRequest:
    tile_x_range: tuple[int, int]  # (block_x0, block_x1) inclusive/exclusive
    tile_y_range: tuple[int, int]  # (block_y0, block_y1) inclusive/exclusive
    loader: object                 # AnalysisLoader (read-only, internally locked)
    channels: list[str]
    dilation: float
    min_channels: int
    timestamp: float
    # Per-plot flags: only compute what is needed
    need_bar: bool = True
    need_upset: bool = True
    need_dilation: bool = True


@dataclass
class ViewportPlotResult:
    bar_data: list  # [(channel, density_pct), ...]
    upset_data: list  # [{"channels": [...], "iou": float, "overlap_coeff": float}, ...]
    dilation_data: dict  # {key: [{"dilation": float, ...}]}
    timestamp: float


def _run_viewport_queries(req: ViewportPlotRequest) -> ViewportPlotResult:
    """Compute viewport plot data on a worker thread via the analysis loader.

    Only computes the plot types flagged in the request.
    """
    loader = req.loader
    by_range = req.tile_y_range
    bx_range = req.tile_x_range

    bar_data = []
    upset_data = []
    dilation_data = {}

    if req.need_bar or req.need_upset:
        metrics = loader.get_viewport_metrics(
            by_range=by_range,
            bx_range=bx_range,
            dilation=req.dilation,
            min_channels=req.min_channels,
            limit=200,
        )
        if req.need_bar:
            bar_data = metrics["bar"]
        if req.need_upset:
            upset_data = metrics["upset"]

    if req.need_dilation and req.channels:
        dilation_data = loader.get_viewport_dilation_curves(
            by_range=by_range,
            bx_range=bx_range,
            channels=req.channels,
        )

    return ViewportPlotResult(
        bar_data=bar_data,
        upset_data=upset_data,
        dilation_data=dilation_data,
        timestamp=req.timestamp,
    )


class ViewportPlotComputer:
    """Async viewport-local plot computation with debounce + thread pool.

    Debouncing is driven by the main-thread poll loop (check_and_apply is
    called every ~100 ms).  on_camera_moved() just stores the latest request
    with a timestamp — no threads are spawned.  When check_and_apply() sees
    a pending request that has been quiet for DEBOUNCE_SECONDS, it submits
    the work to the executor.  This keeps the VTK interactor callback
    completely lightweight so interaction is never blocked.
    """

    DEBOUNCE_SECONDS = 0.80  # wait 800 ms of quiet before computing

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viewport_plots")
        self._queue: queue.Queue[ViewportPlotResult] = queue.Queue()

        # Pending request (set by on_camera_moved, consumed by check_and_apply)
        self._pending: Optional[ViewportPlotRequest] = None
        self._computing: bool = False  # True while a bg job is running

        self._loader = None
        self._channel_order: List[str] = []
        self._z_depth: int = 1

        self._channels: List[str] = []          # active channels (for dilation curves + filtering)
        self._active_channels: List[str] = []   # active channel names (for filtering viewport results)
        self._dilation: float = 0.0
        self._min_channels: int = 2
        self._enabled: bool = False

        # Per-plot flags: which plot types need viewport computation
        self._need_bar: bool = False
        self._need_upset: bool = False
        self._need_dilation: bool = False

    def set_analysis(self, loader, channel_order: List[str], z_depth: int):
        """Called after analysis is loaded. `loader` is the AnalysisLoader."""
        self._loader = loader
        self._channel_order = channel_order
        self._z_depth = z_depth
        print("[viewport_plots] Analysis context set (zarr backend)")

    def clear_analysis(self):
        """Called when analysis is cleared."""
        self._loader = None
        self._channels = []
        self._active_channels = []
        self._channel_order = []

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if not enabled:
            self._pending = None
        print(f"[viewport_plots] Enabled: {enabled}")

    def update_channels(self, channel_names: List[str]):
        self._channels = list(channel_names)

    def update_active_channels(self, channel_names: List[str]):
        """Set active channel names used for dilation curves and filtering viewport results."""
        self._active_channels = list(channel_names)
        self._channels = list(channel_names)

    def update_dilation(self, dilation: float):
        self._dilation = dilation

    def update_min_channels(self, min_ch: int):
        self._min_channels = min_ch

    def update_needed_plots(self, need_bar: bool, need_upset: bool, need_dilation: bool):
        """Set which plot types need viewport computation."""
        self._need_bar = need_bar
        self._need_upset = need_upset
        self._need_dilation = need_dilation

    # ── Camera move trigger (lightweight — no threads) ──

    def on_camera_moved(self, tile_x_range: tuple[int, int], tile_y_range: tuple[int, int]):
        """Called from main thread on EndInteractionEvent.  Just stores the
        request; the poll loop will submit it after the debounce period."""
        if not self._enabled or self._loader is None:
            return
        if tile_x_range[1] <= tile_x_range[0] or tile_y_range[1] <= tile_y_range[0]:
            return

        self._pending = ViewportPlotRequest(
            tile_x_range=tile_x_range,
            tile_y_range=tile_y_range,
            loader=self._loader,
            channels=list(self._channels),
            dilation=self._dilation,
            min_channels=self._min_channels,
            timestamp=time.monotonic(),
            need_bar=self._need_bar,
            need_upset=self._need_upset,
            need_dilation=self._need_dilation,
        )

    def _bg_compute(self, req: ViewportPlotRequest):
        """Worker thread: run loader queries, enqueue result."""
        try:
            result = _run_viewport_queries(req)
            self._queue.put(result)
            n_blocks = (req.tile_x_range[1] - req.tile_x_range[0]) * (req.tile_y_range[1] - req.tile_y_range[0])
            plots = [s for s, f in [("bar", req.need_bar), ("upset", req.need_upset), ("dilation", req.need_dilation)] if f]
            print(f"[viewport_plots] Computed [{','.join(plots)}]: "
                  f"{len(result.bar_data)} ch, {len(result.upset_data)} combos, "
                  f"{len(result.dilation_data)} curves from {n_blocks} blocks")
        except Exception as e:
            import traceback
            print(f"[viewport_plots] Error: {e}")
            traceback.print_exc()
        finally:
            self._computing = False

    # ── Main thread: debounce + apply results ──

    def check_and_apply(self, state) -> bool:
        """Called every ~100 ms from the poll loop.

        1. If a pending request has been sitting for ≥ DEBOUNCE_SECONDS
           (meaning no new camera events replaced it), submit it for
           background computation.
        2. Drain the result queue and push to trame state.
        """
        # --- submit pending request if debounce period elapsed ---
        req = self._pending
        if req is not None and not self._computing:
            age = time.monotonic() - req.timestamp
            if age >= self.DEBOUNCE_SECONDS:
                self._pending = None
                self._computing = True
                self._executor.submit(self._bg_compute, req)

        # --- drain results ---
        result = None
        while True:
            try:
                result = self._queue.get_nowait()
            except queue.Empty:
                break

        if result is None:
            return False

        # Full viewport data (all channels)
        state.bar_data_viewport = result.bar_data
        state.upset_data_viewport = result.upset_data
        state.dilation_data_viewport = result.dilation_data

        # Filtered viewport data (active/selected channels only)
        active_set = set(self._active_channels)
        state.bar_data_viewport_selected = [
            item for item in result.bar_data if item[0] in active_set
        ] if active_set else []
        state.upset_data_viewport_selected = [
            item for item in result.upset_data
            if any(ch in active_set for ch in item["channels"])
        ] if active_set else []

        try:
            state.dirty(
                "bar_data_viewport", "upset_data_viewport", "dilation_data_viewport",
                "bar_data_viewport_selected", "upset_data_viewport_selected",
            )
        except Exception:
            pass
        print(f"[viewport_plots] Applied to state: bar={len(result.bar_data)}, "
              f"upset={len(result.upset_data)}, dilation={len(result.dilation_data)}, "
              f"bar_selected={len(state.bar_data_viewport_selected)}, "
              f"upset_selected={len(state.upset_data_viewport_selected)}")
        return True
