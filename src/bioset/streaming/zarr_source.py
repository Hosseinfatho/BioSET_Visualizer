from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

import dask.array as da
from ome_zarr.io import parse_url

from ..cache import wrap_store_with_cache


# Schemes whose data lives off-device and benefits from a local disk cache.
# Anything else (a bare filesystem path, file://, or a Windows drive letter) is
# already-local and must NOT be disk-cached (that just copies local chunks into
# a second local dir — double I/O and space for zero benefit).
_REMOTE_SCHEMES = frozenset(
    {"globus", "http", "https", "s3", "gs", "gcs", "az", "abfs", "abfss"})


def _is_remote_url(url: str) -> bool:
    """True for off-device sources that should be disk-cached: ``globus://…``,
    ``http(s)://…`` (incl. S3), ``s3/gs/az://``. A local path, ``file://``, or a
    Windows drive (``C:\\…``) has no remote scheme and returns False."""
    if not url:
        return False
    i = url.find("://")
    if i <= 0:  # no scheme separator -> local filesystem path
        return False
    return url[:i].lower() in _REMOTE_SCHEMES


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

        # Only disk-cache REMOTE sources (Globus / S3 / http). A local zarr is
        # already on fast local disk, so caching it just duplicates chunks into a
        # second dir — read it straight from the source instead.
        remote = self._consolidated or _is_remote_url(self.url)
        if self.cache_enabled and remote:
            cache_subdir = _url_to_cache_subdir(self.url)
            url_specific_cache_dir = self.cache_dir / cache_subdir
            print(f"[zarr_source] Using cache dir: {url_specific_cache_dir}")

            self.store = wrap_store_with_cache(
                source_store,
                cache_dir=url_specific_cache_dir,
                max_size_bytes=self.cache_size_bytes,
            )
        else:
            if self.cache_enabled and not remote:
                print(f"[zarr_source] Local source — reading directly (no disk cache)")
            self.store = source_store

        self._arrays: Dict[int, da.Array] = {}
        self._raw_arrays: Dict[int, Any] = {}
        self._croot = None   # cached consolidated root group (Globus)
        # Inferred layout (lazy): real level names ordered finest→coarsest, and a
        # map from canonical axis (t/c/z/y/x) to the array's dimension index.
        # These make the source work with ANY OME-Zarr — level names like
        # ``s0..sN`` (not just ``0..N``), levels in any stored order, and arrays
        # that are 3D (z,y,x), 4D (c,z,y,x), or 5D (t,c,z,y,x).
        self._level_paths: Optional[list] = None
        self._axis_index: Optional[Dict[str, Optional[int]]] = None

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

    # ------------------------------------------------------------------
    # Level (resolution) discovery — names + order inferred from the store.
    # ------------------------------------------------------------------
    def _level_paths_list(self) -> Optional[list]:
        """Real level names ordered finest→coarsest (component 0 = finest).

        We do NOT assume numeric names (``0..N``) or that the first listed level
        is the finest: order by voxel scale from the ``multiscales`` metadata
        (smallest scale = finest). Falls back to spec order, then to listing the
        group's arrays ordered by size (largest = finest). Returns None only when
        nothing can be discovered (caller then probes numeric names).
        """
        if self._level_paths is not None:
            return self._level_paths or None

        paths: Optional[list] = None
        attrs = self.root_attrs()
        ms = attrs.get("multiscales")
        if ms:
            datasets = ms[0].get("datasets") or []
            if datasets:
                def _scale_prod(d):
                    for tf in d.get("coordinateTransformations") or []:
                        if tf.get("type") == "scale" and tf.get("scale"):
                            p = 1.0
                            for v in tf["scale"]:
                                try:
                                    p *= float(v)
                                except (TypeError, ValueError):
                                    pass
                            return p
                    return None
                keyed = [(_scale_prod(d), str(d.get("path"))) for d in datasets]
                if all(k is not None for k, _ in keyed):
                    keyed.sort(key=lambda t: t[0])  # ascending scale = finest first
                    paths = [p for _, p in keyed]
                else:
                    # No scale info: trust NGFF ordering (datasets are finest-first).
                    paths = [str(d.get("path")) for d in datasets]

        if paths is None:
            # No multiscales block: list the group's arrays and order by size
            # (largest = finest). Robust to any naming.
            try:
                import numpy as _np
                import zarr
                g = self._consolidated_root() if self._consolidated \
                    else zarr.open_group(self.store, mode="r")
                keys = list(g.array_keys())
                if keys:
                    keys.sort(key=lambda k: int(_np.prod(g[k].shape)), reverse=True)
                    paths = keys
            except Exception:
                paths = None

        self._level_paths = paths or []
        return paths

    def _path_for(self, component: int) -> str:
        """Real dataset name for a component index (0 = finest)."""
        paths = self._level_paths_list()
        if paths and 0 <= component < len(paths):
            return paths[component]
        return str(component)  # last-resort legacy numeric name

    def level_count(self, max_probe: int = 24) -> int:
        """Number of resolution levels (inferred from the store, never assumed)."""
        paths = self._level_paths_list()
        if paths is not None:
            return max(1, len(paths))

        count = 0
        for component in range(max_probe):
            try:
                self.array(component)
            except Exception:
                break
            count += 1
        return max(1, count)

    def array(self, component: int) -> da.Array:
        if component not in self._arrays:
            if self._consolidated:
                self._arrays[component] = da.from_zarr(self.raw_array(component))
            else:
                self._arrays[component] = da.from_zarr(
                    self.store, component=self._path_for(component))
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
            a = root[self._path_for(component)]
            self._raw_arrays[component] = a
        return a

    def shape_tczyx(self, component: int) -> Tuple[int, ...]:
        return tuple(self.array(component).shape)

    # ------------------------------------------------------------------
    # Axis-aware access — works for 3D/4D/5D arrays with any axis order.
    # ------------------------------------------------------------------
    def _axes(self) -> Dict[str, Optional[int]]:
        """Map canonical axis {t,c,z,y,x} → dimension index (or None if absent).

        Prefers the ``multiscales`` ``axes`` names; if absent, infers from the
        array's dimensionality (5D→tczyx, 4D→czyx, 3D→zyx, 2D→yx)."""
        if self._axis_index is not None:
            return self._axis_index

        names = None
        attrs = self.root_attrs()
        ms = attrs.get("multiscales")
        if ms:
            axes = ms[0].get("axes")
            if axes:
                names = [
                    (a.get("name") if isinstance(a, dict) else str(a)) or ""
                    for a in axes
                ]
        if not names:
            try:
                ndim = self.raw_array(0).ndim
            except Exception:
                ndim = 5
            names = {
                5: ["t", "c", "z", "y", "x"],
                4: ["c", "z", "y", "x"],
                3: ["z", "y", "x"],
                2: ["y", "x"],
                1: ["x"],
            }.get(ndim, ["z", "y", "x"])

        idx: Dict[str, Optional[int]] = {k: None for k in ("t", "c", "z", "y", "x")}
        for i, n in enumerate(names):
            k = str(n).strip().lower()[:1]  # NGFF names: t/c/z/y/x (or channel/time)
            if k in idx:
                idx[k] = i
        self._axis_index = idx
        return idx

    def num_channels(self) -> int:
        """Channel count from the 'c' axis; 1 when the store has no channel axis
        (e.g. a bare 3D z,y,x volume — treated as a single channel)."""
        ci = self._axes().get("c")
        if ci is None:
            return 1
        try:
            return int(self.raw_array(0).shape[ci])
        except Exception:
            return 1

    def canonical_shape(self, component: int) -> Tuple[int, int, int]:
        """(z, y, x) sizes for a component, regardless of stored axis layout.
        A missing spatial axis (e.g. a 2D image) reports size 1 for that axis."""
        sh = self.raw_array(component).shape
        ax = self._axes()
        z = int(sh[ax["z"]]) if ax["z"] is not None else 1
        y = int(sh[ax["y"]]) if ax["y"] is not None else 1
        x = int(sh[ax["x"]]) if ax["x"] is not None else 1
        return (z, y, x)

    def canonical_chunks(self, component: int) -> Tuple[int, int, int]:
        """(cz, ty, tx) chunk sizes for a component (defaults to the full extent
        on any axis without chunk info)."""
        arr = self.raw_array(component)
        ax = self._axes()
        z, y, x = self.canonical_shape(component)
        chunks = getattr(arr, "chunks", None)
        if not chunks:
            return (z, y, x)
        cz = int(chunks[ax["z"]]) if ax["z"] is not None else z
        ty = int(chunks[ax["y"]]) if ax["y"] is not None else y
        tx = int(chunks[ax["x"]]) if ax["x"] is not None else x
        return (cz, ty, tx)

    def read_region(self, component: int, ch: int,
                    y0: int, y1: int, x0: int, x1: int, t: int = 0):
        """Read a full-z column for one channel over [y0:y1, x0:x1] as a (z,y,x)
        numpy array, selecting the t/c axes only if the store actually has them."""
        arr = self.raw_array(component)
        ax = self._axes()
        idx: list = [slice(None)] * arr.ndim
        if ax["t"] is not None:
            idx[ax["t"]] = int(t)
        if ax["c"] is not None:
            idx[ax["c"]] = int(ch)
        if ax["y"] is not None:
            idx[ax["y"]] = slice(int(y0), int(y1))
        if ax["x"] is not None:
            idx[ax["x"]] = slice(int(x0), int(x1))
        # z axis stays a full slice. NGFF orders spatial axes z,y,x, so after the
        # scalar t/c selections the remaining axes are already (z, y, x).
        import numpy as _np
        return _np.asarray(arr[tuple(idx)])

    def read_full(self, component: int, ch: int, t: int = 0):
        """Read a whole channel volume at a component as a (z,y,x) numpy array."""
        _z, y, x = self.canonical_shape(component)
        return self.read_region(component, ch, 0, y, 0, x, t=t)

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
