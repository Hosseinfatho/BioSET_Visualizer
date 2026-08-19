"""Viewport-driven streaming of mesh tile surfaces.

Threading model mirrors HeatmapLOD:
  - on_camera_moved() on the main thread -> debounce -> executor
  - _bg_load() on a worker: file read, normals, world transform (pure data)
  - check_and_apply() from the main-thread poll loop: actor creation, AddActor,
    eviction — everything that touches the renderer

The manager owns the tile index and the actor cache; this owns the scheduling.
"""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class MeshRequest:
    roi_vox: Optional[Tuple[int, int, int, int]]
    timestamp: float


@dataclass
class MeshLoaded:
    tile: object          # MeshTileInfo
    polydata: object      # vtkPolyData or None
    timestamp: float


class MeshStreamer:
    """Keeps the visible mesh tiles of enabled channels loaded, under budget."""

    DEBOUNCE_DELAY = 0.25
    # Results are drained a few per poll tick so a big refill cannot stall the
    # UI thread building hundreds of actors in one go.
    MAX_APPLY_PER_TICK = 12

    def __init__(self, manager=None, renderer=None):
        self._manager = manager
        self._renderer = renderer
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mesh_stream")
        self._queue: "queue.Queue[MeshLoaded]" = queue.Queue()
        self._debounce_lock = threading.Lock()
        self._pending: Optional[MeshRequest] = None
        self._latest_ts = 0.0
        self._last_roi = None
        self._visible_keys = None
        self._inflight: set = set()

    def set_manager(self, manager, renderer=None):
        self._manager = manager
        if renderer is not None:
            self._renderer = renderer

    @property
    def is_ready(self) -> bool:
        return self._manager is not None and self._manager.is_available

    # ── main thread: scheduling ────────────────────────────

    def on_camera_moved(self, roi_vox=None):
        """Queue a refresh for the current viewport (debounced)."""
        if not self.is_ready or not self._manager.get_active_channels():
            return
        req = MeshRequest(roi_vox=roi_vox, timestamp=time.time())
        with self._debounce_lock:
            self._pending = req
            self._latest_ts = req.timestamp
        threading.Thread(target=self._debounce_cb, args=(req,), daemon=True).start()

    def refresh_now(self, roi_vox=None):
        """Refresh without waiting for the debounce (channel toggled on/off)."""
        if not self.is_ready:
            return
        req = MeshRequest(roi_vox=roi_vox if roi_vox is not None else self._last_roi,
                          timestamp=time.time())
        with self._debounce_lock:
            self._pending = None
            self._latest_ts = req.timestamp
        self._executor.submit(self._bg_load, req)

    def _debounce_cb(self, req: MeshRequest):
        time.sleep(self.DEBOUNCE_DELAY)
        with self._debounce_lock:
            if self._pending and self._pending.timestamp == req.timestamp:
                self._pending = None
                self._executor.submit(self._bg_load, req)

    # ── worker thread: loading ─────────────────────────────

    def _bg_load(self, req: MeshRequest):
        mgr = self._manager
        if mgr is None:
            return
        try:
            self._last_roi = req.roi_vox
            tiles = mgr.visible_tiles(req.roi_vox)
            # Record the visible set so the main thread can retire everything
            # else. Set before loading, so a tile that arrives late is still
            # recognised as wanted.
            self._visible_keys = {mgr.tile_key(t) for t in tiles}
            if mgr.last_truncated:
                print(f"[mesh] out-of-memory ceiling hit — showing the {len(tiles)} "
                      f"tiles nearest the view centre")
            wanted = 0
            for tile in tiles:
                if req.timestamp != self._latest_ts:
                    return  # the camera moved on; do not finish a stale refill
                key = mgr.tile_key(tile)
                if mgr.is_loaded(tile):
                    mgr.touch(tile)
                    continue
                if key in self._inflight:
                    continue
                self._inflight.add(key)
                pd = mgr.load_tile_polydata(tile)
                self._queue.put(MeshLoaded(tile=tile, polydata=pd,
                                           timestamp=req.timestamp))
                wanted += 1
            if wanted:
                print(f"[mesh] queued {wanted} new tiles "
                      f"({len(tiles)} visible for {len(mgr.get_active_channels())} channels)")
        except Exception as e:
            import traceback
            print(f"[mesh] streaming failed: {e}")
            traceback.print_exc()

    # ── main thread: applying ──────────────────────────────

    def check_and_apply(self) -> bool:
        """Drain a bounded number of loaded tiles into the scene.

        Returns True if the scene changed, so the caller can update the view.
        """
        mgr = self._manager
        if mgr is None:
            return False
        changed = False
        for _ in range(self.MAX_APPLY_PER_TICK):
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            self._inflight.discard(mgr.tile_key(item.tile))
            if item.timestamp != self._latest_ts:
                continue  # loaded for a view the user has already left
            if mgr.apply_loaded(item.tile, item.polydata):
                changed = True
        if changed:
            # Retire whatever has left the viewport, THEN fall back on the LRU
            # for anything still over budget. Without the first step, tiles
            # scrolled off screen kept their actors and their share of the
            # budget, which is part of why the visible ones went missing.
            keep = self._visible_keys
            if keep is not None:
                dropped = mgr.evict_outside(keep)
                if dropped:
                    print(f"[mesh_streamer] retired {dropped} tiles that left the view")
            mgr.evict_to_budget(keep or set())
        return changed

    def clear(self):
        with self._debounce_lock:
            self._pending = None
            self._latest_ts = time.time()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._inflight.clear()
        self._last_roi = None
        self._visible_keys = None
