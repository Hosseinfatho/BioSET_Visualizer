from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def prune_cache_dir(cache_dir: Path, budget_bytes: int) -> None:
    """Trim one dataset's on-disk cache subdir to ``budget_bytes`` (LRU by mtime).

    zarr's experimental ``CacheStore`` tracks its LRU size in memory only and
    rebuilds it empty every process start, so chunks written in previous
    sessions are served on hits but never re-entered into the LRU and therefore
    never evicted — the cache dir grows without bound across runs. This bounds
    the persistent footprint directly: while the subdir total exceeds the budget,
    delete the least-recently-modified files (chunks are write-once, so mtime is
    a good recency proxy) until under budget, then drop any now-empty dirs.

    Safe to run before the store is opened: every file here is a reconstructable
    cache copy of a source key (chunk or metadata), so a deleted entry is simply
    re-fetched from the source on next access.
    """
    if budget_bytes <= 0 or not cache_dir.exists():
        return

    files: list[tuple[str, int, float]] = []
    total = 0
    for root, _dirs, names in os.walk(cache_dir):
        for name in names:
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            files.append((path, st.st_size, st.st_mtime))
            total += st.st_size

    if total <= budget_bytes:
        return

    files.sort(key=lambda t: t[2])  # oldest first = LRU
    for path, size, _mtime in files:
        if total <= budget_bytes:
            break
        try:
            os.remove(path)
            total -= size
        except OSError:
            continue

    # Remove directories left empty by the deletions (bottom-up, best-effort).
    for root, _dirs, _names in os.walk(cache_dir, topdown=False):
        if Path(root) == cache_dir:
            continue
        try:
            os.rmdir(root)
        except OSError:
            pass


def wrap_store_with_cache(source_store: Any, *, cache_dir: Path, max_size_bytes: int):
    """
    Wrap an existing zarr Store with an on-disk CacheStore.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Bound this dataset's persistent cache before opening it (no live store to
    # race). Keeps the subdir at <= the configured size across sessions rather
    # than growing unbounded (the CacheStore's own eviction can't see residue it
    # didn't write this run).
    prune_cache_dir(cache_dir, max_size_bytes)

    from zarr.storage import LocalStore
    from zarr.experimental.cache_store import CacheStore

    cache_store = LocalStore(str(cache_dir))

    cached_store = CacheStore(
        store=source_store,
        cache_store=cache_store,
        max_size=max_size_bytes,
        max_age_seconds="infinity",
        cache_set_data=True,
    )
    return cached_store
