from __future__ import annotations

import queue
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .lod import choose_heatmap_level


TILE_SIZES = {0: 128, 1: 256, 2: 512, 3: 1024}


@dataclass
class HeatmapRequest:
    level: int
    db_path: Path
    channels: List[str]        
    channel_order: List[str]   
    dilation: float
    z_depth: int
    timestamp: float


@dataclass
class HeatmapResult:
    level: int
    tiles: list  


def _run_tile_query(conn, channels, channel_order, dilation, level, z_depth):
    """
    Run the combination-tiles query on an arbitrary sqlite3 connection.
    Mirrors the logic of AnalysisLoader._get_single_channel_tiles /
    _get_multi_channel_tiles without depending on the loader instance.
    """
    from bioset.analysis.loader import TileData

    tile_size = TILE_SIZES.get(level, 128)
    conn.row_factory = sqlite3.Row

    if len(channels) == 1:
        channel = channels[0]
        try:
            cursor = conn.execute('''
                SELECT tile_x0, tile_x1, tile_y0, tile_y1, voxel_count
                FROM channel_stats
                WHERE channel = ? AND dilation = ? AND hierarchy_level = ?
                ORDER BY voxel_count DESC
            ''', (channel, dilation, level))
            rows = cursor.fetchall()
            if not rows and dilation != 0.0:
                cursor = conn.execute('''
                    SELECT tile_x0, tile_x1, tile_y0, tile_y1, voxel_count
                    FROM channel_stats
                    WHERE channel = ? AND dilation = 0.0 AND hierarchy_level = ?
                    ORDER BY voxel_count DESC
                ''', (channel, level))
                rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if "no such table: channel_stats" in str(e):
                return []
            raise
        results = []
        for row in rows:
            x_span = max(1, row["tile_x1"] - row["tile_x0"])
            y_span = max(1, row["tile_y1"] - row["tile_y0"])
            tile_vol = (x_span * tile_size) * (y_span * tile_size) * z_depth
            voxel_count = row["voxel_count"] or 0
            active_frac = voxel_count / tile_vol if tile_vol > 0 else 0.0
            results.append(TileData(
                x0=row["tile_x0"], x1=row["tile_x1"],
                y0=row["tile_y0"], y1=row["tile_y1"],
                count=voxel_count, active_fraction=active_frac,
            ))
        return results
    else:
        sorted_channels = sorted(
            channels,
            key=lambda c: channel_order.index(c) if c in channel_order else 999
        )
        channels_str = "|".join(sorted_channels)
        cursor = conn.execute('''
            SELECT t.tile_x0, t.tile_x1, t.tile_y0, t.tile_y1, t.inter_count
            FROM combinations c
            JOIN tiles t ON c.id = t.combination_id
            WHERE c.channels = ? AND c.dilation = ? AND c.hierarchy_level = ?
            ORDER BY t.inter_count DESC
        ''', (channels_str, dilation, level))
        results = []
        for row in cursor:
            x_span = max(1, row["tile_x1"] - row["tile_x0"])
            y_span = max(1, row["tile_y1"] - row["tile_y0"])
            tile_vol = (x_span * tile_size) * (y_span * tile_size) * z_depth
            inter_count = row["inter_count"] or 0
            active_frac = inter_count / tile_vol if tile_vol > 0 else 0.0
            results.append(TileData(
                x0=row["tile_x0"], x1=row["tile_x1"],
                y0=row["tile_y0"], y1=row["tile_y1"],
                count=inter_count, active_fraction=active_frac,
            ))
        return results


class HeatmapLOD:
    """
    Drives automatic heatmap hierarchy-level selection based on camera zoom.

    Threading model mirrors VolumeStreamer:
      - on_camera_moved() called on main thread → debounce → executor
      - _bg_load() runs in worker thread, opens its own SQLite connection
      - check_and_apply() called from main thread asyncio poll loop
    """

    DEBOUNCE_DELAY = 0.15

    def __init__(self, distance_rules=None):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="heatmap_lod")
        self._queue: queue.Queue[HeatmapResult] = queue.Queue()
        self._debounce_lock = threading.Lock()
        self._pending: Optional[HeatmapRequest] = None
        self._current_level: int = 3

        self._db_path: Optional[Path] = None
        self._channel_order: List[str] = []
        self._z_depth: int = 1
        self._channels: List[str] = []
        self._dilation: float = 0.0

        # Distance rules from config: [(distance_threshold, level), ...]
        self._distance_rules = distance_rules or (
            (1000.0, 3),
            (300.0,  2),
            (100.0,  1),
            (-100.0, 0),
        )
        self._auto_mode: bool = True  # controlled by UI toggle

    def set_analysis(self, db_path: Path, channel_order: List[str], z_depth: int):
        """Called after analysis file is loaded."""
        self._db_path = db_path
        self._channel_order = channel_order
        self._z_depth = z_depth
        self._current_level = 3  
        print(f"[heatmap_lod] Analysis context set: {db_path}, "
              f"{len(channel_order)} channels, z_depth={z_depth}")

    def clear_analysis(self):
        """Called when analysis is cleared."""
        self._db_path = None
        self._channels = []
        self._channel_order = []

    def update_channels(self, channel_names: List[str]):
        """Update the list of active channel names."""
        self._channels = list(channel_names)

    def update_dilation(self, dilation: float):
        """Update current dilation so next load uses correct value."""
        self._dilation = dilation

    def set_auto_mode(self, enabled: bool):
        """Enable or disable automatic level selection. Called when UI toggle changes."""
        self._auto_mode = enabled
        print(f"[heatmap_lod] Auto mode: {enabled}")

    # Camera movement

    def on_camera_moved(self, distance: float):
        """
        Called from the EndInteractionEvent handler (main thread).
        Schedules a background heatmap re-query if the level should change.
        Only runs when auto mode is enabled.
        """
        if not self._auto_mode:
            return
        if self._db_path is None or not self._channels:
            return

        desired_level = choose_heatmap_level(distance, self._distance_rules)
        if desired_level == self._current_level:
            return

        req = HeatmapRequest(
            level=desired_level,
            db_path=self._db_path,
            channels=list(self._channels),
            channel_order=list(self._channel_order),
            dilation=self._dilation,
            z_depth=self._z_depth,
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
        """Runs in worker thread. Opens its own SQLite connection."""
        print(f"[heatmap_lod] Loading level {req.level} for {req.channels}")
        try:
            conn = sqlite3.connect(str(req.db_path), check_same_thread=False)
            try:
                tiles = _run_tile_query(
                    conn,
                    req.channels,
                    req.channel_order,
                    req.dilation,
                    req.level,
                    req.z_depth,
                )
            finally:
                conn.close()
            print(f"[heatmap_lod] Level {req.level}: {len(tiles)} tiles loaded")
            self._queue.put(HeatmapResult(level=req.level, tiles=tiles))
        except Exception as e:
            import traceback
            print(f"[heatmap_lod] Background load failed: {e}")
            traceback.print_exc()

    # Main thread

    def check_and_apply(self, heatmap_renderer, state) -> bool:
        """
        Called from main thread asyncio poll loop.
        Drains queue and applies tile data to the heatmap renderer.
        Returns True if anything was applied (triggers view.update()).
        """
        applied = False
        while True:
            try:
                result = self._queue.get_nowait()
            except queue.Empty:
                break

            spacing = (
                getattr(state, 'physical_size_x', 1.0),
                getattr(state, 'physical_size_y', 1.0),
                getattr(state, 'physical_size_z', 1.0),
            )
            from bioset.scene.heatmap import hex_to_rgb
            color = hex_to_rgb(getattr(state, 'heatmap_color', '#FFFFFF'))

            heatmap_renderer.update_tiles(result.tiles, spacing=spacing, color=color)
            self._current_level = result.level

            state.current_hierarchy_level = result.level

            print(f"[heatmap_lod] Applied level {result.level}: {len(result.tiles)} tiles")
            applied = True

        return applied
