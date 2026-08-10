"""Stores over the preprocessing outputs.

- `GridInfo`: parsed `colocalization.zarr` root attrs (radius->code, shapes).
- `FieldStore`: cached access to the per-channel EDT/occ planes.
- `TallyStore`: the tally parquets as per-radius numpy arrays, with vectorized
  fingerprint queries (no SQL, no DuckDB).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
import zarr

from .constants import BLOCK_VOX, DETENT_RADII_UM
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

    @classmethod
    def from_attrs(cls, attrs) -> "GridInfo":
        return cls(
            bin_factors=tuple(attrs["bin_factors"]),
            bin_um=tuple(attrs["bin_um"]),
            quant_um=float(attrs["quant_um"]),
            clamp_um=float(attrs["clamp_um"]),
            voxel_um=tuple(attrs["voxel_um"]),
            grid_shape_zyx=tuple(attrs["grid_shape_zyx"]),
            volume_shape_zyx=tuple(attrs["volume_shape_zyx"]),
            n_levels=int(attrs.get("n_levels", 1)),
            levels=int(attrs.get("levels", 256)),
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
        self.root = zarr.open(str(zarr_path), mode="r")
        self.grid = GridInfo.from_attrs(self.root.attrs)
        self._edt_cache_bytes = int(edt_cache_bytes)
        self._lock = threading.Lock()
        self._edt_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._edt_cache_used = 0
        self._mask_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._cumhist_cache: Dict[tuple, np.ndarray] = {}
        self._occ_cache: "OrderedDict[int, np.ndarray]" = OrderedDict()

    @property
    def channel_names(self) -> list:
        return list(self.root.attrs["channels"])

    def level_shape(self, level: int) -> Tuple[int, int, int]:
        _, _, z, y, x = self.root["edt"][str(level)].shape
        return (z, y, x)

    # ── planes ─────────────────────────────────────────────

    def edt_plane(self, channel: int, level: int = 0) -> np.ndarray:
        """Decoded (z, y, x) uint8 EDT plane for one channel, LRU-cached."""
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

    def occ_plane(self, channel: int) -> np.ndarray:
        """Level-0 occupancy plane (foreground voxels per bin, exact).

        Level 0 ONLY: occ pyramid levels >= 1 are sum-clipped at 255 and
        quantitatively meaningless — coarse occupancy must be derived by
        reducing this plane instead.
        """
        with self._lock:
            if channel in self._occ_cache:
                self._occ_cache.move_to_end(channel)
                return self._occ_cache[channel]
        arr = np.asarray(self.root["occ"]["0"][0, channel])
        with self._lock:
            self._occ_cache[channel] = arr
            while len(self._occ_cache) > 4:
                self._occ_cache.popitem(last=False)
        return arr

    # ── derived ────────────────────────────────────────────

    def channel_mask(self, channel: int, code: int, level: int = 0) -> np.ndarray:
        """Bool dilated mask `edt <= code`, small LRU."""
        key = (channel, code, level)
        with self._lock:
            if key in self._mask_cache:
                self._mask_cache.move_to_end(key)
                return self._mask_cache[key]
        mask = self.edt_plane(channel, level) <= np.uint8(code)
        with self._lock:
            self._mask_cache[key] = mask
            while len(self._mask_cache) > self.MASK_CACHE_SIZE:
                self._mask_cache.popitem(last=False)
        return mask

    def channel_cumhist(self, channel: int, level: int = 0) -> np.ndarray:
        """Cumulative EDT histogram (256,) int64: bins within any radius code
        in O(1). Cached permanently (1 KB per entry)."""
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


class TallyStore:
    """The tally parquets held as per-radius numpy arrays.

    Fingerprints are pre-masked to included channels at load time (excluded
    channels become invisible to every query) and rows re-aggregated.
    Re-including a channel requires `reload()` — a rare action.
    """

    def __init__(self, tally_dir: str | Path, registry: ChannelRegistry):
        self.tally_dir = Path(tally_dir)
        self.registry = registry
        # per-radius arrays
        self.global_by_radius: Dict[int, tuple] = {}   # ri -> (fp0, fp1, count)
        self.blocks_by_radius: Dict[int, tuple] = {}   # ri -> (fp0, fp1, count, by, bx)
        self.chstats_by_radius: Dict[int, tuple] = {}  # ri -> (by, bx, channel, voxel_count, sum_intensity)
        self.n_radii = 0
        self._pair_matrix_cache: Dict[int, np.ndarray] = {}
        self._lock = threading.Lock()
        self._load()

    # ── loading ────────────────────────────────────────────

    def _load(self):
        import pyarrow.parquet as pq

        m0, m1 = self.registry.included_fp_masks()

        g = pq.read_table(self.tally_dir / "tally_global.parquet")
        fp0 = g.column("fp_0").to_numpy().astype(np.uint64)
        fp1 = g.column("fp_1").to_numpy().astype(np.uint64)
        count = g.column("count").to_numpy().astype(np.int64)
        ridx = g.column("radius_idx").to_numpy().astype(np.int64)

        self.n_radii = int(ridx.max()) + 1
        if self.n_radii != len(DETENT_RADII_UM):
            raise ValueError(
                f"tally has {self.n_radii} radii but DETENT_RADII_UM has "
                f"{len(DETENT_RADII_UM)} — the hardcoded radius mapping in "
                f"analysis/constants.py does not match this dataset"
            )

        for ri in range(self.n_radii):
            sel = ridx == ri
            self.global_by_radius[ri] = self._mask_and_regroup(
                fp0[sel], fp1[sel], count[sel], m0, m1
            )

        t = pq.read_table(self.tally_dir / "tally.parquet")
        fp0 = t.column("fp_0").to_numpy().astype(np.uint64)
        fp1 = t.column("fp_1").to_numpy().astype(np.uint64)
        count = t.column("count").to_numpy().astype(np.int64)
        by = t.column("block_y").to_numpy().astype(np.int32)
        bx = t.column("block_x").to_numpy().astype(np.int32)
        ridx = t.column("radius_idx").to_numpy().astype(np.int64)
        for ri in range(self.n_radii):
            sel = ridx == ri
            self.blocks_by_radius[ri] = self._mask_and_regroup(
                fp0[sel], fp1[sel], count[sel], m0, m1, by[sel], bx[sel]
            )

        c = pq.read_table(self.tally_dir / "channel_stats.parquet")
        by = c.column("block_y").to_numpy().astype(np.int32)
        bx = c.column("block_x").to_numpy().astype(np.int32)
        ch = c.column("channel").to_numpy().astype(np.int32)
        vc = c.column("voxel_count").to_numpy().astype(np.int64)
        si = c.column("sum_intensity").to_numpy().astype(np.float64)
        ridx = c.column("radius_idx").to_numpy().astype(np.int64)
        for ri in range(self.n_radii):
            sel = ridx == ri
            self.chstats_by_radius[ri] = (by[sel], bx[sel], ch[sel], vc[sel], si[sel])

        self._check_truncation()

    def _check_truncation(self):
        """Warn if the per-block tally was truncated (residual > 0)."""
        import pyarrow.parquet as pq
        path = self.tally_dir / "tally_blocks.parquet"
        if not path.exists():
            return
        tb = pq.read_table(path, columns=["residual"])
        residual = int(tb.column("residual").to_numpy().sum())
        if residual > 0:
            print(
                f"[tally] WARNING: per-block tally is truncated "
                f"(total residual {residual} bins) — block-local counts are "
                f"lower bounds for rare combinations"
            )

    @staticmethod
    def _mask_and_regroup(fp0, fp1, count, m0, m1, by=None, bx=None):
        """Mask fingerprints to included channels and re-aggregate duplicates.

        Rows whose fingerprint becomes empty (bins holding only excluded
        channels) are dropped.
        """
        fp0 = fp0 & m0
        fp1 = fp1 & m1
        keep = (fp0 != np.uint64(0)) | (fp1 != np.uint64(0))
        fp0, fp1, count = fp0[keep], fp1[keep], count[keep]
        if by is not None:
            by, bx = by[keep], bx[keep]
            rec = np.empty(fp0.size, dtype=[
                ("a", np.uint64), ("b", np.uint64), ("y", np.int32), ("x", np.int32)])
            rec["a"], rec["b"], rec["y"], rec["x"] = fp0, fp1, by, bx
        else:
            rec = np.empty(fp0.size, dtype=[("a", np.uint64), ("b", np.uint64)])
            rec["a"], rec["b"] = fp0, fp1
        uniq, inverse = np.unique(rec, return_inverse=True)
        agg = np.zeros(uniq.size, dtype=np.int64)
        np.add.at(agg, inverse, count)
        if by is not None:
            return uniq["a"].copy(), uniq["b"].copy(), agg, uniq["y"].copy(), uniq["x"].copy()
        return uniq["a"].copy(), uniq["b"].copy(), agg

    def reload(self):
        """Re-read parquets (needed after registry inclusion changes)."""
        self.global_by_radius.clear()
        self.blocks_by_radius.clear()
        self.chstats_by_radius.clear()
        with self._lock:
            self._pair_matrix_cache.clear()
        self._load()

    # ── fingerprint queries ────────────────────────────────

    def inter_union_counts(
        self,
        indices: Sequence[int],
        radius_idx: int,
        block_rows: Optional[np.ndarray] = None,
    ) -> Tuple[int, int]:
        """(intersection, union) bin counts for a channel set, in one pass.

        intersection = bins containing ALL the channels (superset sum);
        union        = bins containing ANY of them.
        `block_rows`: optional bool row mask over the per-block table — when
        given, queries `tally.parquet` rows instead of the global table.
        """
        m0, m1 = self.registry.fp_masks(indices)
        if block_rows is None:
            fp0, fp1, count = self.global_by_radius[radius_idx]
        else:
            fp0, fp1, count, _, _ = self.blocks_by_radius[radius_idx]
            fp0, fp1, count = fp0[block_rows], fp1[block_rows], count[block_rows]
        inter = int(count[_superset_rowmask(fp0, fp1, m0, m1)].sum())
        union = int(count[_union_rowmask(fp0, fp1, m0, m1)].sum())
        return inter, union

    def superset_count(
        self,
        indices: Sequence[int],
        radius_idx: int,
        block_rows: Optional[np.ndarray] = None,
    ) -> int:
        """Bins containing ALL the given channels."""
        m0, m1 = self.registry.fp_masks(indices)
        if block_rows is None:
            fp0, fp1, count = self.global_by_radius[radius_idx]
        else:
            fp0, fp1, count, _, _ = self.blocks_by_radius[radius_idx]
            fp0, fp1, count = fp0[block_rows], fp1[block_rows], count[block_rows]
        return int(count[_superset_rowmask(fp0, fp1, m0, m1)].sum())

    def block_row_mask(
        self,
        radius_idx: int,
        by_range: Tuple[int, int],
        bx_range: Tuple[int, int],
    ) -> np.ndarray:
        """Bool row mask over the per-block table for a block range
        (half-open, [y0, y1) x [x0, x1)). Compute once per viewport request."""
        _, _, _, by, bx = self.blocks_by_radius[radius_idx]
        return (
            (by >= by_range[0]) & (by < by_range[1])
            & (bx >= bx_range[0]) & (bx < bx_range[1])
        )

    # ── pair matrix ────────────────────────────────────────

    @staticmethod
    def _accumulate_pair_matrix(fp0, fp1, count) -> np.ndarray:
        """Count-weighted co-occurrence matrix from fingerprint rows.

        Products stay < 2^53 so the float64 matmul is exact.
        """
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
        return M.astype(np.int64)

    def pair_matrix(self, radius_idx: int) -> np.ndarray:
        """(128, 128) int64: M[a, b] = bins containing channels a AND b;
        diagonal = per-channel bin counts. Lazy, cached per radius."""
        with self._lock:
            cached = self._pair_matrix_cache.get(radius_idx)
        if cached is not None:
            return cached
        fp0, fp1, count = self.global_by_radius[radius_idx]
        M = self._accumulate_pair_matrix(fp0, fp1, count)
        with self._lock:
            self._pair_matrix_cache[radius_idx] = M
        return M

    def pair_matrix_rows(self, radius_idx: int, block_rows: np.ndarray) -> np.ndarray:
        """Pair matrix over a block-filtered subset of tally.parquet rows
        (viewport-local upset). Not cached — viewport subsets are small."""
        fp0, fp1, count, _, _ = self.blocks_by_radius[radius_idx]
        return self._accumulate_pair_matrix(
            fp0[block_rows], fp1[block_rows], count[block_rows]
        )

    def channel_bin_counts(self, radius_idx: int) -> np.ndarray:
        """(128,) int64 per-channel bin counts at a tallied radius."""
        return np.diag(self.pair_matrix(radius_idx)).copy()

    # ── channel stats (voxel-exact) ────────────────────────

    def channel_stats_sum(
        self,
        radius_idx: int,
        by_range: Optional[Tuple[int, int]] = None,
        bx_range: Optional[Tuple[int, int]] = None,
    ) -> Dict[int, Tuple[int, float]]:
        """Per-channel (voxel_count, sum_intensity) summed over a block range
        (whole volume when no range given)."""
        by, bx, ch, vc, si = self.chstats_by_radius[radius_idx]
        sel = np.ones(by.size, dtype=bool)
        if by_range is not None:
            sel &= (by >= by_range[0]) & (by < by_range[1])
        if bx_range is not None:
            sel &= (bx >= bx_range[0]) & (bx < bx_range[1])
        out: Dict[int, Tuple[int, float]] = {}
        chs = ch[sel]
        vcs = vc[sel]
        sis = si[sel]
        for c in np.unique(chs):
            m = chs == c
            out[int(c)] = (int(vcs[m].sum()), float(sis[m].sum()))
        return out

    def channel_stats_block(self, block_y: int, block_x: int, radius_idx: int):
        """[(channel, voxel_count, sum_intensity)] for one block."""
        by, bx, ch, vc, si = self.chstats_by_radius[radius_idx]
        sel = (by == block_y) & (bx == block_x)
        return [
            (int(c), int(v), float(s))
            for c, v, s in zip(ch[sel], vc[sel], si[sel])
        ]
