"""Feed bioset's meshes and colocalization into the vendored label engine.

The reference application read its label inputs off disk: `.glb` tile grids it
assembled itself and three precomputed `.npy` heatmaps. bioset already has both
— streamed mesh tiles in `scene/meshes.py` and exact colocalization in the
analysis loader — so this module is the adapter, and none of that file loading
was ported.

Two things here are worth knowing before changing them.

**Everything is read at radius 0.** Colocalization sites mean "these two markers
physically overlap", and at r=0 that is the literal voxel intersection rather
than an intersection of dilated shells. The dilation slider does not enter into
label placement at all.

**The grid mapping is identity, and that is checked, not assumed.** The engine
wants `world_x = origin_x + (col + 0.5) * bin_w` with array `[row, col]` mapping
to `[y, x]`, no flip and no transpose. bioset's own glyph heatmap places cells
at `(cx + 0.5) * cell_size_vox * sx` (`scene/heatmap.py`), which is the same
convention with origin 0. INTEGRATION.md section 6 calls this out as the first
thing to re-verify on a new dataset; `t_labels.py` does verify it.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# Labels are only offered when the view is down to a manageable patch of
# tissue. A tile is 512 voxels ~ 72 um square, so 64 of them is a ~580 um
# field. The successive limits here (4, then 16, then this) were each tighter
# than people actually work at; the real constraint is not distance but how
# much geometry a labelling pass has to read and solve, and that is bounded
# separately by MAX_COMPONENTS_PER_CHANNEL and MAX_COLOC_SITES.
#
# Reading 64 tiles' geometry costs ~67 ms on the worker (measured), so the
# cost of a wider gate is small and paid once per Label press.
MAX_LABEL_TILES = 64

# Half a voxel. Welding is what makes a cell straddling a tile seam ONE
# connected component instead of two, i.e. one label instead of a duplicate
# pair — see INTEGRATION.md section 2.1. Appending without it was the bug.
WELD_TOLERANCE_VOX = 0.5

# Cell edge for the colocalization/occupancy grids, in voxels. 16 gives ~2.2 um
# bins, so a four-tile viewport is ~64 bins across — fine enough to separate
# neighbouring cells, coarse enough that region labelling stays trivial.
SITE_CELL_VOX = 16


def viewport_tiles(mesh_mgr, roi_vox, channels=None):
    """Manifest tiles intersecting the viewport, for `channels`.

    Reads the MANIFEST, not the scene. Whether a surface is switched on is a
    display choice; the geometry exists either way, and the manifest already
    says which tiles cover which ground. Requiring the surface to be visible
    only meant you had to turn it on to find out what was there.
    """
    if mesh_mgr is None or roi_vox is None:
        return []
    try:
        chans = list(channels) if channels is not None else None
        return mesh_mgr.visible_tiles(tuple(roi_vox), channels=chans)
    except Exception:
        return []


def viewport_tile_keys(mesh_mgr, roi_vox, channels=None) -> set:
    """Distinct (tile_y, tile_x) positions the viewport touches.

    Position, not tile: the same footprint carries one tile per channel, and
    the gate is about how much GROUND is in view, not how many channels are on.
    """
    return {(t.tile_y, t.tile_x)
            for t in viewport_tiles(mesh_mgr, roi_vox, channels)}


def labels_available(mesh_mgr, roi_vox, max_tiles: int = MAX_LABEL_TILES,
                     channels=None):
    """(allowed, n_tiles) — is the view close enough in to label?"""
    n = len(viewport_tile_keys(mesh_mgr, roi_vox, channels))
    return (0 < n <= max_tiles), n


def welded_surface(mesh_mgr, channel_idx: int, roi_vox=None):
    """One welded vtkPolyData over a channel's tiles in the viewport.

    Geometry comes from the MANIFEST and is read on demand, so labelling works
    on a channel whose surface is switched off. Displaying a surface is a
    display choice; the tiles exist regardless, and measured on mis_v3 reading
    them costs ~67 ms for a 48-tile view — worker-thread work, not a reason to
    make the user turn surfaces on first.

    A loaded tile already in the scene is reused rather than re-read.

    Welding matters: `vtkAppendPolyData` alone leaves a nucleus straddling a
    seam as two connected components, so it earns two labels. Half a voxel
    joins them (INTEGRATION.md section 2.1).
    """
    from vtkmodules.vtkFiltersCore import vtkAppendPolyData
    from .flagpole import weld

    if mesh_mgr is None:
        return None
    ci = int(channel_idx)
    tiles = viewport_tiles(mesh_mgr, roi_vox, channels=[ci])
    if not tiles:
        return None

    live = getattr(mesh_mgr, "_actors", {})
    parts = []
    for tile in tiles:
        pd = None
        actor = live.get((ci, tile.tile_y, tile.tile_x))
        if actor is not None:
            mapper = actor.GetMapper()
            pd = mapper.GetInput() if mapper is not None else None
        if pd is None:
            pd = mesh_mgr.load_tile_polydata(tile)
        if pd is not None and pd.GetNumberOfPoints() > 0:
            parts.append(pd)
    if not parts:
        return None

    if len(parts) == 1:
        merged = parts[0]
    else:
        app = vtkAppendPolyData()
        for p in parts:
            app.AddInputData(p)
        app.Update()
        merged = app.GetOutput()

    tol = WELD_TOLERANCE_VOX * float(getattr(mesh_mgr, "base_sx", 0.14))
    return weld(merged, tol)


def dense_field(loader, channels: Sequence[str], roi_vox=None,
                cell_vox: int = SITE_CELL_VOX):
    """(dense (ny, nx) array, origin_xy, bin_size) for a marker set at r=0.

    The array holds ACTIVE BIN COUNTS, not fractions, and that is deliberate.
    `HeatmapField.fractions` is not in consistent units across marker counts:
    a single channel at r=0 takes the voxel-exact occupancy path and reports a
    VOXEL fraction, while a combination reports a BIN fraction. Comparing them
    put colocalization above occupancy in every cell, which breaks the
    `coloc <= min(occ_a, occ_b)` invariant the engine's interaction detection
    rests on. `counts` is active bins per cell on both paths.

    It also restores the vendored thresholds: the reference's heatmaps were
    per-bin counts too, so COLOC_MIN_VALUE ("a bin needs value >= 2") means
    what it was tuned to mean.

    Zero-count cells are absent from the sparse field, so the grid is built by
    scatter.

    Cropped to the viewport when `roi_vox` is given — the engine's region
    labelling is O(grid), and there is no reason to label ground the user
    cannot see.
    """
    if loader is None or not getattr(loader, "is_loaded", False) or not channels:
        return None, (0.0, 0.0), (1.0, 1.0)

    level = _level_for_cell(loader, cell_vox)
    field = loader.get_heatmap_field(list(channels), 0.0, level)
    if field is None or field.counts.size == 0:
        return None, (0.0, 0.0), (1.0, 1.0)

    dense = np.zeros((field.ny, field.nx), dtype=np.float32)
    dense[field.cells_yx[:, 0], field.cells_yx[:, 1]] = field.counts

    sx, sy = _spacing_xy(loader)
    bin_w = field.cell_size_vox * sx
    bin_h = field.cell_size_vox * sy

    if roi_vox is None:
        return dense, (0.0, 0.0), (bin_w, bin_h)

    x0, x1, y0, y1 = roi_vox
    cx0 = max(0, int(x0) // field.cell_size_vox)
    cx1 = min(field.nx, -(-int(x1) // field.cell_size_vox))
    cy0 = max(0, int(y0) // field.cell_size_vox)
    cy1 = min(field.ny, -(-int(y1) // field.cell_size_vox))
    if cx1 <= cx0 or cy1 <= cy0:
        return None, (0.0, 0.0), (bin_w, bin_h)

    # The crop moves the array's [0,0], so the world origin moves with it.
    return (dense[cy0:cy1, cx0:cx1],
            (cx0 * bin_w, cy0 * bin_h),
            (bin_w, bin_h))


def _level_for_cell(loader, cell_vox: int) -> int:
    """Hierarchy level whose cell edge is `cell_vox`, else the finest."""
    sizes = getattr(loader, "cell_sizes_vox", None) or {}
    for level, size in sizes.items():
        if int(size) == int(cell_vox):
            return int(level)
    return 0


def _spacing_xy(loader) -> Tuple[float, float]:
    """World size of one voxel in x and y."""
    vox = getattr(getattr(loader, "grid", None), "voxel_um", None)
    if vox is not None and len(vox) >= 3:
        return float(vox[2]), float(vox[1])
    return 0.14, 0.14
