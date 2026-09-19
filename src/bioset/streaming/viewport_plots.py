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
    # Channels the UpSet dialog allows in a combination; None = no restriction.
    combo_channels: object = None
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
            combo_channels=req.combo_channels,
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
        # Channels ticked in the UpSet dialog. None = no restriction.
        self._selected_channels = None
        # Curve keys ticked in the dilation dialog; None means no
        # restriction. Held separately from the raw result so a filter
        # change can be applied without re-running the viewport query.
        self._dilation_keys = None
        self._dilation_raw: dict = {}
        # Channels ticked in the BAR dialog, and the last raw bar result. Held
        # apart from the UpSet selection above: they are two separate dialogs,
        # and the bar arrays were previously gated by the UpSet one.
        self._bar_selected = None
        self._bar_raw: list = []
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

    def update_selected_channels(self, names):
        """Channels ticked in the UpSet dialog; None means no restriction.

        The viewport arrays previously ignored this entirely and filtered only
        by the 3D view's active channels, so the dialog's checkboxes were inert
        in Local scope."""
        self._selected_channels = None if names is None else list(names)

    def update_dilation_selection(self, keys):
        """Curve keys ticked in the dilation dialog; None means no restriction.

        The viewport curves were previously published unfiltered, so the dialog
        — and the single/multiple view mode, whose options the selection is
        drawn from — did nothing at all in Local scope: the global array was
        filtered and the viewport one was not.
        """
        self._dilation_keys = None if keys is None else set(keys)

    def update_bar_selected_channels(self, names):
        """Channels ticked in the BAR dialog; None means no restriction.

        The bar's own dialog previously never reached the viewport arrays at
        all — they were filtered by the UpSet dialog's selection, so in Local
        scope the bar's checkboxes were inert and the UpSet's silently moved
        the bar.
        """
        self._bar_selected = None if names is None else set(names)

    def apply_bar_filter(self, state) -> bool:
        """Re-publish both bar arrays under the current selection.

        Mirrors the global rule exactly: the "All" array is the dialog's
        selection, and the "Selected" array is that intersected with the
        channels active in the 3D view. Works off the cached result so ticking
        a box does not re-run the viewport query.
        """
        if not self._bar_raw:
            return False
        allowed = self._bar_selected
        chosen = [item for item in self._bar_raw
                  if allowed is None or item[0] in allowed]
        active = set(self._active_channels)
        if allowed is not None:
            active &= allowed
        state.bar_data_viewport = chosen
        state.bar_data_viewport_selected = [
            item for item in chosen if item[0] in active
        ] if active else []
        try:
            state.dirty("bar_data_viewport", "bar_data_viewport_selected")
        except Exception:
            pass
        return True

    def apply_dilation_filter(self, state) -> bool:
        """Re-publish the viewport curves under the current selection.

        Separate from `check_and_apply` so ticking a box re-filters the cached
        result immediately, instead of waiting for — and paying for — another
        viewport query. Returns True if anything was written.
        """
        if not self._dilation_raw:
            return False
        keys = self._dilation_keys
        state.dilation_data_viewport = (
            dict(self._dilation_raw) if keys is None
            else {k: v for k, v in self._dilation_raw.items() if k in keys}
        )
        try:
            state.dirty("dilation_data_viewport")
        except Exception:
            pass
        return True

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
            combo_channels=(None if self._selected_channels is None
                            else list(self._selected_channels)),
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

        state.upset_data_viewport = result.upset_data
        # Cache the raw bar rows so a dialog change re-filters them without
        # another query, then publish through the bar's own selection.
        self._bar_raw = result.bar_data
        self.apply_bar_filter(state)
        # Keep the unfiltered curves so a dialog change can re-filter them
        # without another query, then publish through the same selection the
        # global array is filtered by.
        self._dilation_raw = result.dilation_data
        self.apply_dilation_filter(state)

        # UpSet only. The dialog's exclusion is applied inside
        # get_viewport_metrics, so `result.upset_data` already contains only
        # ticked channels. What is left here is the "Selected" channel mode's
        # inclusion rule: keep combinations involving at least one channel
        # active in the 3D view. The bar arrays are NOT derived from this — they
        # have their own dialog, applied in apply_bar_filter above.
        allowed = None if self._selected_channels is None else set(self._selected_channels)
        touching = set(self._active_channels)
        if allowed is not None:
            touching &= allowed

        state.upset_data_viewport_selected = [
            item for item in result.upset_data
            if touching.intersection(item["channels"])
        ] if touching else []

        try:
            state.dirty(
                "bar_data_viewport", "upset_data_viewport", "dilation_data_viewport",
                "bar_data_viewport_selected", "upset_data_viewport_selected",
            )
        except Exception:
            pass
        # Counts as PUBLISHED, not as computed: the bar and dilation arrays are
        # filtered by their dialogs on the way out, so logging the raw result
        # would not match what is on screen.
        print(f"[viewport_plots] Applied to state: bar={len(state.bar_data_viewport)}, "
              f"upset={len(result.upset_data)}, "
              f"dilation={len(state.dilation_data_viewport)}, "
              f"bar_selected={len(state.bar_data_viewport_selected)}, "
              f"upset_selected={len(state.upset_data_viewport_selected)}")
        return True
