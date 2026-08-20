"""Loader and query facade over the zarr + parquet analysis outputs.

Replaces the old gzipped-SQLite ``.bioset`` backend. The public surface keeps
the old class/method names so UI call sites stay mechanical:

- ``dilation=`` kwargs now mean a radius in micrometers (continuous; the
  preprocessed radii — read from the dataset, see ``analysis/radii.py`` — are
  exact "detent" fast paths through the tally tables, anything else is computed
  from the EDT fields). These are always the *requested* radii; the *effective*
  ones are labels only and must never be passed back in as a query value.
- ``hierarchy_level=`` now selects the heatmap cell size (see
  ``constants.DEFAULT_CELL_SIZES_VOX``); global plot queries ignore it (their
  answers are level-independent).
- Channels cross this API as display-name strings (unique after registry
  filtering); indices/fingerprints are internal.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from itertools import combinations as iter_combinations
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import compute
from .constants import BLOCK_VOX, DEFAULT_CELL_SIZES_VOX
from .registry import ChannelRegistry
from .store import FieldStore, GridInfo, TallyStore


@dataclass
class AnalysisMetadata:
    channels: list[str]
    hierarchy_levels: list[dict]
    dilation_amounts: list[float]      # detent radii (µm) to QUERY with; slider ticks
    volume_bounds: dict
    radius_max_um: float = 0.0         # continuous-slider cap (set from the dataset)
    dtype_max: int = 65535             # kept for Biomni payloads; prefer image metadata
    # Radii to LABEL with. Never query with these: they floor to the next EDT
    # code, selecting a larger mask than the tally row (see analysis/radii.py).
    dilation_amounts_effective: list[float] = field(default_factory=list)
    detent_snap_um: float = 0.08       # slider magnetic-snap half-window


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
    fractions: np.ndarray       # (N,) float32 — active fraction per cell
    source_level: int = 0       # EDT pyramid level the mask came from
    exact: bool = True          # False = built from a coarse superset (preview)


@dataclass
class CombinationData:
    """A biomarker combination with aggregated overlap metrics.

    `iou` and `overlap_coeff` are ratios and so unit-free — they are comparable
    across scopes. `total_count` is NOT: `count_unit` says whether it counts raw
    voxels (the combination tables) or 1.12 µm analysis bins (the EDT fields),
    which differ by up to 256x. Never sum or compare counts across units.
    """
    channels: list[str]
    total_count: int
    iou: float = 0.0
    overlap_coeff: float = 0.0
    tiles: list[TileData] = field(default_factory=list)
    count_unit: str = "bins"            # "voxels" | "bins"
    radius_um_effective: float = 0.0    # radius the numbers describe, for display


def crop_field_to_roi(
    field: Optional[HeatmapField],
    roi_vox: Optional[Tuple[int, int, int, int]],
    margin_frac: float = 1.0,
) -> Optional[HeatmapField]:
    """Crop a HeatmapField to a viewport ROI given in voxels (x0, x1, y0, y1),
    expanded by `margin_frac` of the ROI extent on each side.

    Cells inside the (expanded) view keep full detail; only off-screen
    instances are dropped, which is what keeps fine-level glyph counts sane
    when zoomed in. Returns the original field object unchanged when the
    expanded ROI covers the whole grid (identity check tells callers whether
    a crop was applied).
    """
    if field is None or roi_vox is None or field.counts.size == 0:
        return field
    x0, x1, y0, y1 = roi_vox
    cs = field.cell_size_vox
    mx = (x1 - x0) * margin_frac
    my = (y1 - y0) * margin_frac
    cx0 = max(0, int(np.floor((x0 - mx) / cs)))
    cx1 = min(field.nx, int(np.ceil((x1 + mx) / cs)))
    cy0 = max(0, int(np.floor((y0 - my) / cs)))
    cy1 = min(field.ny, int(np.ceil((y1 + my) / cs)))
    if cx0 <= 0 and cy0 <= 0 and cx1 >= field.nx and cy1 >= field.ny:
        return field
    cy = field.cells_yx[:, 0]
    cx = field.cells_yx[:, 1]
    keep = (cy >= cy0) & (cy < cy1) & (cx >= cx0) & (cx < cx1)
    if bool(keep.all()):
        return field
    return HeatmapField(
        level=field.level,
        cell_size_vox=field.cell_size_vox,
        ny=field.ny,
        nx=field.nx,
        cells_yx=field.cells_yx[keep],
        counts=field.counts[keep],
        fractions=field.fractions[keep],
        source_level=field.source_level,
        exact=field.exact,
    )


# Sentinel for "every combination size at once" wherever a degree is expected.
# 0 rather than None because it crosses to the browser as a plain integer and
# compares cleanly against the size buttons.
ALL_DEGREES: int = 0


def _metric_of(combo: "CombinationData", metric: str) -> float:
    """The ranked field of a row, defaulting to IoU for anything unrecognised."""
    if metric == "overlap_coeff":
        return combo.overlap_coeff
    if metric == "count":
        return float(combo.total_count)
    return combo.iou


def _extend(seed: Sequence[int], pool: Sequence[int], k: int) -> list[tuple]:
    """Every way to grow `seed` to size `k` using members of `pool`.

    Used to turn the exact top pairs of a region into degree>=3 candidates,
    which are then exact-counted from masks already in hand.
    """
    need = k - len(seed)
    if need <= 0:
        return [tuple(sorted(seed))]
    rest = [p for p in pool if p not in seed]
    return [tuple(sorted(tuple(seed) + extra))
            for extra in iter_combinations(rest, need)]


class AnalysisLoader:
    """Query interface over a results directory containing
    ``colocalization.zarr`` and ``tally/``."""

    # Region-query budget in analysis bins. Above this, viewport metrics step
    # down the EDT pyramid rather than reading the full-resolution region.
    MAX_REGION_BINS = 4_000_000

    def __init__(self, cell_sizes_vox: Optional[Dict[int, int]] = None,
                 radius_max_um: Optional[float] = None):
        self.cell_sizes_vox = dict(cell_sizes_vox or DEFAULT_CELL_SIZES_VOX)
        # None = span whatever the dataset tallied, clipped by its clamp.
        self.radius_max_um = None if radius_max_um is None else float(radius_max_um)
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
        # (indices, code, source_level) -> (summed-area table, mask shape).
        # Cell size is applied by differencing, so LOD changes reuse this.
        self._colcount_cache: "OrderedDict[tuple, tuple]" = OrderedDict()

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
        self.tally = TallyStore(tally_path, self.registry, self.grid.radii)
        self._results_dir = path

        radii = self.grid.radii
        # The slider spans what the run actually tallied, not a hardcoded cap.
        cap = min(self.radius_max_um, self.grid.clamp_um) \
            if self.radius_max_um is not None else self.grid.clamp_um
        cap = max(cap, radii.max_um)
        # ...but never far enough to reach the top EDT code. That code is the
        # saturation flag ("at least clamp_um"), where every far or unwritten
        # bin sits, so querying it selects the whole volume — the far end of the
        # slider used to report every channel covering 100%.
        top = (self.grid.levels - 2) * self.grid.quant_um
        cap = min(cap, top)

        vz, vy, vx = self.grid.volume_shape_zyx
        self.metadata = AnalysisMetadata(
            channels=self.registry.display_names(),
            hierarchy_levels=[{"level": lvl} for lvl in sorted(self.cell_sizes_vox)],
            dilation_amounts=list(radii.requested_um),
            volume_bounds={"x": [0, vx], "y": [0, vy], "z": [0, vz]},
            radius_max_um=cap,
            dilation_amounts_effective=list(radii.effective_um),
            detent_snap_um=radii.snap_window_um(),
        )
        self._loaded = True
        self._warm_histograms()
        n_excluded = self.registry.n_channels - len(self.metadata.channels)
        print(
            f"[analysis] Loaded: {len(self.metadata.channels)} channels "
            f"({n_excluded} hidden), {self.tally.n_radii} tallied radii "
            f"(from {radii.source}, up to {radii.max_um:.2f} um), "
            f"grid {self.grid.grid_shape_zyx}"
        )
        return self.metadata

    def _warm_histograms(self):
        """Build the per-channel EDT histograms off the main thread.

        Coverage is O(1) per channel once these exist, but building all of them
        cold costs one full plane read each — ~8 s for 49 channels, which is
        unacceptable on the first bar-chart render and pointless to repeat every
        session. Loading the sidecar makes it instant; otherwise a daemon thread
        builds and saves it while the UI comes up.
        """
        indices = self.registry.included_indices()
        if self.fields.load_cumhist_cache(0):
            print(f"[analysis] channel histograms restored from cache ({len(indices)} channels)")
            return

        def _run():
            t0 = time.perf_counter()
            try:
                self.fields.warm_cumhists(indices, 0)
                print(f"[analysis] channel histograms warmed in "
                      f"{time.perf_counter() - t0:.1f} s — coverage is now O(1) at any radius")
            except Exception as exc:
                print(f"[analysis] histogram warm-up failed ({exc}); "
                      f"coverage will build them on demand")

        self._warm_thread = threading.Thread(
            target=_run, name="bioset-cumhist-warm", daemon=True)
        self._warm_thread.start()

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
        self._colcount_cache.clear()

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
        return self.grid.radii.idx_for(r_um) if self.grid else None

    def _nearest_detent(self, r_um: float) -> int:
        return self.grid.radii.nearest_idx(r_um)

    def detent_label_um(self, radius_idx: int) -> float:
        """Effective radius for `radius_idx` — for display, never for querying."""
        return self.grid.radii.label_um(radius_idx)

    def _code(self, r_um: float) -> int:
        return self.grid.code_for(r_um)

    # ──────────────────────────────────────────────
    # Internal: counts for channel-index sets
    # ──────────────────────────────────────────────

    def _inter_union(self, indices: Sequence[int], r_um: float) -> Tuple[int, int]:
        """(intersection, union) BIN counts at any radius, from the EDT fields.

        Always the field path, at every radius. The combination tables answer
        the same question in raw voxels; keeping this one in bins means
        `_inter_union`, `_channel_counts` and `_curves_for` share a unit and
        their ratios can be compared. See `_ranked_combos` for the voxel side.
        """
        code = self._code(r_um)
        if len(indices) == 1:
            n = int(self.fields.channel_cumhist(indices[0])[code])
            return n, n
        planes = [self.fields.edt_plane(c) for c in indices]
        inter_cum, union_cum = compute.dilation_curve(planes, self.grid.levels)
        return int(inter_cum[code]), int(union_cum[code])

    def _channel_counts(self, indices: Sequence[int], r_um: float) -> List[int]:
        """Per-channel BIN counts at any radius — O(1) from the cached histograms."""
        code = self._code(r_um)
        return [int(self.fields.channel_cumhist(c)[code]) for c in indices]

    def _decode_fp(self, fp0: int, fp1: int) -> List[int]:
        """Channel indices named by a 128-bit fingerprint."""
        a, b = int(fp0), int(fp1)
        out = [i for i in range(64) if (a >> i) & 1]
        out += [64 + i for i in range(64) if (b >> i) & 1]
        return out

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
        channel_filter: Optional[Sequence[str]] = None,
        metric: str = "iou",
        require_any: Optional[Sequence[str]] = None,
    ) -> list[CombinationData]:
        """Top combinations, best metric first. Counts are RAW VOXELS.

        `min_channels` is an EXACT degree, not a minimum (the state variable's
        name predates that). Pass `ALL_DEGREES` (0) for every size at once,
        merged into one strictly descending list.

        `channel_filter` restricts to combinations lying entirely inside that
        set, applied to the whole table BEFORE the ranking is truncated —
        filtering the top-N afterwards silently loses rows whose combinations
        rank below the cutoff globally, which for a handful of low-abundance
        channels means losing all of them.

        `require_any` keeps only combinations naming AT LEAST ONE of those
        channels. The two filters compose as: exclude anything containing a
        channel outside `channel_filter`, then keep what touches
        `require_any` — "combinations involving what I am looking at, and
        nothing I have hidden".

        `metric` is the field ranked on, so the list matches what the plot
        draws instead of always being the top-N by IoU.
        """
        if not self.is_loaded:
            return []
        degree = int(min_channels)
        if degree == ALL_DEGREES:
            return self._ranked_all_degrees(dilation, limit, channel_filter,
                                            metric, require_any)
        return self._ranked_combos(dilation, degree, limit,
                                   channel_filter=channel_filter, metric=metric,
                                   require_any=require_any)

    @property
    def max_combo_degree(self) -> int:
        """Largest combination size the ranked tables cover (0 if none)."""
        return int(self.tally.max_degree) if self.tally else 0

    def _ranked_all_degrees(
        self,
        r_um: float,
        limit: int,
        channel_filter: Optional[Sequence[str]] = None,
        metric: str = "iou",
        require_any: Optional[Sequence[str]] = None,
    ) -> list[CombinationData]:
        """Every degree from 2 up, merged into one descending list.

        Note the ranking is genuinely dominated by pairs: adding a channel can
        only shrink an intersection while growing the union, so IoU falls with
        degree. That is a property of the measure, not of this merge.
        """
        out: list[CombinationData] = []
        for degree in range(2, self.max_combo_degree + 1):
            out.extend(self._ranked_combos(
                r_um, degree, limit, channel_filter=channel_filter,
                metric=metric, require_any=require_any))
        out.sort(key=lambda c: _metric_of(c, metric), reverse=True)
        return out[:max(1, int(limit))]

    def _indices_for(self, names: Sequence[str]) -> list[int]:
        known = set(self.registry.display_names())
        return [self.registry.index_of(c) for c in names if c in known]

    def _any_row_mask(self, rows, names: Sequence[str]):
        """Rows naming AT LEAST ONE of `names` — the OR half of the filter."""
        indices = self._indices_for(names)
        if not indices:
            return None
        m0, m1 = self.registry.fp_masks(indices)
        return (((rows.fp0 & m0) != np.uint64(0))
                | ((rows.fp1 & m1) != np.uint64(0)))

    def _subset_row_mask(self, rows, channel_filter: Sequence[str]):
        """Rows whose fingerprint lies entirely inside `channel_filter`.

        Returns None when the filter names nothing usable, and an all-False
        mask when it names channels the registry does not know.
        """
        try:
            indices = self.registry.indices_of([c for c in channel_filter])
        except KeyError:
            indices = [self.registry.index_of(c) for c in channel_filter
                       if c in self.registry.display_names()]
        if not indices:
            return None
        m0, m1 = self.registry.fp_masks(indices)
        return (((rows.fp0 & ~m0) == np.uint64(0))
                & ((rows.fp1 & ~m1) == np.uint64(0)))

    def _ranked_combos(
        self,
        r_um: float,
        degree: int,
        limit: int,
        row_mask: Optional[np.ndarray] = None,
        channel_filter: Optional[Sequence[str]] = None,
        metric: str = "iou",
        require_any: Optional[Sequence[str]] = None,
    ) -> list[CombinationData]:
        """Top rows of one degree from the combination table.

        Off-detent radii rank at the nearest tallied radius: an exact recount
        would need one full-volume pass per candidate (~2-4 s for a page of
        pairs), whereas the table is a sort over rows already computed. The
        radius actually used is reported on every row.
        """
        if not self.tally.has_combos or degree < 1:
            return self._ranked_combos_legacy(r_um, degree, limit)
        ri = self.is_detent(r_um)
        ri = ri if ri is not None else self._nearest_detent(r_um)
        rows = self.tally.combos(ri, degree)
        if rows is None or len(rows) == 0:
            return []

        if metric == "overlap_coeff" and rows.overlap_coeff is not None:
            score = rows.overlap_coeff
        elif rows.iou is not None:
            score = rows.iou
        else:
            score = rows.n_inter.astype(float)

        idx = np.arange(len(rows))
        if row_mask is not None:
            idx = idx[row_mask]
        if channel_filter:
            # Exclusion: drop anything naming a channel the user unticked.
            sub = self._subset_row_mask(rows, channel_filter)
            if sub is not None:
                idx = idx[sub[idx]]
        if require_any:
            # Inclusion (OR): keep only rows naming at least one of these.
            anym = self._any_row_mask(rows, require_any)
            if anym is not None:
                idx = idx[anym[idx]]
        if idx.size == 0:
            return []
        order = idx[np.argsort(score[idx])[::-1][:max(1, int(limit))]]

        label = self.detent_label_um(ri)
        out = []
        for i in order:
            if rows.n_inter[i] <= 0:
                continue
            names = [self.registry.name_of(c)
                     for c in self._decode_fp(rows.fp0[i], rows.fp1[i])]
            out.append(CombinationData(
                channels=names,
                total_count=int(rows.n_inter[i]),
                iou=float(rows.iou[i]) if rows.iou is not None else 0.0,
                overlap_coeff=(float(rows.overlap_coeff[i])
                               if rows.overlap_coeff is not None else 0.0),
                count_unit="voxels",
                radius_um_effective=label,
            ))
        return out

    def _ranked_combos_legacy(self, r_um: float, degree: int, limit: int
                              ) -> list[CombinationData]:
        """Ranking for datasets with no combination table (pre-combos runs).

        Pairs come from the fingerprint co-occurrence matrix; higher degrees
        from k-subsets of the highest-count fingerprints, exact-counted. Both
        are in BINS. This is the old behaviour, kept only for those datasets.
        """
        if self.tally.n_radii == 0:
            return []
        ri = self.is_detent(r_um)
        base_ri = ri if ri is not None else self._nearest_detent(r_um)
        idx = self.registry.included_indices()
        label = self.detent_label_um(base_ri)

        if degree == 2:
            M = self.tally.legacy_pair_matrix(base_ri)
            diag = np.diag(M)
            scored = []
            for ai, a in enumerate(idx):
                if diag[a] == 0:
                    continue
                for b in idx[ai + 1:]:
                    inter = int(M[a, b])
                    if inter == 0:
                        continue
                    union = int(diag[a] + diag[b] - inter)
                    scored.append((
                        inter / union if union > 0 else 0.0,
                        inter / int(min(diag[a], diag[b])),
                        inter, a, b,
                    ))
            scored.sort(key=lambda t: t[0], reverse=True)
            return [
                CombinationData(
                    channels=[self.registry.name_of(a), self.registry.name_of(b)],
                    total_count=inter, iou=iou, overlap_coeff=oc,
                    count_unit="bins", radius_um_effective=label,
                )
                for iou, oc, inter, a, b in scored[:limit]
            ]

        fp0, fp1, count = self.tally._legacy_global_rows(base_ri)
        order = np.argsort(count)[::-1][:4096]
        cand: dict[tuple, None] = {}
        for i in order:
            bits = [c for c in self._decode_fp(fp0[i], fp1[i])
                    if c in self.registry._index_by_name.values()]
            if len(bits) < degree:
                continue
            for combo in iter_combinations(sorted(bits), degree):
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
            iou, oc = self._metrics(
                combo, inter, union, self._channel_counts(list(combo), r_um))
            scored.append((iou, oc, inter, combo))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            CombinationData(
                channels=[self.registry.name_of(c) for c in combo],
                total_count=inter, iou=iou, overlap_coeff=oc,
                count_unit="bins", radius_um_effective=label,
            )
            for iou, oc, inter, combo in scored[:limit]
        ]

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
        """Combinations involving the filter channels, best metric first.

        - exact_match: just the one combination.
        - otherwise: every subset (size>=2) of the filter set, plus every pair
          (filter channel x any other channel) — the same set the candidate-pool
          version produced, but selected exhaustively with one row mask instead
          of pre-scored and truncated.
        """
        if not self.is_loaded or not channel_filter:
            return []
        try:
            f_idx = self.registry.indices_of(channel_filter)
        except KeyError:
            return []

        if exact_match:
            return self._exact_combination(f_idx, channel_filter, dilation)

        if not self.tally.has_combos:
            return self._filtered_combinations_legacy(f_idx, dilation, limit)

        ri = self.is_detent(dilation)
        ri = ri if ri is not None else self._nearest_detent(dilation)
        m0, m1 = self.registry.fp_masks(f_idx)

        out: list[CombinationData] = []
        for degree in range(2, self.max_combo_degree + 1):
            rows = self.tally.combos(ri, degree)
            if rows is None or len(rows) == 0:
                continue
            # subsets of the filter set ...
            keep = (rows.fp0 & ~m0) == np.uint64(0)
            keep &= (rows.fp1 & ~m1) == np.uint64(0)
            # ... plus pairs that touch it
            if degree == 2:
                keep |= ((rows.fp0 & m0) != np.uint64(0)) | ((rows.fp1 & m1) != np.uint64(0))
            out.extend(self._ranked_combos(dilation, degree, limit, row_mask=keep))

        out.sort(key=lambda c: c.iou, reverse=True)
        return out[:limit]

    def _exact_combination(self, indices: Sequence[int], names: Sequence[str],
                           r_um: float) -> list[CombinationData]:
        """The one combination named by `indices`, from the table when it covers
        that degree, otherwise counted from the EDT fields."""
        ri = self.is_detent(r_um)
        if self.tally.has_combos and ri is not None and len(indices) <= self.max_combo_degree:
            rows = self.tally.combos(ri, len(indices))
            if rows is not None and len(rows):
                m0, m1 = self.registry.fp_masks(indices)
                hit = np.flatnonzero((rows.fp0 == m0) & (rows.fp1 == m1))
                if hit.size:
                    i = int(hit[0])
                    return [CombinationData(
                        channels=self.registry.sort_names(names),
                        total_count=int(rows.n_inter[i]),
                        iou=float(rows.iou[i]) if rows.iou is not None else 0.0,
                        overlap_coeff=(float(rows.overlap_coeff[i])
                                       if rows.overlap_coeff is not None else 0.0),
                        count_unit="voxels",
                        radius_um_effective=self.detent_label_um(ri),
                    )]
        # Any degree, any radius — exact from the fields, in bins.
        inter, union = self._inter_union(list(indices), r_um)
        if inter == 0:
            return []
        iou, oc = self._metrics(
            indices, inter, union, self._channel_counts(list(indices), r_um))
        return [CombinationData(
            channels=self.registry.sort_names(names),
            total_count=inter, iou=iou, overlap_coeff=oc, count_unit="bins",
        )]

    def _filtered_combinations_legacy(self, f_idx, dilation, limit
                                      ) -> list[CombinationData]:
        """Pre-combos datasets: pre-score candidates, then exact-count (bins)."""
        combos: dict[tuple, None] = {}
        if 2 <= len(f_idx) <= 10:
            for size in range(2, len(f_idx) + 1):
                for c in iter_combinations(sorted(f_idx), size):
                    combos[c] = None
        for a in f_idx:
            for b in self.registry.included_indices():
                if b != a:
                    combos[tuple(sorted((a, b)))] = None

        base_ri = self.is_detent(dilation)
        base_ri = base_ri if base_ri is not None else self._nearest_detent(dilation)
        M = self.tally.legacy_pair_matrix(base_ri)

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
                total_count=inter, iou=iou, overlap_coeff=oc, count_unit="bins",
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
        """Per-channel coverage percent, descending — as a fraction of 1.12 µm
        analysis bins, at every radius.

        One unit at all radii, deliberately. Reporting voxel-exact percentages
        on a tallied radius and bin percentages between them made the axis
        change meaning mid-drag, for two numbers that are both exact but differ
        by up to 256x. The voxel-exact figure is still available at tallied
        radii through `channel_voxel_coverage`, as an annotation rather than as
        the series. O(1) per channel from the cached cumulative histograms.
        """
        if not self.is_loaded:
            return []
        code = self._code(dilation)
        total = self.grid.n_bins
        results = [
            (self.registry.name_of(c),
             int(self.fields.channel_cumhist(c)[code]) / total * 100.0)
            for c in self.registry.included_indices()
        ]
        results.sort(key=lambda t: t[1], reverse=True)
        return results

    def channel_voxel_coverage(self, dilation: float) -> dict[str, float]:
        """Voxel-exact per-channel coverage percent, or {} away from a tallied
        radius. For annotating the bin-based series, not for replacing it."""
        if not self.is_loaded:
            return {}
        ri = self.is_detent(dilation)
        if ri is None:
            return {}
        stats = self.tally.channel_stats_sum(ri)
        total = self.grid.n_voxels
        return {
            self.registry.name_of(c): stats.get(c, (0, 0.0))[0] / total * 100.0
            for c in self.registry.included_indices()
        }

    def coverage_is_voxel_exact(self, dilation: float) -> bool:
        """Deprecated: the coverage series is now always in bins. Reports only
        whether a voxel-exact annotation is available."""
        return self.is_detent(dilation) is not None

    # ──────────────────────────────────────────────
    # Heatmap field
    # ──────────────────────────────────────────────

    def get_heatmap_field(
        self,
        channels: list[str],
        dilation: float,
        hierarchy_level: int,
        *,
        source_level: Optional[int] = None,
    ) -> Optional[HeatmapField]:
        """Per-cell active-bin counts/fractions for a combination.

        `source_level` picks which EDT pyramid level the mask is built from.
        Level 0 is exact; None means level 0. Coarser levels are cheap enough to
        keep a drag responsive (172 ms -> 16 ms for two channels) and, being
        min-reduced, they never drop a cell that has signal — measured, every
        occupied cell survives and its active *fraction* only ever rises. They
        are flagged `exact=False` and every number shown elsewhere still comes
        from level 0.

        `counts` are in bins **of the source level**, so they are comparable
        across cell sizes but NOT across source levels; `fractions` are
        comparable throughout and are what the display should key on.
        Single channel at r=0 gets voxel-exact fractions from the occupancy array.
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
        src = self._clamp_source_level(source_level)

        key = (tuple(sorted(indices)), code, level, src)
        cached = self._field_cache.get(key)
        if cached is not None:
            self._field_cache.move_to_end(key)
            return cached

        # The column map and its summed-area table depend only on the
        # combination, radius and source level — not on the cell size. Caching
        # them makes an LOD change a four-corner difference instead of another
        # full pass over 46M elements.
        table, shape = self._column_table(indices, code, src)
        z, gy, gx = shape
        # A coarse source level halves y/x, so the cell must shrink to match if
        # the cells are to cover the same ground.
        cell_bins = max(1, (cell_vox // self.grid.bin_factors[1]) >> src)
        sums = compute.cell_sums_from_sat(table, cell_bins)
        denoms = compute.cell_denoms(z, gy, gx, cell_bins)

        if len(indices) == 1 and code == 0 and src == 0:
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
            source_level=src,
            exact=(src == 0),
        )
        self._field_cache[key] = result
        while len(self._field_cache) > 8:
            self._field_cache.popitem(last=False)
        return result

    def field_is_cached(self, channels: list[str], dilation: float,
                        hierarchy_level: int, source_level: int = 0) -> bool:
        """Whether `get_heatmap_field` would return without recomputing.

        Lets callers skip a coarse preview pass when the exact field is already
        a cache hit — drawing the preview then costs a second glyph rebuild on
        the main thread for no gain.
        """
        if not self.is_loaded or not channels:
            return False
        try:
            indices = tuple(sorted(self.registry.indices_of(channels)))
        except KeyError:
            return False
        code = self._code(dilation)
        src = self._clamp_source_level(source_level)
        if (indices, code, int(hierarchy_level), src) in self._field_cache:
            return True
        # The summed-area table is the expensive part; with it cached, applying
        # a different cell size is a four-corner difference.
        return (indices, code, src) in self._colcount_cache

    def _clamp_source_level(self, source_level: Optional[int]) -> int:
        if not source_level:
            return 0
        return max(0, min(int(source_level), self.fields.n_edt_levels - 1))

    def _column_table(self, indices: Sequence[int], code: int, src: int):
        """Summed-area table of the combination's per-column bin counts."""
        key = (tuple(sorted(indices)), code, src)
        hit = self._colcount_cache.get(key)
        if hit is not None:
            self._colcount_cache.move_to_end(key)
            return hit
        planes = [self.fields.edt_plane(c, src) for c in indices]
        mask = compute.combination_mask(planes, code)
        entry = (compute.sat(compute.column_counts(mask)), mask.shape)
        self._colcount_cache[key] = entry
        while len(self._colcount_cache) > 6:
            self._colcount_cache.popitem(last=False)
        return entry

    def coarse_level_for_interaction(self, hierarchy_level: int) -> int:
        """EDT level cheap enough to rebuild the field while the user is moving.

        The pyramid halves y/x per level and the LOD cell sizes are powers of
        two, so level N keeps cells bit-aligned with level 0 — the preview lines
        up with the exact field that replaces it.
        """
        if not self.is_loaded:
            return 0
        cell_vox = self.cell_sizes_vox.get(int(hierarchy_level), 16)
        cell_bins = max(1, cell_vox // self.grid.bin_factors[1])
        return self._clamp_source_level(max(0, int(cell_bins).bit_length() - 2))

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
        # Stop one code short of the top. The highest code is the EDT's
        # saturation flag ("at least clamp_um"), not a measured distance: every
        # far or unwritten bin sits there, so `edt <= 255` selects the entire
        # volume. Including it put a vertical jump to 100% at the end of every
        # curve — which also dragged the y-axis domain up to 100% and squashed
        # the real range (0.7-33% on the reference pair) into the bottom.
        max_code = min(self._code(self.metadata.radius_max_um), self.grid.levels - 2)
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
        combo_channels: Optional[Sequence[str]] = None,
    ) -> dict:
        """Bar + upset data restricted to a block range, exact at ANY radius.

        Returns {"bar": [(name, pct)], "upset": [{channels, iou, overlap_coeff}]}.

        `combo_channels` restricts which channels may appear in a combination —
        the UpSet dialog's selection. The bar is deliberately NOT restricted by
        it: coverage has its own channel filter, and the per-channel counts come
        free with the pair matrix. Without this the viewport UpSet ignored the
        dialog entirely and had to be filtered after the fact, which both leaked
        unticked channels in the unfiltered mode and threw away most of the work.

        Computed from region-scoped EDT reads, not from the per-block
        combination table: that table keeps only the top 200 rows per block, so
        summing it across a region recovers a median 32% of the true count at
        degree 2 and ~1% at degree 3 (its own `residual` column is non-zero on
        90% of rows). Reading the region instead is exact and, because it pulls
        only the intersecting chunks, costs ~150 ms for 49 channels over a
        typical viewport. Counts are BINS.
        """
        if not self.is_loaded:
            return {"bar": [], "upset": [], "exact": True, "unit": "bins"}
        included = self.registry.included_indices()
        if not included:
            return {"bar": [], "upset": [], "exact": True, "unit": "bins"}

        ys, xs = self._viewport_bin_region(by_range, bx_range)
        if ys.stop <= ys.start or xs.stop <= xs.start:
            return {"bar": [], "upset": [], "exact": True, "unit": "bins"}

        # Cost scales with region x channels. A zoomed-out "local" scope can
        # approach the whole volume (47M bins x 49 channels ~ 7 s), so step down
        # the pyramid until the read is bounded. Level 0 is exact; coarser
        # levels are min-reduced supersets, so the ratios stay monotone but are
        # no longer exact — reported as `exact` for the caller to surface.
        level = 0
        zdim = self.grid.grid_shape_zyx[0]
        while (level + 1 < self.fields.n_edt_levels
               and (ys.stop - ys.start) * (xs.stop - xs.start) * zdim
               > self.MAX_REGION_BINS):
            level += 1
            ys = slice(ys.start // 2, max(ys.start // 2 + 1, -(-ys.stop // 2)))
            xs = slice(xs.start // 2, max(xs.start // 2 + 1, -(-xs.stop // 2)))

        code = self._code(dilation)
        masks = [self.fields.edt_region(c, ys, xs, level) <= np.uint8(code)
                 for c in included]

        total = max(1, masks[0].size)
        M = compute.pair_intersection_matrix(masks)
        diag = np.diag(M)

        bar = [(self.registry.name_of(c), int(diag[i]) / total * 100.0)
               for i, c in enumerate(included)]
        bar.sort(key=lambda t: t[1], reverse=True)

        k = int(min_channels)
        # ALL_DEGREES asks for every size merged; degree 1 is not offered (a
        # set's IoU against itself is always 1.0), so it maps to the same thing.
        want_all = k <= 1
        degrees = list(range(2, self.max_combo_degree + 1)) if want_all else [k]

        # Positions (into `included`) a combination may use.
        if combo_channels:
            wanted = set(combo_channels)
            allowed = [i for i, c in enumerate(included)
                       if self.registry.name_of(c) in wanted]
        else:
            allowed = list(range(len(included)))
        allowed_set = set(allowed)

        # Pairs come straight off the matrix; higher degrees extend the best
        # pairs and are exact-counted by AND-ing the region masks already read.
        pairs = []
        for ai, i in enumerate(allowed):
            if diag[i] == 0:
                continue
            for j in allowed[ai + 1:]:
                inter = int(M[i, j])
                if inter == 0:
                    continue
                union = int(diag[i] + diag[j] - inter)
                pairs.append((
                    inter / union if union > 0 else 0.0,
                    inter / int(min(diag[i], diag[j])),
                    inter, i, j,
                ))
        pairs.sort(reverse=True)

        scored = []
        if 2 in degrees:
            scored.extend((iou, oc, cnt, (i, j))
                          for iou, oc, cnt, i, j in pairs[:limit])
        for k_deg in [d for d in degrees if d >= 3]:
            seeds = pairs[: max(limit, 40)]
            hot = [i for i, _ in sorted(enumerate(diag), key=lambda t: -t[1])
                   if i in allowed_set][:24]
            seen: set[tuple] = set()
            for _, _, _, i, j in seeds:
                for extra in _extend(sorted((i, j)), hot, k_deg):
                    if extra in seen:
                        continue
                    seen.add(extra)
                    acc = masks[extra[0]]
                    for m in extra[1:]:
                        acc = acc & masks[m]
                    inter = int(np.count_nonzero(acc))
                    if inter == 0:
                        continue
                    uni = masks[extra[0]]
                    for m in extra[1:]:
                        uni = uni | masks[m]
                    union = int(np.count_nonzero(uni))
                    mn = int(min(diag[m] for m in extra))
                    scored.append((
                        inter / union if union > 0 else 0.0,
                        inter / mn if mn > 0 else 0.0,
                        inter, extra,
                    ))
                if len(seen) >= 400:
                    break
        scored.sort(reverse=True, key=lambda t: t[0])
        scored = scored[:limit]

        upset = [
            {"channels": [self.registry.name_of(included[m]) for m in members],
             "iou": iou, "overlap_coeff": oc, "count": cnt}
            for iou, oc, cnt, members in scored
        ]
        return {"bar": bar, "upset": upset,
                "exact": level == 0, "unit": "bins"}

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
        ri = ri if ri is not None else self._nearest_detent(dilation)
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
                "stats_radius_um": self.detent_label_um(ri),
            })
        rows.sort(key=lambda r: r["voxel_count"], reverse=True)
        return rows

    def region_channel_stats(
        self,
        by_range: Optional[Tuple[int, int]],
        bx_range: Optional[Tuple[int, int]],
        dilation: float,
    ) -> dict:
        """Per-channel stats over a block range, in the schema the VLM expects.

        `{"dtype_max", "total_voxels", "channels": {name: {"mean_intensity",
        "segmented_voxels"}}}` — the same shape the tile picker used to build
        for one block, now over an arbitrary region (None ranges = whole
        volume). The agent reads `mean_intensity / dtype_max` as expression
        level and `segmented_voxels / total_voxels` as coverage, so both
        denominators have to describe the same region as the numerators.

        `mean_intensity` comes from the SUMMED region totals. Averaging
        per-block means instead would weight a block holding a hundred voxels
        the same as one holding a hundred thousand.

        It is `None` where the dataset carries no intensity for the region —
        mis_v3 leaves most of `sum_intensity` NaN. Null rather than 0.0,
        because 0.0 reads as "measured, and absent", which is a different
        claim. `intensity_available` says whether any of it was real.

        Every included channel appears, zeros and all: the agent is told to
        reason about markers that are absent here, and the store only returns
        the ones with signal.
        """
        if not self.is_loaded:
            return {}
        ri = self.is_detent(dilation)
        ri = ri if ri is not None else self._nearest_detent(dilation)
        sums = self.tally.channel_stats_region(ri, by_range, bx_range)

        # Z is in VOXELS, from the volume bounds — `grid_shape_zyx` counts
        # bins, and mixing the two understates the denominator ~4x, which
        # would let segmented_voxels exceed total_voxels.
        bounds = self.metadata.volume_bounds if self.metadata else {}
        z_depth = max(1, bounds["z"][1] - bounds["z"][0]) if "z" in bounds else 1
        ny = self.tally.n_blocks_y if by_range is None else by_range[1] - by_range[0]
        nx = self.tally.n_blocks_x if bx_range is None else bx_range[1] - bx_range[0]
        total_voxels = max(0, ny) * max(0, nx) * BLOCK_VOX * BLOCK_VOX * z_depth

        channels = {}
        any_intensity = False
        for c in self.registry.included_indices():
            vc, si, vc_known = sums.get(c, (0, 0.0, 0))
            mean = (si / vc_known) if vc_known > 0 else None
            any_intensity = any_intensity or mean is not None
            channels[self.registry.name_of(c)] = {
                "mean_intensity": mean,
                "segmented_voxels": int(vc),
            }
        return {
            "dtype_max": int(self.metadata.dtype_max) if self.metadata else 65535,
            "total_voxels": int(total_voxels),
            "intensity_available": any_intensity,
            "stats_radius_um": self.detent_label_um(ri),
            "channels": channels,
        }
