"""Constants describing the preprocessing outputs consumed by the analysis backend.

These describe properties of the data produced by BioSET_Preprocessing that are
not (yet) recorded in the outputs themselves.
"""
from __future__ import annotations

# The tallied radii are no longer hardcoded here: newer pipeline runs record
# them in the zarr root attrs (`dilate_um` / `dilate_um_effective`) and in
# meta.json, so they are read from the dataset. See `analysis/radii.py`, whose
# `FALLBACK_RADII_UM` still covers runs made before the pipeline wrote them.

# Edge length (in voxels, y and x) of the blocks used by the tally tables
# (channel_stats / tally_blocks / combos_blocks). A property of the tally data,
# not of the heatmap.
BLOCK_VOX: int = 128

# Marker used in channel names to flag acquisitions excluded from analysis.
DO_NOT_USE_MARKER: str = "(do not use)"

# Heatmap LOD level -> cell edge length in voxels (y/x; z is always full depth).
# Level 0 (16 vox ~ 2.2 um) is fine enough for cells to trace subcellular
# structure. Overridable via config.VolumeConfig.
DEFAULT_CELL_SIZES_VOX: dict[int, int] = {0: 16, 1: 64, 2: 256, 3: 1024}
