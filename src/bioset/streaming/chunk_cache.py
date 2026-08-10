"""Chunk page-table: a byte-budget LRU over decoded chunks + a residency map.

This replaces the exact-ROI contiguous array cache. Keying on chunk identity
``(comp, ch, cyi, cxi)`` (rather than a whole-viewport ROI) is what makes
overlapping pans/zooms reuse data instead of re-downloading it, and lets a
viewport be assembled from a mix of already-resident and freshly-fetched tiles.

Both structures are thread-safe: with concurrent per-chunk fetch threads they
are mutated from many threads at once, so every public method locks internally.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

import numpy as np

# (comp, ch, cyi, cxi)  [+ czi if a z-chunked store is ever supported]
ChunkKey = Tuple[int, ...]


class ChunkCache:
    """Thread-safe byte-budget LRU over decoded chunk arrays.

    Budget is in *bytes*, not entries: overlapping ROIs share chunks instead of
    duplicating whole arrays, so this is typically *less* RAM than the old
    32-entry ROI cache while holding far more reusable data.
    """

    def __init__(self, budget_bytes: int):
        self.budget = int(budget_bytes)
        self._store: "OrderedDict[ChunkKey, np.ndarray]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: ChunkKey) -> Optional[np.ndarray]:
        with self._lock:
            arr = self._store.get(key)
            if arr is None:
                self.misses += 1
                return None
            self._store.move_to_end(key)   # LRU touch
            self.hits += 1
            return arr

    def put(self, key: ChunkKey, arr: np.ndarray) -> None:
        with self._lock:
            existing = self._store.get(key)
            if existing is not None:
                self._bytes -= existing.nbytes
            self._store[key] = arr
            self._store.move_to_end(key)
            self._bytes += arr.nbytes
            while self._bytes > self.budget and len(self._store) > 1:
                _, old = self._store.popitem(last=False)   # evict LRU
                self._bytes -= old.nbytes
                self.evictions += 1

    def __contains__(self, key: ChunkKey) -> bool:
        with self._lock:
            return key in self._store

    @property
    def nbytes(self) -> int:
        with self._lock:
            return self._bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._bytes = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "entries": len(self._store),
                "bytes": self._bytes,
                "hit_rate": (self.hits / total) if total else 0.0,
            }


class State(Enum):
    ABSENT = 0
    IN_FLIGHT = 1
    RESIDENT = 2


@dataclass
class PageEntry:
    state: State = State.ABSENT
    gen: int = -1
    # A tile may be resident at a COARSER comp than requested; this records
    # which — the hook for mixed-resolution (blurry periphery) rendering.
    resident_comp: int = -1


class PageTable:
    """Residency map over chunk keys.

    ``mark_inflight`` returns ``False`` when the tile is already resident or in
    flight, which dedups concurrent fetch requests for the same tile.
    """

    def __init__(self):
        self._e: dict[ChunkKey, PageEntry] = {}
        self._lock = threading.Lock()

    def state(self, key: ChunkKey) -> State:
        with self._lock:
            e = self._e.get(key)
            return e.state if e else State.ABSENT

    def mark_inflight(self, key: ChunkKey, gen: int, comp: int) -> bool:
        with self._lock:
            e = self._e.get(key)
            if e and e.state in (State.RESIDENT, State.IN_FLIGHT):
                return False
            self._e[key] = PageEntry(State.IN_FLIGHT, gen, comp)
            return True

    def mark_resident(self, key: ChunkKey, gen: int, comp: int) -> None:
        with self._lock:
            self._e[key] = PageEntry(State.RESIDENT, gen, comp)

    def mark_absent(self, key: ChunkKey) -> None:
        with self._lock:
            self._e.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._e.clear()
