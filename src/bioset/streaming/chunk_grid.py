"""Chunk (tile) addressing for one multiscale component.

A "chunk" here is one zarr chunk's XY footprint spanning the full z-column
(the Globus store is chunked ``(1, 1, Z, ty, tx)`` so z is a single chunk and
xy is tiled). Tiles are addressed by ``(cyi, cxi)``; a viewport loads every
tile its snapped ROI covers.

This module is pure (no VTK, no I/O) and is unit-testable on its own. It reuses
the frozen :class:`~bioset.streaming.lod.ROI` so tile ROIs interoperate with the
rest of the streamer.
"""
from __future__ import annotations

import math
from typing import List, Tuple

from .lod import ROI


class ChunkGrid:
    """Tile addressing for one component, derived from the raw zarr array.

    ``zarr_array`` must expose ``.shape`` (t, c, z, y, x) and ``.chunks``
    (1, 1, cz, ty, tx). Only the xy tiling is used for addressing; the ``cz``
    axis is retained so a z-chunked store (e.g. the S3 layout) can be supported
    later without changing the key shape.
    """

    def __init__(self, zarr_array):
        # Back-compat path: assumes a 5D (t,c,z,y,x) array. Prefer `from_dims`
        # (fed by ZarrMultiscaleSource.canonical_shape/chunks), which works for
        # 3D/4D/5D stores with any axis order.
        _, _, z, y, x = zarr_array.shape
        _, _, cz, ty, tx = zarr_array.chunks
        self._set_dims(z, y, x, cz, ty, tx)

    @classmethod
    def from_dims(cls, z, y, x, cz, ty, tx) -> "ChunkGrid":
        """Build a grid from canonical (z,y,x) sizes and (cz,ty,tx) chunk sizes,
        independent of how many/which axes the underlying array has."""
        self = cls.__new__(cls)
        self._set_dims(z, y, x, cz, ty, tx)
        return self

    def _set_dims(self, z, y, x, cz, ty, tx) -> None:
        self.Z, self.Y, self.X = int(z), int(y), int(x)
        self.cz, self.ty, self.tx = int(cz), int(ty), int(tx)
        self.n_cz = math.ceil(self.Z / self.cz)   # 1 for full-z columns
        self.n_cy = math.ceil(self.Y / self.ty)
        self.n_cx = math.ceil(self.X / self.tx)

    def snap_roi(self, roi: ROI) -> ROI:
        """Expand an ROI outward to tile boundaries (clamped to the volume)."""
        x0 = (roi.x0 // self.tx) * self.tx
        x1 = min(self.X, math.ceil(roi.x1 / self.tx) * self.tx)
        y0 = (roi.y0 // self.ty) * self.ty
        y1 = min(self.Y, math.ceil(roi.y1 / self.ty) * self.ty)
        # Guard against an empty/degenerate ROI producing x1<=x0.
        x1 = max(x1, min(self.X, x0 + self.tx))
        y1 = max(y1, min(self.Y, y0 + self.ty))
        return ROI(x0, x1, y0, y1)

    def covering_tiles(self, roi: ROI) -> List[Tuple[int, int]]:
        """``(cyi, cxi)`` tiles covering ``roi``, ordered CENTRE-OUT.

        Centre-out is the fetch/replacement priority: the tile nearest the
        viewport centre is loaded (and sharpened) first.
        """
        cy0, cy1 = roi.y0 // self.ty, (roi.y1 - 1) // self.ty
        cx0, cx1 = roi.x0 // self.tx, (roi.x1 - 1) // self.tx
        # Viewport centre in tile-index space.
        ccy = 0.5 * (roi.y0 + roi.y1) / self.ty - 0.5
        ccx = 0.5 * (roi.x0 + roi.x1) / self.tx - 0.5
        tiles = [(cyi, cxi)
                 for cyi in range(cy0, cy1 + 1)
                 for cxi in range(cx0, cx1 + 1)]
        tiles.sort(key=lambda t: (t[0] - ccy) ** 2 + (t[1] - ccx) ** 2)
        return tiles

    def tile_bounds(self, cyi: int, cxi: int) -> ROI:
        """Voxel ROI of tile ``(cyi, cxi)`` (clamped to the volume edge)."""
        y0, x0 = cyi * self.ty, cxi * self.tx
        return ROI(x0, min(self.X, x0 + self.tx), y0, min(self.Y, y0 + self.ty))
