# cache.py
from __future__ import annotations

from pathlib import Path
from typing import Any


def wrap_store_with_cache(source_store: Any, *, cache_dir: Path, max_size_bytes: int):
    """
    Wrap an existing zarr Store with an on-disk CacheStore.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

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
