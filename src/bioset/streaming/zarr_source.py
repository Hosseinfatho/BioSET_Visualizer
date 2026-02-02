from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, Any

import dask.array as da
from ome_zarr.io import parse_url

from ..cache import wrap_store_with_cache


def _url_to_cache_subdir(url: str) -> str:
    """Generate a unique cache subdirectory name from the URL."""
    # Use a hash of the URL to create a unique but filesystem-safe directory name
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    # Also create a readable prefix from the URL (last path segment)
    try:
        # Extract dataset name from URL path
        path_parts = url.rstrip('/').split('/')
        # Find a meaningful name (skip numeric parts like "0" for component)
        name_part = None
        for part in reversed(path_parts):
            if part and not part.isdigit():
                name_part = part[:30]  # Limit length
                break
        if name_part:
            # Sanitize for filesystem
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
            # Create a unique cache directory for this specific URL
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

    def array(self, component: int) -> da.Array:
        if component not in self._arrays:
            self._arrays[component] = da.from_zarr(
                self.store, component=str(component))
        return self._arrays[component]

    def shape_tczyx(self, component: int) -> Tuple[int, ...]:
        return tuple(self.array(component).shape)
