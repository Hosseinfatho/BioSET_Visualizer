"""Stores over the preprocessing outputs.

- `GridInfo`: parsed `colocalization.zarr` root attrs (radius->code, shapes).
- `FieldStore`: cached access to the per-channel EDT/occ planes.
- `TallyStore`: the tally parquets, sized for interaction rather than
  completeness — see its docstring for what is loaded and what deliberately is not.

UNITS — the sharpest edge in this package. The combination tables and
`channel_stats` count **raw voxels**; everything derived from the EDT/occ arrays
counts **bins**, which are 256 raw voxels each. Both are legitimate measures and
they are never interchangeable. Method names here say which one they return.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
import zarr

from .constants import BLOCK_VOX
from .radii import RadiusTable
from .registry import ChannelRegistry


# ──────────────────────────────────────────────────────────────
# GridInfo
# ──────────────────────────────────────────────────────────────

@dataclass
class GridInfo:
    """Facts about the analysis grid, parsed from zarr root attrs."""
    bin_factors: Tuple[int, int, int]      # voxels per bin (z, y, x), e.g. (4, 8, 8)
    bin_um: Tuple[float, float, float]
    quant_um: float                        # µm per EDT code
    clamp_um: float                        # EDT saturates beyond this radius
    voxel_um: Tuple[float, float, float]
    grid_shape_zyx: Tuple[int, int, int]   # level-0 bin grid
    volume_shape_zyx: Tuple[int, int, int] # raw voxel volume
    n_levels: int                          # pyramid levels present
    levels: int = 256                      # EDT code count
    radii: Optional[RadiusTable] = None    # tallied radii; see analysis/radii.py

    @classmethod
    def from_attrs(cls, attrs, results_dir=None) -> "GridInfo":
        quant_um = float(attrs["quant_um"])
        levels = int(attrs.get("levels", 256))
        return cls(
            bin_factors=tuple(attrs["bin_factors"]),
            bin_um=tuple(attrs["bin_um"]),
            quant_um=quant_um,
            clamp_um=float(attrs["clamp_um"]),
            voxel_um=tuple(attrs["voxel_um"]),
            grid_shape_zyx=tuple(attrs["grid_shape_zyx"]),
            volume_shape_zyx=tuple(attrs["volume_shape_zyx"]),
            n_levels=int(attrs.get("n_levels", 1)),
            levels=levels,
            radii=RadiusTable.from_dataset(attrs, results_dir, quant_um, levels),
        )

    def code_for(self, r_um: float) -> int:
        """Largest EDT code still within `r_um`; `edt <= code` is the mask."""
        return int(min(np.floor(float(r_um) / self.quant_um), self.levels - 1))

    @property
    def n_bins(self) -> int:
        z, y, x = self.grid_shape_zyx
        return z * y * x

    @property
    def n_voxels(self) -> int:
        z, y, x = self.volume_shape_zyx
        return z * y * x

    @property
    def voxels_per_bin(self) -> int:
        fz, fy, fx = self.bin_factors
        return fz * fy * fx

    @property
    def bins_per_block_yx(self) -> int:
        """Bins per tally block edge in y/x (block is BLOCK_VOX voxels)."""
        return BLOCK_VOX // self.bin_factors[1]


# ──────────────────────────────────────────────────────────────
# FieldStore
# ──────────────────────────────────────────────────────────────

class FieldStore:
    """Cached access to `colocalization.zarr` per-channel planes.

    All getters are thread-safe (heatmap LOD and viewport-plot workers read
    concurrently with the main thread).
    """

    MASK_CACHE_SIZE = 8  # (channel, code, level) bool planes

    def __init__(self, zarr_path: str | Path, edt_cache_bytes: int = 1 << 30):
        self.zarr_path = Path(zarr_path)
        self.root = zarr.open(str(zarr_path), mode="r")
        # The radius mapping falls back to meta.json, which sits beside the store.
        self.grid = GridInfo.from_attrs(self.root.attrs, self.zarr_path.parent)
        self._edt_cache_bytes = int(edt_cache_bytes)
        self._lock = threading.Lock()
        self._edt_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._edt_cache_used = 0
        self._mask_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._cumhist_cache: Dict[tuple, np.ndarray] = {}
        self._occ_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._occ_levels_ok: Optional[bool] = None

    @property
    def channel_names(self) -> list:
        return list(self.root.attrs["channels"])

    def level_shape(self, level: int) -> Tuple[int, int, int]:
        _, _, z, y, x = self.root["edt"][str(level)].shape
        return (z, y, x)

    @property
    def n_edt_levels(self) -> int:
        return max(1, int(self.grid.n_levels))

    # ── planes ─────────────────────────────────────────────

    def edt_plane(self, channel: int, level: int = 0) -> np.ndarray:
        """Decoded (z, y, x) uint8 EDT plane for one channel, LRU-cached.

        The pyramid is MIN-reduced, so a coarse level is a strict display
        *superset* of level 0 — usable to preview a mask, never to count with.
        """
        key = (channel, level)
        with self._lock:
            if key in self._edt_cache:
                self._edt_cache.move_to_end(key)
                return self._edt_cache[key]
        arr = np.asarray(self.root["edt"][str(level)][0, channel])
        with self._lock:
            self._edt_cache[key] = arr
            self._edt_cache_used += arr.nbytes
            while self._edt_cache_used > self._edt_cache_bytes and len(self._edt_cache) > 1:
                _, old = self._edt_cache.popitem(last=False)
                self._edt_cache_used -= old.nbytes
        return arr

    def edt_region(self, channel: int, ys: slice, xs: slice, level: int = 0) -> np.ndarray:
        """(z, dy, dx) uint8 EDT for one channel over a bin sub-rectangle.

        Reads only the intersecting zarr chunks. Slicing a cached whole plane
        instead is ~20x slower here, because 49 channels x 46 MB thrashes the
        plane LRU — so viewport-scoped work must come through this, not
        `edt_plane`. Uncached: callers hold the region for the length of one query.
        """
        return np.asarray(self.root["edt"][str(level)][0, channel, :, ys, xs])

    def occ_levels_usable(self) -> bool:
        """Whether occ levels >= 1 carry a meaningful density.

        Cannot be read off the attrs: runs whose occ pyramid is mean-reduced
        (usable) and runs whose pyramid is sum-clipped at 255 (meaningless)
        both report ``occupancy_reduction: "sum"``. Probe instead — a
        sum-clipped pyramid piles up at the 255 ceiling as it coarsens, a
        mean-reduced one thins out.
        """
        if self._occ_levels_ok is not None:
            return self._occ_levels_ok
        ok = False
        try:
            if self.n_edt_levels > 1:
                fine = np.asarray(self.root["occ"]["0"][0, 0])
                coarse = np.asarray(self.root["occ"]["1"][0, 0])
                f_nz, c_nz = fine[fine > 0], coarse[coarse > 0]
                if f_nz.size and c_nz.size:
                    # Mean-reduced: coarse values sit at or below the fine ones.
                    ok = bool(c_nz.mean() <= f_nz.mean() * 1.05)
        except Exception as exc:
            print(f"[fields] could not probe the occ pyramid ({exc}); using level 0 only")
        self._occ_levels_ok = ok
        return ok

    def occ_plane(self, channel: int, level: int = 0) -> np.ndarray:
        """Occupancy plane (foreground voxels per bin at level 0; a mean density
        at coarser levels, when `occ_levels_usable()` says the pyramid supports it)."""
        if level != 0 and not self.occ_levels_usable():
            level = 0
        key = (channel, level)
        with self._lock:
            if key in self._occ_cache:
                self._occ_cache.move_to_end(key)
                return self._occ_cache[key]
        arr = np.asarray(self.root["occ"][str(level)][0, channel])
        with self._lock:
            self._occ_cache[key] = arr
            while len(self._occ_cache) > 4:
                self._occ_cache.popitem(last=False)
        return arr

    # ── derived ────────────────────────────────────────────

    def channel_cumhist(self, channel: int, level: int = 0) -> np.ndarray:
        """Cumulative EDT histogram (256,) int64: **bins** within any radius code
        in O(1). Cached permanently (2 KB per entry)."""
        key = (channel, level)
        with self._lock:
            hist = self._cumhist_cache.get(key)
        if hist is not None:
            return hist
        plane = self.edt_plane(channel, level)
        hist = np.bincount(plane.ravel(), minlength=self.grid.levels)[
            : self.grid.levels
        ].cumsum()
        with self._lock:
            self._cumhist_cache[key] = hist
        return hist

    # ── cumulative histograms: warm once, reuse forever ────
    #
    # These are what make per-channel coverage O(1) at ANY radius, but building
    # one costs a full 46 MB plane read, so the first caller that wants all 49
    # channels waits ~8 s. They are also tiny (256 int64 each) and depend only
    # on the dataset — so they are computed once off the main thread and cached
    # beside the store, making every later session instant.

    def _sidecar_path(self, level: int) -> Path:
        return self.zarr_path.parent / f".cumhist_L{level}.npz"

    def load_cumhist_cache(self, level: int = 0) -> bool:
        """Populate the histogram cache from the sidecar, if one is present."""
        path = self._sidecar_path(level)
        if not path.exists():
            return False
        try:
            with np.load(path) as z:
                if int(z["levels"]) != self.grid.levels:
                    return False
                channels, table = z["channels"], z["table"]
            with self._lock:
                for c, row in zip(channels.tolist(), table):
                    self._cumhist_cache[(int(c), level)] = row.astype(np.int64)
            return True
        except Exception as exc:
            print(f"[fields] ignoring unreadable histogram cache {path}: {exc}")
            return False

    def warm_cumhists(self, channels: Sequence[int], level: int = 0,
                      save: bool = True) -> None:
        """Build every channel's histogram, then persist them.

        Safe to run on a worker thread: `channel_cumhist` is lock-guarded, and
        callers that arrive mid-warm simply compute their own entry.
        """
        for c in channels:
            self.channel_cumhist(int(c), level)
        if not save:
            return
        path = self._sidecar_path(level)
        try:
            with self._lock:
                rows = [(c, h) for (c, lv), h in self._cumhist_cache.items() if lv == level]
            rows.sort()
            np.savez_compressed(
                path,
                channels=np.array([c for c, _ in rows], dtype=np.int32),
                table=np.stack([h for _, h in rows]).astype(np.int64),
                levels=np.int64(self.grid.levels),
            )
        except Exception as exc:
            # A read-only results directory is normal; the cost is just a
            # re-warm next session.
            print(f"[fields] could not write {path} ({exc}); histograms stay in memory")

    def clear(self):
        with self._lock:
            self._edt_cache.clear()
            self._edt_cache_used = 0
            self._mask_cache.clear()
            self._cumhist_cache.clear()
            self._occ_cache.clear()


# ──────────────────────────────────────────────────────────────
# TallyStore
# ──────────────────────────────────────────────────────────────

def _superset_rowmask(fp0, fp1, m0: np.uint64, m1: np.uint64) -> np.ndarray:
    return ((fp0 & m0) == m0) & ((fp1 & m1) == m1)


def _union_rowmask(fp0, fp1, m0: np.uint64, m1: np.uint64) -> np.ndarray:
    return ((fp0 & m0) != np.uint64(0)) | ((fp1 & m1) != np.uint64(0))


@dataclass
class ComboRows:
    """Ranked combination rows. All counts are RAW VOXELS."""
    fp0: np.ndarray          # uint64
    fp1: np.ndarray          # uint64
    n_inter: np.ndarray      # int64 — voxels containing AT LEAST this set
    n_union: np.ndarray      # int64 — voxels containing at least one member
    iou: Optional[np.ndarray] = None            # None for regional sums
    overlap_coeff: Optional[np.ndarray] = None

    def __len__(self) -> int:
        return int(self.fp0.size)


class TallyStore:
    """The tally tables, sized for interaction rather than completeness.

    Preloaded (small, hot):
        combos_global.parquet   ranked combinations, degree 1..4, per radius
        channel_stats.parquet   per (radius, block, channel), held dense
        tally_blocks.parquet    per-block bookkeeping (truncation check)

    Deliberately never read:
        tally.parquet / tally_global.parquet — 75M fingerprint rows and ~3 GB
        resident. They are the only source of exactly-this-set counts and of
        combinations above `combos_max_degree`; neither is surfaced by the UI.
        Datasets that predate combos_global fall back to reading tally_global
        one radius at a time (see `_legacy_global_rows`).

        combos_blocks.parquet — truncated per block; see the note below.

    UNITS: every count returned here is a RAW VOXEL count. Bin counts come from
    `FieldStore.channel_cumhist` / `edt_region` instead. They differ by up to 256x.
    """

    def __init__(self, tally_dir: str | Path, registry: ChannelRegistry,
                 radii: Optional[RadiusTable] = None):
        self.tally_dir = Path(tally_dir)
        self.registry = registry
        self.radii = radii
        self.n_radii = 0
        self.max_degree = 0
        self.has_combos = False
        self.n_blocks_y = 0
        self.n_blocks_x = 0
        self.residual_total = 0

        self._combos: Dict[Tuple[int, int], ComboRows] = {}
        self._cs_voxels: Optional[np.ndarray] = None    # (ri, by, bx, ch) int64
        self._cs_intensity: Optional[np.ndarray] = None # (ri, by, bx, ch) float64
        self._legacy_cache: Dict[int, tuple] = {}
        self._pair_matrix_cache: Dict[int, np.ndarray] = {}
        self._lock = threading.Lock()
        self._load()

    # ── loading ────────────────────────────────────────────

    @property
    def _combos_global_path(self) -> Path:
        return self.tally_dir / "combos_global.parquet"

    def _load(self):
        self._load_channel_stats()
        if self._combos_global_path.exists():
            self._load_combos_global()
            self.has_combos = True
        else:
            print("[tally] no combos_global.parquet — falling back to the "
                  "fingerprint tables (legacy dataset, loaded one radius at a time)")
            self._probe_legacy_radii()
        self._check_truncation()

        if self.radii is not None and self.n_radii and self.n_radii != len(self.radii):
            # Not fatal: the tables are keyed by radius_idx, so a mismatch only
            # means the labels are wrong. It does mean the mapping came from the
            # fallback constant rather than the dataset — say so loudly.
            print(
                f"[tally] WARNING: tally has {self.n_radii} radii but the radius "
                f"mapping ({self.radii.source}) has {len(self.radii)} — radius "
                f"labels will be wrong above index {len(self.radii) - 1}."
            )

    def _load_channel_stats(self):
        import pyarrow.parquet as pq

        t = pq.read_table(self.tally_dir / "channel_stats.parquet")
        by = t.column("block_y").to_numpy().astype(np.int32)
        bx = t.column("block_x").to_numpy().astype(np.int32)
        ch = t.column("channel").to_numpy().astype(np.int32)
        ri = t.column("radius_idx").to_numpy().astype(np.int32)
        vc = t.column("voxel_count").to_numpy().astype(np.int64)
        si = t.column("sum_intensity").to_numpy().astype(np.float64)

        self.n_radii = int(ri.max()) + 1
        self.n_blocks_y = int(by.max()) + 1
        self.n_blocks_x = int(bx.max()) + 1
        n_ch = max(int(ch.max()) + 1, self.registry.n_channels)

        shape = (self.n_radii, self.n_blocks_y, self.n_blocks_x, n_ch)
        self._cs_voxels = np.zeros(shape, dtype=np.int64)
        self._cs_intensity = np.zeros(shape, dtype=np.float64)
        # Scatter, rather than group-by: one pass, and every later query is a slice.
        self._cs_voxels[ri, by, bx, ch] = vc
        self._cs_intensity[ri, by, bx, ch] = si

    def _load_combos_global(self):
        import pyarrow.parquet as pq

        t = pq.read_table(self._combos_global_path, columns=[
            "degree", "radius_idx", "fp_0", "fp_1",
            "n_inter", "n_union", "iou", "overlap_coeff",
        ])
        deg = t.column("degree").to_numpy().astype(np.int8)
        ri = t.column("radius_idx").to_numpy().astype(np.int16)
        # uint64 must not pass through float: bits above 2^53 vanish silently.
        fp0 = t.column("fp_0").to_numpy().astype(np.uint64)
        fp1 = t.column("fp_1").to_numpy().astype(np.uint64)
        ni = t.column("n_inter").to_numpy().astype(np.int64)
        nu = t.column("n_union").to_numpy().astype(np.int64)
        iou = t.column("iou").to_numpy().astype(np.float64)
        oc = t.column("overlap_coeff").to_numpy().astype(np.float64)

        self.n_radii = max(self.n_radii, int(ri.max()) + 1)
        self.max_degree = int(deg.max())

        m0, m1 = self.registry.included_fp_masks()
        stray = int(np.count_nonzero((fp0 & ~m0) | (fp1 & ~m1)))
        if stray:
            print(f"[tally] WARNING: {stray} combos rows name channels the registry "
                  f"excludes — the analysed-channel rule disagrees with the pipeline's")

        for r in range(self.n_radii):
            for d in range(1, self.max_degree + 1):
                sel = (ri == r) & (deg == d)
                if not sel.any():
                    continue
                self._combos[(r, d)] = ComboRows(
                    fp0=fp0[sel], fp1=fp1[sel], n_inter=ni[sel], n_union=nu[sel],
                    iou=iou[sel], overlap_coeff=oc[sel],
                )

    def _probe_legacy_radii(self):
        import pyarrow.parquet as pq
        path = self.tally_dir / "tally_global.parquet"
        if not path.exists():
            return
        t = pq.read_table(path, columns=["radius_idx"])
        self.n_radii = max(self.n_radii,
                           int(t.column("radius_idx").to_numpy().max()) + 1)

    def _check_truncation(self):
        """Record whether the per-block tally was truncated (residual > 0)."""
        import pyarrow.parquet as pq
        path = self.tally_dir / "tally_blocks.parquet"
        if not path.exists():
            return
        tb = pq.read_table(path, columns=["residual"])
        self.residual_total = int(tb.column("residual").to_numpy().sum())
        if self.residual_total > 0:
            print(
                f"[tally] WARNING: per-block tally is truncated "
                f"(total residual {self.residual_total}) — block-local counts are "
                f"lower bounds for rare combinations"
            )

    def reload(self):
        """Re-read the tables (needed after registry inclusion changes)."""
        self._combos.clear()
        with self._lock:
            self._legacy_cache.clear()
            self._pair_matrix_cache.clear()
        self._load()

    # ── combinations (raw voxels) ──────────────────────────

    def combos(self, radius_idx: int, degree: int) -> Optional[ComboRows]:
        """Whole-volume ranked rows for one degree, or None if not tallied."""
        return self._combos.get((int(radius_idx), int(degree)))

    # NOTE — why there is no `combos_region` reading combos_blocks.parquet.
    #
    # combos_blocks keeps only the top `combos_block_top_k` (200) rows per
    # (block, radius, degree), so its per-block counts do NOT sum to the true
    # regional total above degree 1. Measured on mis_v3 at radius_idx 3:
    #
    #     degree 1:  49 rows/block, never capped   -> sums are EXACT
    #     degree 2:  2073/2519 blocks capped       -> median 32% of the truth
    #     degree 3:  2193/2493 blocks capped       -> median  1.3% of the truth
    #
    # and its own `residual` column is non-zero on 90% of rows, saying so. (The
    # zero `residual` in tally_blocks.parquet is a different table and does not
    # cover this.) Regional metrics therefore come from the EDT arrays via
    # `FieldStore.edt_region`, which is exact and, read region-scoped, fast
    # enough for interaction. See AnalysisLoader.get_viewport_metrics.

    # ── channel stats (raw voxels) ─────────────────────────

    def _cs_slice(self, radius_idx, by_range, bx_range):
        y0, y1 = by_range if by_range else (0, self.n_blocks_y)
        x0, x1 = bx_range if bx_range else (0, self.n_blocks_x)
        y0, y1 = max(0, y0), min(self.n_blocks_y, y1)
        x0, x1 = max(0, x0), min(self.n_blocks_x, x1)
        return (int(radius_idx), slice(y0, y1), slice(x0, x1))

    def channel_voxel_counts(
        self,
        radius_idx: int,
        by_range: Optional[Tuple[int, int]] = None,
        bx_range: Optional[Tuple[int, int]] = None,
    ) -> np.ndarray:
        """Per-channel RAW VOXEL counts, indexed by channel index.

        Verified identical to the degree-1 rows of `combos`, so this is the
        correct denominator for a combination's overlap coefficient.
        """
        sl = self._cs_slice(radius_idx, by_range, bx_range)
        return self._cs_voxels[sl].sum(axis=(0, 1))

    def channel_stats_sum(
        self,
        radius_idx: int,
        by_range: Optional[Tuple[int, int]] = None,
        bx_range: Optional[Tuple[int, int]] = None,
    ) -> Dict[int, Tuple[int, float]]:
        """Per-channel (voxel_count, sum_intensity) over a block range
        (whole volume when no range given). Only channels with signal appear."""
        sl = self._cs_slice(radius_idx, by_range, bx_range)
        vc = self._cs_voxels[sl].sum(axis=(0, 1))
        si = self._cs_intensity[sl].sum(axis=(0, 1))
        nz = np.flatnonzero(vc)
        return {int(c): (int(vc[c]), float(si[c])) for c in nz}

    def channel_stats_region(
        self,
        radius_idx: int,
        by_range: Optional[Tuple[int, int]] = None,
        bx_range: Optional[Tuple[int, int]] = None,
    ) -> Dict[int, Tuple[int, float, int]]:
        """Per-channel (voxel_count, sum_intensity, voxels_backing_intensity)
        over a block range.

        Separate from `channel_stats_sum` because `sum_intensity` is NOT always
        populated — on mis_v3, 1.30M of 1.48M rows are NaN while the rest carry
        real values. A plain sum therefore returns NaN for any channel touching
        one such block, which is both wrong and unserializable as JSON.

        So intensity is summed NaN-safely, and the third element reports how
        many voxels actually backed that sum. A mean must divide by THAT, not
        by the full voxel count, or it is diluted by blocks that never
        contributed. Coverage still uses the full count, which is always known.
        """
        sl = self._cs_slice(radius_idx, by_range, bx_range)
        vc_blocks = self._cs_voxels[sl]
        si_blocks = self._cs_intensity[sl]

        known = np.isfinite(si_blocks)
        vc = vc_blocks.sum(axis=(0, 1))
        si = np.where(known, si_blocks, 0.0).sum(axis=(0, 1))
        vc_known = np.where(known, vc_blocks, 0).sum(axis=(0, 1))

        nz = np.flatnonzero(vc)
        return {int(c): (int(vc[c]), float(si[c]), int(vc_known[c])) for c in nz}

    def channel_stats_block(self, block_y: int, block_x: int, radius_idx: int):
        """[(channel, voxel_count, sum_intensity)] for one block."""
        if not (0 <= block_y < self.n_blocks_y and 0 <= block_x < self.n_blocks_x):
            return []
        vc = self._cs_voxels[int(radius_idx), int(block_y), int(block_x)]
        si = self._cs_intensity[int(radius_idx), int(block_y), int(block_x)]
        return [(int(c), int(vc[c]), float(si[c])) for c in np.flatnonzero(vc)]

    # ── legacy fingerprint path (datasets without combos_*) ─

    def _legacy_global_rows(self, radius_idx: int):
        """(fp0, fp1, count) for one radius from tally_global.parquet — BINS.

        Only reached on datasets predating combos_global. One radius is held at
        a time; this is the path the whole-table preload used to take eagerly.
        """
        with self._lock:
            hit = self._legacy_cache.get(radius_idx)
        if hit is not None:
            return hit

        import pyarrow.parquet as pq
        t = pq.read_table(
            self.tally_dir / "tally_global.parquet",
            columns=["fp_0", "fp_1", "count"],
            filters=[("radius_idx", "==", int(radius_idx))],
        )
        m0, m1 = self.registry.included_fp_masks()
        fp0 = t.column("fp_0").to_numpy().astype(np.uint64) & m0
        fp1 = t.column("fp_1").to_numpy().astype(np.uint64) & m1
        count = t.column("count").to_numpy().astype(np.int64)
        keep = (fp0 != np.uint64(0)) | (fp1 != np.uint64(0))
        rows = (fp0[keep], fp1[keep], count[keep])
        with self._lock:
            self._legacy_cache = {radius_idx: rows}  # one radius at a time
        return rows

    def legacy_inter_union(self, indices: Sequence[int], radius_idx: int) -> Tuple[int, int]:
        """(intersection, union) BIN counts from the fingerprint table."""
        m0, m1 = self.registry.fp_masks(indices)
        fp0, fp1, count = self._legacy_global_rows(radius_idx)
        inter = int(count[_superset_rowmask(fp0, fp1, m0, m1)].sum())
        union = int(count[_union_rowmask(fp0, fp1, m0, m1)].sum())
        return inter, union

    def legacy_pair_matrix(self, radius_idx: int) -> np.ndarray:
        """(128, 128) int64 co-occurrence in BINS; diagonal = per-channel counts."""
        with self._lock:
            cached = self._pair_matrix_cache.get(radius_idx)
        if cached is not None:
            return cached
        fp0, fp1, count = self._legacy_global_rows(radius_idx)
        n = fp0.size
        M = np.zeros((128, 128), dtype=np.float64)
        chunk = 262144
        for s in range(0, n, chunk):
            e = min(s + chunk, n)
            b0 = np.unpackbits(
                fp0[s:e].view(np.uint8).reshape(-1, 8), axis=1, bitorder="little")
            b1 = np.unpackbits(
                fp1[s:e].view(np.uint8).reshape(-1, 8), axis=1, bitorder="little")
            B = np.concatenate([b0, b1], axis=1).astype(np.float64)
            M += B.T @ (B * count[s:e, None])
        M = M.astype(np.int64)
        with self._lock:
            self._pair_matrix_cache[radius_idx] = M
        return M
