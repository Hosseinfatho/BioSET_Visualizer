"""Array primitives over the EDT/occ fields.

Ported/adapted from BioSET_Preprocessing's `query.py` (no dependency on that
package). Everything is arithmetic over per-channel arrays: a dilated mask at
radius r is `edt <= code`, so no combination or radius is ever a lookup.

All functions take already-selected per-channel planes (sequences of arrays of
identical shape) and are numpy-first with an `xp` seam so cupy arrays work
transparently if a GPU path is added later.
"""
from __future__ import annotations

from typing import Dict, Sequence, Tuple

import numpy as np


def _xp(a):
    mod = type(a).__module__
    if mod.startswith("cupy"):
        import cupy
        return cupy
    return np


def combination_mask(planes: Sequence, code: int):
    """Bins within `code` of **every** channel — the co-localization mask."""
    if len(planes) == 0:
        raise ValueError("need at least one channel plane")
    out = planes[0] <= code
    for p in planes[1:]:
        out &= p <= code
    return out


def union_mask(planes: Sequence, code: int):
    out = planes[0] <= code
    for p in planes[1:]:
        out |= p <= code
    return out


def degree_map(planes: Sequence, code: int, dtype=np.uint8):
    """How many of the channels are within `code` of each bin."""
    xp = _xp(planes[0])
    acc = xp.zeros(planes[0].shape, dtype=dtype)
    for p in planes:
        acc += (p <= code).astype(dtype)
    return acc


def at_least_k(planes: Sequence, code: int, k: int):
    return degree_map(planes, code) >= k


def intersection_bounds(occ_planes: Sequence, voxels_per_bin: int = 256):
    """Bracket the true *voxel* intersection inside each bin, at r=0.

        upper = min_c occ_c                                   (cannot overlap more)
        lower = max(0, sum_c occ_c - (k-1) * voxels_per_bin)  (pigeonhole)
    """
    xp = _xp(occ_planes[0])
    k = len(occ_planes)
    upper = occ_planes[0].astype(xp.int32)
    total = upper.copy()
    for p in occ_planes[1:]:
        s = p.astype(xp.int32)
        upper = xp.minimum(upper, s)
        total += s
    lower = xp.maximum(total - (k - 1) * int(voxels_per_bin), 0)
    return lower, upper


def dilation_curve(planes: Sequence, levels: int = 256) -> Tuple[np.ndarray, np.ndarray]:
    """Intersection and union bin counts at *every* radius code, in one pass.

    The intersection-vs-radius curve is the cumulative histogram of the per-bin
    **max** of the member distance maps, and the union curve that of the **min**.
    Returns (inter_cum, union_cum), each int64 of length `levels`; index by a
    radius code to get counts at that radius.
    """
    xp = _xp(planes[0])
    mx = planes[0].copy()
    mn = planes[0].copy()
    for p in planes[1:]:
        mx = xp.maximum(mx, p)
        mn = xp.minimum(mn, p)
    inter = xp.bincount(mx.ravel(), minlength=levels)[:levels].cumsum()
    union = xp.bincount(mn.ravel(), minlength=levels)[:levels].cumsum()
    return inter, union


def per_channel_curves(planes: Sequence, levels: int = 256) -> np.ndarray:
    """Per-channel cumulative EDT histograms: (k, levels) int64.

    `out[i][code]` = number of bins of channel i within that radius code.
    """
    xp = _xp(planes[0])
    out = xp.stack([
        xp.bincount(p.ravel(), minlength=levels)[:levels].cumsum() for p in planes
    ])
    return out


def region_tally(masks_by_index: Dict[int, np.ndarray]):
    """Exact-fingerprint distribution over a region, from per-channel bool masks.

    `masks_by_index` maps channel index (0..127) -> bool array (same shape each).
    Returns (fp0, fp1, count) uint64/uint64/int64 arrays — one row per distinct
    fingerprint present in the region, including the empty fingerprint.
    Channel c lives at word c // 64, bit c % 64.
    """
    indices = list(masks_by_index)
    if not indices:
        raise ValueError("need at least one channel mask")
    first = masks_by_index[indices[0]]
    xp = _xp(first)
    w0 = xp.zeros(first.shape, dtype=xp.uint64)
    w1 = xp.zeros(first.shape, dtype=xp.uint64)
    one = xp.uint64(1)
    for c in indices:
        bit = masks_by_index[c].astype(xp.uint64) << xp.uint64(c % 64)
        if c // 64 == 0:
            w0 |= bit
        else:
            w1 |= bit
    pairs = xp.empty(first.size, dtype=[("a", xp.uint64), ("b", xp.uint64)])
    pairs["a"] = w0.ravel()
    pairs["b"] = w1.ravel()
    uniq, counts = xp.unique(pairs, return_counts=True)
    return uniq["a"].copy(), uniq["b"].copy(), counts.astype(xp.int64)


def cell_reduce(arr: np.ndarray, cell_bins_yx: int) -> Tuple[np.ndarray, np.ndarray]:
    """Reduce a (z, y, x) array to a per-cell 2D sum, collapsing z fully and
    grouping y/x into cells of `cell_bins_yx` bins.

    The grid is padded with zeros to whole cells; `denoms` carries the TRUE
    number of bins per cell (z included, edge cells smaller), so
    `sums / denoms` is an exact fraction everywhere.

    Returns (sums, denoms), both shape (ny_cells, nx_cells), int64.
    """
    xp = _xp(arr)
    z, y, x = arr.shape
    cb = int(cell_bins_yx)
    ny, nx = -(-y // cb), -(-x // cb)
    pad_y, pad_x = ny * cb - y, nx * cb - x
    a = arr
    if pad_y or pad_x:
        a = xp.pad(a, ((0, 0), (0, pad_y), (0, pad_x)))
    sums = (
        a.reshape(z, ny, cb, nx, cb)
        .sum(axis=(0, 2, 4), dtype=xp.int64)
    )
    edge_y = xp.full(ny, cb, dtype=xp.int64)
    edge_x = xp.full(nx, cb, dtype=xp.int64)
    if pad_y:
        edge_y[-1] = cb - pad_y
    if pad_x:
        edge_x[-1] = cb - pad_x
    denoms = z * xp.outer(edge_y, edge_x)
    return sums, denoms
