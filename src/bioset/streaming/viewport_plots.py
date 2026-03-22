"""Background computation of plot metrics restricted to viewport-visible tiles.

Threading model mirrors HeatmapLOD:
  - on_camera_moved() called on main thread → debounce → executor
  - _bg_compute() runs in worker thread, opens its own SQLite connection
  - check_and_apply() called from main thread asyncio poll loop

All queries use hierarchy_level=0 (finest granularity) regardless of the
heatmap LOD level.  Tile coordinates in the DB are always in level-0 grid
units (each cell = 128 voxels), so the ROI-to-grid conversion is just
``voxel_coord // 128``.
"""
from __future__ import annotations

import queue
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import combinations as iter_combinations
from pathlib import Path
from typing import List, Optional

# Viewport plots always query at the finest hierarchy level.
_QUERY_LEVEL = 0
_BASE_TILE = 128


@dataclass
class ViewportPlotRequest:
    tile_x_range: tuple[int, int]  # (grid_x0, grid_x1) inclusive/exclusive
    tile_y_range: tuple[int, int]  # (grid_y0, grid_y1) inclusive/exclusive
    db_path: Path
    channels: list[str]
    channel_order: list[str]
    dilation: float
    min_channels: int
    z_depth: int
    timestamp: float


@dataclass
class ViewportPlotResult:
    bar_data: list  # [(channel, density_pct), ...]
    upset_data: list  # [{"channels": [...], "iou": float, "overlap_coeff": float}, ...]
    dilation_data: dict  # {key: [{"dilation": float, ...}]}
    timestamp: float


def _tile_range_sql(req: ViewportPlotRequest, table_prefix=""):
    """Build range-based tile filter: tile_x0 >= ? AND tile_x0 < ? AND ..."""
    prefix = f"{table_prefix}." if table_prefix else ""
    sql = (f"{prefix}tile_x0 >= ? AND {prefix}tile_x0 < ? "
           f"AND {prefix}tile_y0 >= ? AND {prefix}tile_y0 < ?")
    params = [req.tile_x_range[0], req.tile_x_range[1],
              req.tile_y_range[0], req.tile_y_range[1]]
    return sql, params


def _run_viewport_queries(conn, req: ViewportPlotRequest) -> ViewportPlotResult:
    """Execute all viewport plot queries on a worker-thread connection."""
    conn.row_factory = sqlite3.Row
    dilation = req.dilation
    channels = req.channels
    min_channels = req.min_channels

    tile_filter, tile_params = _tile_range_sql(req)
    tile_filter_t, tile_params_t = _tile_range_sql(req, "t")

    z_depth = req.z_depth
    n_tiles_x = req.tile_x_range[1] - req.tile_x_range[0]
    n_tiles_y = req.tile_y_range[1] - req.tile_y_range[0]
    tile_volume = n_tiles_x * _BASE_TILE * n_tiles_y * _BASE_TILE * z_depth

    # ── Bar: per-channel voxel density ──
    query_ch = f'''
        SELECT channel, SUM(voxel_count) as total_voxels
        FROM channel_stats
        WHERE dilation = ? AND hierarchy_level = {_QUERY_LEVEL}
          AND {tile_filter}
        GROUP BY channel
        ORDER BY total_voxels DESC
    '''
    cursor = conn.execute(query_ch, [dilation] + tile_params)
    channel_voxels = {}
    bar_data = []
    for row in cursor:
        ch = row["channel"]
        voxels = row["total_voxels"] or 0
        channel_voxels[ch] = voxels
        density = (voxels / tile_volume * 100) if tile_volume > 0 else 0.0
        bar_data.append((ch, density))

    # ── UpSet: combination IoU ──
    query_combo = f'''
        SELECT c.channels, c.channel_count,
               SUM(t.inter_count) as sum_inter,
               SUM(t.union_count) as sum_union
        FROM combinations c
        JOIN tiles t ON c.id = t.combination_id
        WHERE c.dilation = ? AND c.hierarchy_level = {_QUERY_LEVEL}
          AND c.channel_count >= ?
          AND {tile_filter_t}
        GROUP BY c.channels
        HAVING sum_union > 0
        ORDER BY CAST(SUM(t.inter_count) AS REAL) / SUM(t.union_count) DESC
        LIMIT ?
    '''
    cursor = conn.execute(query_combo, [dilation, min_channels] + tile_params_t + [200])
    upset_data = []
    for row in cursor:
        channels_str = row["channels"]
        chs = channels_str.split("|") if channels_str else []
        if len(chs) != len(set(chs)):
            continue
        sum_inter = row["sum_inter"] or 0
        sum_union = row["sum_union"] or 1
        iou = sum_inter / sum_union if sum_union > 0 else 0.0
        ch_totals = [channel_voxels.get(c, 0) for c in chs]
        min_v = min(ch_totals) if ch_totals else 0
        oc = sum_inter / min_v if min_v > 0 else 0.0
        upset_data.append({"channels": chs, "iou": iou, "overlap_coeff": oc})

    # ── Dilation curves ──
    channel_order = req.channel_order

    def sort_chs(ch_list):
        return sorted(ch_list, key=lambda c: channel_order.index(c) if c in channel_order else 999)

    def make_key(ch_list):
        return "|".join(sort_chs(ch_list))

    dilation_data = {}
    for size in range(1, len(channels) + 1):
        for combo in iter_combinations(channels, size):
            combo_list = list(combo)
            combo_key = make_key(combo_list)

            if size == 1:
                curve = _viewport_single_curve(conn, combo_list[0], tile_filter, tile_params, tile_volume)
            else:
                curve = _viewport_multi_curve(
                    conn, combo_list, channel_order, tile_filter, tile_params,
                    tile_filter_t, tile_params_t, tile_volume
                )
            if curve:
                dilation_data[combo_key] = curve

    return ViewportPlotResult(
        bar_data=bar_data,
        upset_data=upset_data,
        dilation_data=dilation_data,
        timestamp=req.timestamp,
    )


def _viewport_single_curve(conn, channel, tile_filter, tile_params, tile_volume):
    query = f'''
        SELECT dilation, SUM(voxel_count) as total_voxels
        FROM channel_stats
        WHERE channel = ? AND hierarchy_level = {_QUERY_LEVEL} AND {tile_filter}
        GROUP BY dilation ORDER BY dilation
    '''
    try:
        cursor = conn.execute(query, [channel] + tile_params)
    except sqlite3.OperationalError:
        return []
    results = []
    for row in cursor:
        v = row["total_voxels"] or 0
        d = (v / tile_volume * 100) if tile_volume > 0 else 0.0
        results.append({"dilation": row["dilation"], "count": v, "iou": 1.0, "overlap_coeff": 1.0, "density": d})
    return results


def _viewport_multi_curve(conn, channels, channel_order, tile_filter, tile_params, tile_filter_t, tile_params_t, tile_volume):
    sorted_channels = sorted(channels, key=lambda c: channel_order.index(c) if c in channel_order else 999)
    channels_str = "|".join(sorted_channels)

    query = f'''
        SELECT c.dilation, SUM(t.inter_count) as sum_inter, SUM(t.union_count) as sum_union
        FROM combinations c JOIN tiles t ON c.id = t.combination_id
        WHERE c.channels = ? AND c.hierarchy_level = {_QUERY_LEVEL} AND {tile_filter_t}
        GROUP BY c.dilation ORDER BY c.dilation
    '''
    cursor = conn.execute(query, [channels_str] + tile_params_t)
    rows = cursor.fetchall()
    if not rows:
        return []

    results = []
    for row in rows:
        dil = row["dilation"]
        si = row["sum_inter"] or 0
        su = row["sum_union"] or 1
        iou = si / su if su > 0 else 0.0

        ch_totals = []
        for ch in channels:
            cq = f"SELECT SUM(voxel_count) as total FROM channel_stats WHERE channel = ? AND dilation = ? AND hierarchy_level = {_QUERY_LEVEL} AND {tile_filter}"
            cr = conn.execute(cq, [ch, dil] + tile_params).fetchone()
            ch_totals.append(cr["total"] or 0 if cr else 0)
        min_v = min(ch_totals) if ch_totals else 0
        oc = si / min_v if min_v > 0 else 0.0
        density = (si / tile_volume * 100) if tile_volume > 0 else 0.0
        results.append({"dilation": dil, "count": si, "iou": iou, "overlap_coeff": oc, "density": density})
    return results


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

        self._db_path: Optional[Path] = None
        self._channel_order: List[str] = []
        self._z_depth: int = 1

        self._channels: List[str] = []
        self._dilation: float = 0.0
        self._min_channels: int = 2
        self._enabled: bool = False

    def set_analysis(self, db_path: Path, channel_order: List[str], z_depth: int):
        """Called after analysis file is loaded."""
        self._db_path = db_path
        self._channel_order = channel_order
        self._z_depth = z_depth
        print(f"[viewport_plots] Analysis context set: {db_path}")

    def clear_analysis(self):
        """Called when analysis is cleared."""
        self._db_path = None
        self._channels = []
        self._channel_order = []

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        print(f"[viewport_plots] Enabled: {enabled}")

    def update_channels(self, channel_names: List[str]):
        self._channels = list(channel_names)

    def update_dilation(self, dilation: float):
        self._dilation = dilation

    def update_min_channels(self, min_ch: int):
        self._min_channels = min_ch

    # ── Camera move trigger (lightweight — no threads) ──

    def on_camera_moved(self, tile_x_range: tuple[int, int], tile_y_range: tuple[int, int]):
        """Called from main thread on EndInteractionEvent.  Just stores the
        request; the poll loop will submit it after the debounce period."""
        if not self._enabled or not self._channels or self._db_path is None:
            return
        if tile_x_range[1] <= tile_x_range[0] or tile_y_range[1] <= tile_y_range[0]:
            return

        self._pending = ViewportPlotRequest(
            tile_x_range=tile_x_range,
            tile_y_range=tile_y_range,
            db_path=self._db_path,
            channels=list(self._channels),
            channel_order=list(self._channel_order),
            dilation=self._dilation,
            min_channels=self._min_channels,
            z_depth=self._z_depth,
            timestamp=time.monotonic(),
        )

    def _bg_compute(self, req: ViewportPlotRequest):
        """Worker thread: open own DB connection, compute, enqueue result."""
        try:
            conn = sqlite3.connect(str(req.db_path), check_same_thread=False)
            try:
                result = _run_viewport_queries(conn, req)
                self._queue.put(result)
                n_tiles = (req.tile_x_range[1] - req.tile_x_range[0]) * (req.tile_y_range[1] - req.tile_y_range[0])
                print(f"[viewport_plots] Computed: {len(result.bar_data)} channels, "
                      f"{len(result.upset_data)} combos, {len(result.dilation_data)} curves "
                      f"from {n_tiles} tiles (x:{req.tile_x_range}, y:{req.tile_y_range})")
            finally:
                conn.close()
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

        state.bar_data_viewport = result.bar_data
        state.upset_data_viewport = result.upset_data
        state.dilation_data_viewport = result.dilation_data
        try:
            state.dirty("bar_data_viewport", "upset_data_viewport", "dilation_data_viewport")
        except Exception:
            pass
        print(f"[viewport_plots] Applied to state: bar={len(result.bar_data)}, "
              f"upset={len(result.upset_data)}, dilation={len(result.dilation_data)}")
        return True
