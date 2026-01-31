from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, Any

import dask.array as da
from ome_zarr.io import parse_url

from ..cache.store import wrap_store_with_cache


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
            self.store = wrap_store_with_cache(
                source_store,
                cache_dir=self.cache_dir,
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
