"""Loader and query facade over the zarr + parquet analysis outputs.

Replaces the old gzipped-SQLite ``.bioset`` backend. The public surface keeps
the old class/method names so UI call sites stay mechanical:

- ``dilation=`` kwargs now mean a radius in micrometers (continuous; the five
  preprocessed radii are exact "detent" fast paths through the tally tables,
  anything else is computed from the EDT fields).
- ``hierarchy_level=`` now selects the heatmap cell size (see
  ``constants.DEFAULT_CELL_SIZES_VOX``); global plot queries ignore it (their
  answers are level-independent).
- Channels cross this API as display-name strings (unique after registry
  filtering); indices/fingerprints are internal.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from itertools import combinations as iter_combinations
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import compute
from .constants import (
    BLOCK_VOX,
    DEFAULT_CELL_SIZES_VOX,
    DETENT_RADII_UM,
    detent_idx,
    nearest_detent_idx,
)
from .registry import ChannelRegistry
from .store import FieldStore, GridInfo, TallyStore


@dataclass
class AnalysisMetadata:
    channels: list[str]
    hierarchy_levels: list[dict]
    dilation_amounts: list[float]      # detent radii (µm); slider tick source
    volume_bounds: dict
    radius_max_um: float = 4.0         # continuous-slider cap
    dtype_max: int = 65535             # kept for Biomni payloads; prefer image metadata


@dataclass
class TileData:
    """One heatmap cell (pick/drill-down result). Coordinates in cell units."""
    x0: int
    x1: int
    y0: int
    y1: int
    count: int                  # active analysis bins in the cell
    active_fraction: float = 0.0


@dataclass
class HeatmapField:
    """Non-zero heatmap cells for one combination at one LOD level."""
    level: int
    cell_size_vox: int          # cell edge in voxels (y/x); z spans the volume
    ny: int                     # cell-grid dims
    nx: int
    cells_yx: np.ndarray        # (N, 2) int32 — (cy, cx) of non-zero cells
    counts: np.ndarray          # (N,) int64 — active bins per cell
    fractions: np.ndarray       # (N,) float32 — exact active fraction per cell


@dataclass
class CombinationData:
    """A biomarker combination with aggregated overlap metrics (bin counts)."""
    channels: list[str]
    total_count: int
    iou: float = 0.0
    overlap_coeff: float = 0.0
    tiles: list[TileData] = field(default_factory=list)


class AnalysisLoader:
    """Query interface over a results directory containing
    ``colocalization.zarr`` and ``tally/``."""

    def __init__(self, cell_sizes_vox: Optional[Dict[int, int]] = None,
                 radius_max_um: float = 4.0):
        self.cell_sizes_vox = dict(cell_sizes_vox or DEFAULT_CELL_SIZES_VOX)
        self.radius_max_um = float(radius_max_um)
        self.registry: Optional[ChannelRegistry] = None
        self.fields: Optional[FieldStore] = None
        self.tally: Optional[TallyStore] = None
        self.grid: Optional[GridInfo] = None
        self.metadata: Optional[AnalysisMetadata] = None
        self._results_dir: Optional[Path] = None
        self._loaded = False
        # small caches
        self._curve_cache: "OrderedDict[tuple, dict]" = OrderedDict()
        self._field_cache: "OrderedDict[tuple, HeatmapField]" = OrderedDict()

    # ──────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def results_dir(self) -> Optional[Path]:
        return self._results_dir

    def load(self, results_dir: str) -> AnalysisMetadata:
        """Load a results directory (server-side path)."""
        self.close()
        path = Path(results_dir)
        zarr_path = path / "colocalization.zarr"
        tally_path = path / "tally"
        if not zarr_path.exists():
            raise FileNotFoundError(f"no colocalization.zarr in {path}")
        if not tally_path.exists():
            raise FileNotFoundError(f"no tally/ directory in {path}")

        print(f"[analysis] Loading {path} ...")
        self.fields = FieldStore(zarr_path)
        self.grid = self.fields.grid
        self.registry = ChannelRegistry(self.fields.channel_names)
        self.tally = TallyStore(tally_path, self.registry)
        self._results_dir = path

        vz, vy, vx = self.grid.volume_shape_zyx
        self.metadata = AnalysisMetadata(
            channels=self.registry.display_names(),
            hierarchy_levels=[{"level": lvl} for lvl in sorted(self.cell_sizes_vox)],
            dilation_amounts=list(DETENT_RADII_UM),
            volume_bounds={"x": [0, vx], "y": [0, vy], "z": [0, vz]},
            radius_max_um=min(self.radius_max_um, self.grid.clamp_um),
        )
        self._loaded = True
        n_excluded = self.registry.n_channels - len(self.metadata.channels)
        print(
            f"[analysis] Loaded: {len(self.metadata.channels)} channels "
            f"({n_excluded} hidden), {self.tally.n_radii} tallied radii, "
            f"grid {self.grid.grid_shape_zyx}"
        )
        return self.metadata

    def close(self):
        if self.fields:
            self.fields.clear()
        self.registry = None
        self.fields = None
        self.tally = None
        self.grid = None
        self.metadata = None
        self._results_dir = None
        self._loaded = False
        self._curve_cache.clear()
        self._field_cache.clear()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # Radius helpers
    # ──────────────────────────────────────────────

    def is_detent(self, r_um: float) -> Optional[int]:
        """radius_idx if `r_um` is a tallied radius, else None."""
        return detent_idx(r_um)

    def _code(self, r_um: float) -> int:
        return self.grid.code_for(r_um)

    # ──────────────────────────────────────────────
    # Internal: counts for channel-index sets
    # ──────────────────────────────────────────────

    def _inter_union(self, indices: Sequence[int], r_um: float) -> Tuple[int, int]:
        """(intersection, union) bin counts at any radius (exact both paths)."""
        ri = self.is_detent(r_um)
        if ri is not None:
            return self.tally.inter_union_counts(indices, ri)
        code = self._code(r_um)
        if len(indices) == 1:
            n = int(self.fields.channel_cumhist(indices[0])[code])
            return n, n
        planes = [self.fields.edt_plane(c) for c in indices]
        inter_cum, union_cum = compute.dilation_curve(planes, self.grid.levels)
        return int(inter_cum[code]), int(union_cum[code])

    def _channel_counts(self, indices: Sequence[int], r_um: float) -> List[int]:
        """Per-channel bin counts at any radius."""
        ri = self.is_detent(r_um)
        if ri is not None:
            diag = self.tally.channel_bin_counts(ri)
            return [int(diag[c]) for c in indices]
        code = self._code(r_um)
        return [int(self.fields.channel_cumhist(c)[code]) for c in indices]

    @staticmethod
    def _metrics(_indices: Sequence[int], inter: int, union: int,
                 ch_counts: Sequence[int]) -> Tuple[float, float]:
        iou = inter / union if union > 0 else 0.0
        mn = min(ch_counts) if ch_counts else 0
        oc = inter / mn if mn > 0 else 0.0
        return iou, oc

    # ──────────────────────────────────────────────
    # UpSet: top combinations
    # ──────────────────────────────────────────────

    def get_top_combinations(
        self,
        dilation: float,
        hierarchy_level: int = 0,
        limit: int = 50,
        min_channels: int = 2,
    ) -> list[CombinationData]:
        """Top combinations of exactly `min_channels` channels by IoU."""
        if not self.is_loaded:
            return []
        if min_channels == 2:
            return self._top_pairs(dilation, limit)
        return self._top_ktuples(dilation, limit, min_channels)

    def _top_pairs(self, r_um: float, limit: int) -> list[CombinationData]:
        ri = self.is_detent(r_um)
        base_ri = ri if ri is not None else nearest_detent_idx(r_um)
        M = self.tally.pair_matrix(base_ri)
        idx = self.registry.included_indices()
        diag = np.diag(M)

        results = []
        if ri is not None:
            for ai in range(len(idx)):
                a = idx[ai]
                if diag[a] == 0:
                    continue
                for b in idx[ai + 1:]:
                    inter = int(M[a, b])
                    if inter == 0:
                        continue
                    union = int(diag[a] + diag[b] - inter)
                    iou = inter / union if union > 0 else 0.0
                    oc = inter / int(min(diag[a], diag[b]))
                    results.append((iou, oc, inter, a, b))
        else:
            # candidates from the nearest detent, exact recount from EDT masks
            cands = []
            for ai in range(len(idx)):
                a = idx[ai]
                for b in idx[ai + 1:]:
                    if M[a, b] > 0:
                        cands.append((int(M[a, b]), a, b))
            cands.sort(reverse=True)
            cands = cands[: max(limit * 4, 200)]
            code = self._code(r_um)
            for _, a, b in cands:
                pa = self.fields.edt_plane(a)
                pb = self.fields.edt_plane(b)
                na = int(self.fields.channel_cumhist(a)[code])
                nb = int(self.fields.channel_cumhist(b)[code])
                inter = int(np.count_nonzero(np.maximum(pa, pb) <= code))
                if inter == 0:
                    continue
                union = na + nb - inter
                iou = inter / union if union > 0 else 0.0
                mn = min(na, nb)
                oc = inter / mn if mn > 0 else 0.0
                results.append((iou, oc, inter, a, b))

        results.sort(key=lambda t: t[0], reverse=True)
        out = []
        for iou, oc, inter, a, b in results[:limit]:
            out.append(CombinationData(
                channels=[self.registry.name_of(a), self.registry.name_of(b)],
                total_count=inter, iou=iou, overlap_coeff=oc,
            ))
        return out

    def _top_ktuples(self, r_um: float, limit: int, k: int) -> list[CombinationData]:
        """Top combinations of exactly k>=3 channels.

        Candidates: k-subsets of the highest-count exact fingerprints at the
        nearest detent, exact-counted afterwards.
        """
        base_ri = self.is_detent(r_um)
        base_ri = base_ri if base_ri is not None else nearest_detent_idx(r_um)
        fp0, fp1, count = self.tally.global_by_radius[base_ri]
        order = np.argsort(count)[::-1][:4096]
        cand: dict[tuple, None] = {}
        for i in order:
            bits = [c for c in self.registry.included_indices()
                    if ((int(fp0[i]) >> (c % 64)) & 1 if c < 64
                        else (int(fp1[i]) >> (c % 64)) & 1)]
            if len(bits) < k:
                continue
            for combo in iter_combinations(bits, k):
                cand[combo] = None
                if len(cand) >= 2048:
                    break
            if len(cand) >= 2048:
                break

        scored = []
        for combo in cand:
            inter, union = self._inter_union(list(combo), r_um)
            if inter == 0:
                continue
            ch_counts = self._channel_counts(list(combo), r_um)
            iou, oc = self._metrics(combo, inter, union, ch_counts)
            scored.append((iou, oc, inter, combo))
        scored.sort(key=lambda t: t[0], reverse=True)

        out = []
        for iou, oc, inter, combo in scored[:limit]:
            out.append(CombinationData(
                channels=[self.registry.name_of(c) for c in combo],
                total_count=inter, iou=iou, overlap_coeff=oc,
            ))
        return out

    # ──────────────────────────────────────────────
    # UpSet: combinations containing given channels
    # ──────────────────────────────────────────────

    def get_filtered_combinations(
        self,
        channel_filter: list[str],
        dilation: float,
        hierarchy_level: int = 0,
        limit: int = 50,
        exact_match: bool = False,
    ) -> list[CombinationData]:
        """Combinations involving the filter channels, sorted by IoU.

        - exact_match: just the one combination.
        - otherwise: every subset (size>=2) of the filter set, plus every
          pair (filter channel x any other channel).
        """
        if not self.is_loaded or not channel_filter:
            return []
        try:
            f_idx = self.registry.indices_of(channel_filter)
        except KeyError:
            return []

        if exact_match:
            inter, union = self._inter_union(f_idx, dilation)
            if inter == 0:
                return []
            iou, oc = self._metrics(
                f_idx, inter, union, self._channel_counts(f_idx, dilation))
            return [CombinationData(
                channels=self.registry.sort_names(channel_filter),
                total_count=inter, iou=iou, overlap_coeff=oc,
            )]

        combos: dict[tuple, None] = {}
        # subsets of the filter set (size >= 2), small k in practice
        if len(f_idx) >= 2 and len(f_idx) <= 10:
            for size in range(2, len(f_idx) + 1):
                for c in iter_combinations(sorted(f_idx), size):
                    combos[c] = None
        # pairs with every other included channel
        for a in f_idx:
            for b in self.registry.included_indices():
                if b == a:
                    continue
                combos[tuple(sorted((a, b)))] = None

        # score pairs cheaply via the pair matrix at the nearest detent, keep
        # a candidate pool, then exact-count
        base_ri = self.is_detent(dilation)
        base_ri = base_ri if base_ri is not None else nearest_detent_idx(dilation)
        M = self.tally.pair_matrix(base_ri)

        def prescore(combo):
            if len(combo) == 2:
                return int(M[combo[0], combo[1]])
            return min(int(M[a, b]) for a, b in iter_combinations(combo, 2))

        pool = sorted(combos, key=prescore, reverse=True)
        pool = [c for c in pool if prescore(c) > 0][: max(limit * 3, 150)]

        results = []
        for combo in pool:
            inter, union = self._inter_union(list(combo), dilation)
            if inter == 0:
                continue
            iou, oc = self._metrics(
                combo, inter, union, self._channel_counts(list(combo), dilation))
            results.append(CombinationData(
                channels=[self.registry.name_of(c) for c in combo],
                total_count=inter, iou=iou, overlap_coeff=oc,
            ))
        results.sort(key=lambda c: c.iou, reverse=True)
        return results[:limit]

    # ──────────────────────────────────────────────
    # Bar chart: coverage
    # ──────────────────────────────────────────────

    def get_channel_coverage(
        self,
        dilation: float,
        hierarchy_level: int = 0,
    ) -> list[tuple[str, float]]:
        """Per-channel coverage percent, descending.

        At tallied radii: voxel-exact (channel_stats). At arbitrary radii:
        fraction of 1.12 µm analysis bins (EDT cumulative histograms).
        """
        if not self.is_loaded:
            return []
        ri = self.is_detent(dilation)
        results = []
        if ri is not None:
            stats = self.tally.channel_stats_sum(ri)
            total = self.grid.n_voxels
            for c in self.registry.included_indices():
                vc, _ = stats.get(c, (0, 0.0))
                results.append((self.registry.name_of(c), vc / total * 100.0))
        else:
            code = self._code(dilation)
            total = self.grid.n_bins
            for c in self.registry.included_indices():
                n = int(self.fields.channel_cumhist(c)[code])
                results.append((self.registry.name_of(c), n / total * 100.0))
        results.sort(key=lambda t: t[1], reverse=True)
        return results

    def coverage_is_voxel_exact(self, dilation: float) -> bool:
        return self.is_detent(dilation) is not None

    # ──────────────────────────────────────────────
    # Heatmap field
    # ──────────────────────────────────────────────

    def get_heatmap_field(
        self,
        channels: list[str],
        dilation: float,
        hierarchy_level: int,
    ) -> Optional[HeatmapField]:
        """Per-cell active-bin counts/fractions for a combination.

        Always computed from level-0 EDT (values are exact at every radius;
        the min-pooled pyramid is only a display superset, never used for
        numbers). Single channel at r=0 gets voxel-exact fractions from occ.
        """
        if not self.is_loaded or not channels:
            return None
        try:
            indices = self.registry.indices_of(channels)
        except KeyError:
            return None
        level = int(hierarchy_level)
        cell_vox = self.cell_sizes_vox.get(level, 16)
        code = self._code(dilation)

        key = (tuple(sorted(indices)), code, level)
        cached = self._field_cache.get(key)
        if cached is not None:
            self._field_cache.move_to_end(key)
            return cached

        cell_bins = max(1, cell_vox // self.grid.bin_factors[1])
        mask = compute.combination_mask(
            [self.fields.edt_plane(c) for c in indices], code)
        sums, denoms = compute.cell_reduce(mask, cell_bins)

        if len(indices) == 1 and code == 0:
            # voxel-exact shading from occupancy
            occ_sums, occ_denoms = compute.cell_reduce(
                self.fields.occ_plane(indices[0]).astype(np.int64), cell_bins)
            fractions_2d = occ_sums / (occ_denoms * self.grid.voxels_per_bin)
        else:
            fractions_2d = sums / denoms

        cy, cx = np.nonzero(sums)
        result = HeatmapField(
            level=level,
            cell_size_vox=cell_vox,
            ny=sums.shape[0],
            nx=sums.shape[1],
            cells_yx=np.stack([cy, cx], axis=1).astype(np.int32),
            counts=sums[cy, cx],
            fractions=fractions_2d[cy, cx].astype(np.float32),
        )
        self._field_cache[key] = result
        while len(self._field_cache) > 6:
            self._field_cache.popitem(last=False)
        return result

    # ──────────────────────────────────────────────
    # Dilation curves (continuous)
    # ──────────────────────────────────────────────

    def get_subcombination_dilation_curves(
        self,
        channels: list[str],
        hierarchy_level: int = 0,
    ) -> dict[str, list[dict]]:
        """Continuous dilation curves for every subcombination of `channels`.

        Keys are '|'-joined display names; each point is
        {dilation, count, iou, overlap_coeff, density} — same shape as before,
        but sampled at every EDT code up to the radius cap instead of 5 points.
        """
        if not self.is_loaded or not channels:
            return {}
        try:
            indices = self.registry.indices_of(channels)
        except KeyError:
            return {}
        return self._curves_for(indices, region=None)

    def _curves_for(self, indices: Sequence[int],
                    region: Optional[Tuple[slice, slice]]) -> dict[str, list[dict]]:
        """Curves for all subcombinations, optionally restricted to a y/x bin
        slice of the level-0 grid."""
        max_code = self._code(self.metadata.radius_max_um)
        codes = np.arange(0, max_code + 1)
        quant = self.grid.quant_um

        def plane(c):
            p = self.fields.edt_plane(c)
            if region is not None:
                p = p[:, region[0], region[1]]
            return p

        if region is None:
            total = self.grid.n_bins
        else:
            total = plane(indices[0]).size

        cache_key = (tuple(sorted(indices)), region)
        cached = self._curve_cache.get(cache_key)
        if cached is not None:
            self._curve_cache.move_to_end(cache_key)
            return cached

        # per-channel cumulative histograms (used by singles and OC denominators)
        cumhists = {}
        for c in indices:
            if region is None:
                cumhists[c] = self.fields.channel_cumhist(c)
            else:
                cumhists[c] = np.bincount(
                    plane(c).ravel(), minlength=self.grid.levels
                )[: self.grid.levels].cumsum()

        results: dict[str, list[dict]] = {}
        for size in range(1, len(indices) + 1):
            for combo in iter_combinations(sorted(indices), size):
                key = "|".join(self.registry.name_of(c) for c in combo)
                if size == 1:
                    cum = cumhists[combo[0]]
                    pts = [
                        {
                            "dilation": round(float(code) * quant, 4),
                            "count": int(cum[code]),
                            "iou": 1.0,
                            "overlap_coeff": 1.0,
                            "density": float(cum[code]) / total * 100.0,
                        }
                        for code in codes
                    ]
                else:
                    inter_cum, union_cum = compute.dilation_curve(
                        [plane(c) for c in combo], self.grid.levels)
                    pts = []
                    for code in codes:
                        inter = int(inter_cum[code])
                        union = int(union_cum[code])
                        mn = min(int(cumhists[c][code]) for c in combo)
                        pts.append({
                            "dilation": round(float(code) * quant, 4),
                            "count": inter,
                            "iou": inter / union if union > 0 else 0.0,
                            "overlap_coeff": inter / mn if mn > 0 else 0.0,
                            "density": inter / total * 100.0,
                        })
                if any(p["count"] > 0 for p in pts):
                    results[key] = pts

        self._curve_cache[cache_key] = results
        while len(self._curve_cache) > 8:
            self._curve_cache.popitem(last=False)
        return results

    # ──────────────────────────────────────────────
    # Viewport-local queries (used by streaming.viewport_plots)
    # ──────────────────────────────────────────────

    def _viewport_bin_region(self, by_range, bx_range) -> Tuple[slice, slice]:
        bpb = self.grid.bins_per_block_yx
        _, gy, gx = self.grid.grid_shape_zyx
        return (
            slice(min(by_range[0] * bpb, gy), min(by_range[1] * bpb, gy)),
            slice(min(bx_range[0] * bpb, gx), min(bx_range[1] * bpb, gx)),
        )

    def get_viewport_metrics(
        self,
        by_range: Tuple[int, int],
        bx_range: Tuple[int, int],
        dilation: float,
        min_channels: int = 2,
        limit: int = 50,
    ) -> dict:
        """Bar + upset data restricted to a block range.

        Returns {"bar": [(name, pct)], "upset": [{channels, iou, overlap_coeff}]}.
        """
        if not self.is_loaded:
            return {"bar": [], "upset": []}
        ri = self.is_detent(dilation)
        included = self.registry.included_indices()

        if ri is not None:
            # bar: voxel-exact from channel_stats; denominator clipped to the
            # true volume extent (the block grid overhangs the volume edge)
            vz, vy, vx = self.grid.volume_shape_zyx
            span_y = max(0, min(by_range[1] * BLOCK_VOX, vy) - by_range[0] * BLOCK_VOX)
            span_x = max(0, min(bx_range[1] * BLOCK_VOX, vx) - bx_range[0] * BLOCK_VOX)
            block_voxels = max(1, span_y * span_x * vz)
            stats = self.tally.channel_stats_sum(ri, by_range, bx_range)
            bar = [
                (self.registry.name_of(c), stats.get(c, (0, 0.0))[0] / block_voxels * 100.0)
                for c in included
            ]
            # upset: pair matrix over the block-filtered rows
            rows = self.tally.block_row_mask(ri, by_range, bx_range)
            M = self.tally.pair_matrix_rows(ri, rows)
        else:
            region = self._viewport_bin_region(by_range, bx_range)
            code = self._code(dilation)
            masks = {
                c: self.fields.edt_plane(c)[:, region[0], region[1]] <= np.uint8(code)
                for c in included
            }
            total = next(iter(masks.values())).size if masks else 1
            bar = [
                (self.registry.name_of(c), int(np.count_nonzero(masks[c])) / total * 100.0)
                for c in included
            ]
            fp0, fp1, count = compute.region_tally(masks)
            M = TallyStore._accumulate_pair_matrix(fp0, fp1, count)

        bar.sort(key=lambda t: t[1], reverse=True)

        diag = np.diag(M)
        upset = []
        if min_channels == 2:
            pairs = []
            for ai in range(len(included)):
                a = included[ai]
                for b in included[ai + 1:]:
                    inter = int(M[a, b])
                    if inter == 0:
                        continue
                    union = int(diag[a] + diag[b] - inter)
                    iou = inter / union if union > 0 else 0.0
                    oc = inter / int(min(diag[a], diag[b]))
                    pairs.append((iou, oc, a, b))
            pairs.sort(reverse=True)
            for iou, oc, a, b in pairs[:limit]:
                upset.append({
                    "channels": [self.registry.name_of(a), self.registry.name_of(b)],
                    "iou": iou,
                    "overlap_coeff": oc,
                })
        return {"bar": bar, "upset": upset}

    def get_viewport_dilation_curves(
        self,
        by_range: Tuple[int, int],
        bx_range: Tuple[int, int],
        channels: list[str],
    ) -> dict[str, list[dict]]:
        """Continuous dilation curves restricted to a block range."""
        if not self.is_loaded or not channels:
            return {}
        try:
            indices = self.registry.indices_of(channels)
        except KeyError:
            return {}
        region = self._viewport_bin_region(by_range, bx_range)
        return self._curves_for(indices, region=region)

    # ──────────────────────────────────────────────
    # Drill-down block stats (Biomni)
    # ──────────────────────────────────────────────

    def get_block_channel_stats(
        self, block_y: int, block_x: int, dilation: float
    ) -> list[dict]:
        """Per-channel voxel-exact stats for one 128-voxel block, at the
        nearest tallied radius. Sorted descending by voxel_count."""
        if not self.is_loaded:
            return []
        ri = self.is_detent(dilation)
        ri = ri if ri is not None else nearest_detent_idx(dilation)
        included = set(self.registry.included_indices())
        rows = []
        for c, vc, si in self.tally.channel_stats_block(block_y, block_x, ri):
            if c not in included:
                continue
            rows.append({
                "channel": self.registry.name_of(c),
                "voxel_count": vc,
                "sum_intensity": si,
                "mean_intensity": si / vc if vc > 0 else 0.0,
                "stats_radius_um": DETENT_RADII_UM[ri],
            })
        rows.sort(key=lambda r: r["voxel_count"], reverse=True)
        return rows
