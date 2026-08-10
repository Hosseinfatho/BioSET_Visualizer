"""Constants describing the preprocessing outputs consumed by the analysis backend.

These describe properties of the data produced by BioSET_Preprocessing that are
not (yet) recorded in the outputs themselves.
"""
from __future__ import annotations

# Radii (in micrometers) at which the tally parquets were computed, indexed by
# `radius_idx`.
#
# NOTE: the preprocessing pipeline does NOT write this mapping into either the
# zarr attrs or the tally directory — it lives only in the pipeline's config
# (`bioset_preprocessing.config.dilate_um`). If a future pipeline version stores
# it (e.g. as a `tally_radii_um` zarr attr), prefer that over this constant.
# `TallyStore` validates that `radius_idx.max() + 1 == len(DETENT_RADII_UM)` and
# fails loudly on mismatch.
DETENT_RADII_UM: tuple[float, ...] = (0.0, 0.5, 1.0, 1.5, 2.0)

# Edge length (in voxels, y and x) of the blocks used by tally.parquet /
# channel_stats.parquet. A property of the tally data, not of the heatmap.
BLOCK_VOX: int = 128

# Marker used in channel names to flag acquisitions excluded from analysis.
DO_NOT_USE_MARKER: str = "(do not use)"

# Heatmap LOD level -> cell edge length in voxels (y/x; z is always full depth).
# Level 0 (16 vox ~ 2.2 um) is fine enough for cells to trace subcellular
# structure. Overridable via config.VolumeConfig.
DEFAULT_CELL_SIZES_VOX: dict[int, int] = {0: 16, 1: 64, 2: 256, 3: 1024}


def nearest_detent_idx(r_um: float) -> int:
    """Index of the tallied radius closest to `r_um`."""
    return min(range(len(DETENT_RADII_UM)), key=lambda i: abs(DETENT_RADII_UM[i] - r_um))


def detent_idx(r_um: float, eps: float = 1e-6):
    """radius_idx if `r_um` is (within eps of) a tallied radius, else None."""
    for i, d in enumerate(DETENT_RADII_UM):
        if abs(r_um - d) <= eps:
            return i
    return None
