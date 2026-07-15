from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

import dask.array as da
from ome_zarr.io import parse_url

from ..cache import wrap_store_with_cache


def _url_to_cache_subdir(url: str) -> str:
    """Generate a unique cache subdirectory name from the URL."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    try:
        path_parts = url.rstrip('/').split('/')
        name_part = None
        for part in reversed(path_parts):
            if part and not part.isdigit():
                name_part = part[:30]  
                break
        if name_part:
            name_part = "".join(c if c.isalnum() or c in '-_' else '_' for c in name_part)
            return f"{name_part}_{url_hash}"
    except Exception:
        pass
    return url_hash


@dataclass
class ZarrMultiscaleSource:
    url: str
    cache_enabled: bool
    cache_dir: Path
    cache_size_bytes: int

    def __post_init__(self):
        root = parse_url(self.url, mode="r")
        source_store = root.store

        if self.cache_enabled:
            cache_subdir = _url_to_cache_subdir(self.url)
            url_specific_cache_dir = self.cache_dir / cache_subdir
            print(f"[zarr_source] Using cache dir: {url_specific_cache_dir}")
            
            self.store = wrap_store_with_cache(
                source_store,
                cache_dir=url_specific_cache_dir,
                max_size_bytes=self.cache_size_bytes,
            )
        else:
            self.store = source_store

        self._arrays: Dict[int, da.Array] = {}
        self._raw_arrays: Dict[int, Any] = {}

    def array(self, component: int) -> da.Array:
        if component not in self._arrays:
            self._arrays[component] = da.from_zarr(
                self.store, component=str(component))
        return self._arrays[component]

    def raw_array(self, component: int):
        """Raw (non-dask) zarr Array for direct per-chunk reads.

        Opened through the *cached* ``self.store`` so chunk reads still hit the
        on-disk CacheStore (the decoded-chunk in-memory cache sits above this).
        The handle is memoized: reopening it per chunk would re-read ``.zarray``,
        one wasted round-trip per chunk.
        """
        a = self._raw_arrays.get(component)
        if a is None:
            import zarr
            a = zarr.open_group(self.store, mode="r")[str(component)]
            self._raw_arrays[component] = a
        return a

    def shape_tczyx(self, component: int) -> Tuple[int, ...]:
        return tuple(self.array(component).shape)

    def cache_stats(self) -> Optional[Dict[str, Any]]:
        """On-disk CacheStore performance counters (hits/misses/evictions/hit_rate).

        A 'miss' means the chunk was fetched from the remote store; a 'hit'
        means it was served from the local on-disk cache. Returns None when
        caching is disabled or the store doesn't expose stats.
        """
        fn = getattr(self.store, "cache_stats", None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                return None
        return None

    def cache_info(self) -> Optional[Dict[str, Any]]:
        """On-disk CacheStore state (current_size, max_size, cached_keys, ...)."""
        fn = getattr(self.store, "cache_info", None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                return None
        return None
