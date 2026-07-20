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
    # Globus HTTPS streaming config (only used when `url` is a Globus URL).
    globus_client_id: Optional[str] = None
    globus_collection_id: Optional[str] = None
    globus_https_base: Optional[str] = None
    globus_token_file: str = "~/.bioset/globus_token.json"

    def __post_init__(self):
        from .globus_store import is_globus_url

        # The Globus HTTPS interface can't list directories, so its stores are
        # consolidated and must be opened with open_consolidated (below). Other
        # sources (S3 / local) keep the ome-zarr parse_url path unchanged.
        self._consolidated = is_globus_url(self.url)
        if self._consolidated:
            from .globus_store import (GlobusHTTPStore, make_globus_authorizer,
                                       resolve_globus_url)
            base = resolve_globus_url(self.url, self.globus_https_base)
            print(f"[zarr_source] Globus HTTPS store: {base}")
            authorizer = make_globus_authorizer(
                self.globus_client_id, self.globus_collection_id,
                self.globus_token_file)
            source_store = GlobusHTTPStore(base, authorizer)
        else:
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
        self._croot = None   # cached consolidated root group (Globus)

    def _consolidated_root(self):
        """Consolidated root group (opened once) for a non-listing store."""
        if self._croot is None:
            import zarr
            self._croot = zarr.open_consolidated(self.store, mode="r")
        return self._croot

    def root_attrs(self) -> Dict[str, Any]:
        """Attributes of the store's root group (OME-Zarr ``multiscales`` / ``omero``).

        These live on the root group, not on the per-level arrays. Returns an
        empty dict when the URL points straight at a level array (e.g. a URL
        ending in ``/0``) or the store has no root ``.zattrs``.
        """
        try:
            if self._consolidated:
                root = self._consolidated_root()
            else:
                import zarr
                root = zarr.open_group(self.store, mode="r")
            return dict(root.attrs)
        except Exception as e:
            print(f"[zarr_source] No root attributes available: {e}")
            return {}

    def array(self, component: int) -> da.Array:
        if component not in self._arrays:
            if self._consolidated:
                self._arrays[component] = da.from_zarr(self.raw_array(component))
            else:
                self._arrays[component] = da.from_zarr(
                    self.store, component=str(component))
        return self._arrays[component]

    def raw_array(self, component: int):
        """Raw (non-dask) zarr Array for direct per-chunk reads.

        Opened through the *cached* ``self.store`` so chunk reads still hit the
        on-disk CacheStore (the decoded-chunk in-memory cache sits above this).
        The handle is memoized: reopening it per chunk would re-read ``.zarray``,
        one wasted round-trip per chunk. Globus stores are opened consolidated
        (no directory listing over HTTPS).
        """
        a = self._raw_arrays.get(component)
        if a is None:
            import zarr
            if self._consolidated:
                root = self._consolidated_root()
            else:
                root = zarr.open_group(self.store, mode="r")
            a = root[str(component)]
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
