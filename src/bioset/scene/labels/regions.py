"""
Region extraction and cross-channel co-localization detection.

Adapted from cycif_mesh_labelling/new/regions.py for BioSET:
- No GLB loading (meshes are provided by MeshManager as already-loaded vtkPolyData)
- extract_channel_geometry() accepts an existing vtkPolyData
- Everything else is identical to the reference implementation
"""

from __future__ import annotations

import numpy as np
import vtk
from itertools import combinations
from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk
from vtkmodules.vtkFiltersCore import vtkConnectivityFilter, vtkPolyDataNormals

from .settings import label_config as config


# ==========================================================================
# Data model
# ==========================================================================

class Region:
    """One labelable spatial unit."""

    _next_id = 0

    def __init__(self, channel, label_text, centroid, bounds, normal,
                 n_cells, channels=None, principal_axis=None):
        self.id = Region._next_id
        Region._next_id += 1

        self.channel = channel
        self.channels = channels or [channel]
        self.label_text = label_text
        self.centroid = np.asarray(centroid, dtype=np.float64)
        self.bounds = bounds
        self.normal = np.asarray(normal, dtype=np.float64)
        self.n_cells = n_cells

        if principal_axis is not None:
            self.principal_axis = np.asarray(principal_axis, dtype=np.float64)
        else:
            self.principal_axis = None

        self.is_composite = len(self.channels) > 1
        self.has_coloc = False

    def __repr__(self):
        kind = "COMPOSITE" if self.is_composite else "SINGLE"
        return f"Region({self.id}, {kind}, '{self.label_text}', cells={self.n_cells})"


# ==========================================================================
# Channel geometry extraction
# ==========================================================================

def extract_channel_geometry(mesh):
    """Extract connected components, PCA-split large regions, compute normals.

    Accepts an already-loaded vtkPolyData (world-space, from MeshManager).
    Returns (mesh, region_ids, region_info, cell_normals).
    region_info values: {centroid, n_cells, bounds, cell_indices}.
    """
    # CC extraction
    conn = vtkConnectivityFilter()
    conn.SetInputData(mesh)
    conn.SetExtractionModeToAllRegions()
    conn.ColorRegionsOn()
    conn.Update()

    labeled = conn.GetOutput()
    rids = vtk_to_numpy(labeled.GetCellData().GetArray("RegionId")).copy()
    mesh.ShallowCopy(labeled)
    print(f"[label_regions] {conn.GetNumberOfExtractedRegions()} connected components")

    # PCA split
    rids, region_info = _split_regions(mesh, rids)

    # Cell normals
    nf = vtkPolyDataNormals()
    nf.SetInputData(mesh)
    nf.ComputeCellNormalsOn()
    nf.ComputePointNormalsOff()
    nf.ConsistencyOn()
    nf.AutoOrientNormalsOn()
    nf.SplittingOff()
    nf.Update()
    cell_normals = vtk_to_numpy(nf.GetOutput().GetCellData().GetNormals()).copy()

    return mesh, rids, region_info, cell_normals


# ==========================================================================
# Overlap geometry computation (co-localization)
# ==========================================================================

def compute_overlap_geometry(ch_a, ch_b):
    """Compute raw overlap geometry between two channels.

    Returns list of dicts with: marker_a, marker_b, rid_a, rid_b,
    centroid, bounds, normal, n_enclosed.
    """
    min_enc = config.get("COLOC_MIN_ENCLOSED", 5)
    ma, mb = ch_a["marker_name"], ch_b["marker_name"]
    ri_a, ri_b = ch_a["region_info"], ch_b["region_info"]

    # Broad phase: AABB overlap test
    pairs = []
    for ra, ia in ri_a.items():
        for rb, ib in ri_b.items():
            if _aabb_overlap(ia["bounds"], ib["bounds"]):
                pairs.append((ra, rb))

    if not pairs:
        return []

    print(f"[label_regions] {ma}+{mb}: {len(pairs)} AABB overlaps, testing enclosed points...")

    results = []
    for ra, rb in pairs:
        sub_a = _extract_region_mesh(ch_a["mesh"], ch_a["region_ids"], ra)
        sub_b = _extract_region_mesh(ch_b["mesh"], ch_b["region_ids"], rb)

        enc_ba = _enclosed_points(sub_a, sub_b)
        enc_ab = _enclosed_points(sub_b, sub_a)
        all_enc = [p for p in [enc_ba, enc_ab] if len(p) > 0]
        if not all_enc:
            continue
        all_pts = np.vstack(all_enc)
        if len(all_pts) < min_enc:
            continue

        centroid = all_pts.mean(axis=0)
        normal = _estimate_normal(ch_a["mesh"], ch_a["region_ids"], ra,
                                   centroid, ch_a["cell_normals"])
        mins, maxs = all_pts.min(axis=0), all_pts.max(axis=0)

        results.append({
            "marker_a": ma, "marker_b": mb,
            "rid_a": ra, "rid_b": rb,
            "centroid": centroid,
            "bounds": (mins[0], maxs[0], mins[1], maxs[1], mins[2], maxs[2]),
            "normal": normal,
            "n_enclosed": len(all_pts),
        })

    print(f"[label_regions] {ma}+{mb}: {len(results)} confirmed overlaps")
    return results


# ==========================================================================
# Region construction from geometry + labels
# ==========================================================================

def build_regions(channels, overlap_data, label_lookup_fn):
    """Build Region objects from channel geometry + overlap data + live labels.

    Parameters
    ----------
    channels : list of channel dicts (marker_name, mesh, region_ids, region_info, cell_normals)
    overlap_data : dict { (marker_a, marker_b): [overlap_dicts] }
    label_lookup_fn : callable(key) -> str or None

    Returns
    -------
    single_regions, composite_regions
    """
    Region._next_id = 0

    single_regions = []
    region_map = {}

    for ch in channels:
        marker = ch["marker_name"]
        normals = ch["cell_normals"]
        rids = ch["region_ids"]

        for rid, info in ch["region_info"].items():
            normal = _estimate_normal(ch["mesh"], rids, rid, info["centroid"], normals)
            label_text = label_lookup_fn(marker)

            r = Region(
                channel=marker,
                label_text=label_text,
                centroid=info["centroid"],
                bounds=info["bounds"],
                normal=normal,
                n_cells=info["n_cells"],
                principal_axis=info.get("principal_axis"),
            )
            single_regions.append(r)
            region_map[(marker, rid)] = r

    print(f"[label_regions] {len(single_regions)} single-channel regions")

    composite_regions = []

    for (ma, mb), overlaps in overlap_data.items():
        coloc_key = "+".join(sorted([ma, mb]))
        coloc_text = label_lookup_fn(coloc_key)

        if coloc_text is None:
            continue

        for ov in overlaps:
            cr = Region(
                channel=coloc_key,
                label_text=coloc_text,
                centroid=ov["centroid"],
                bounds=ov["bounds"],
                normal=ov["normal"],
                n_cells=ov["n_enclosed"],
                channels=[ma, mb],
            )
            composite_regions.append(cr)

            if (ma, ov["rid_a"]) in region_map:
                region_map[(ma, ov["rid_a"])].has_coloc = True
            if (mb, ov["rid_b"]) in region_map:
                region_map[(mb, ov["rid_b"])].has_coloc = True

    print(f"[label_regions] {len(composite_regions)} composite regions")
    return single_regions, composite_regions


def detect_overlaps(channels, label_lookup_fn):
    """Detect overlaps and build regions in one call."""
    overlap_data = {}
    for ch_a, ch_b in combinations(channels, 2):
        ma, mb = ch_a["marker_name"], ch_b["marker_name"]
        overlaps = compute_overlap_geometry(ch_a, ch_b)
        if overlaps:
            overlap_data[(ma, mb)] = overlaps
    return build_regions(channels, overlap_data, label_lookup_fn)


# ==========================================================================
# Internal helpers
# ==========================================================================

def _split_regions(mesh, rids):
    min_cells = config["MIN_CELLS"]
    max_per = config["MAX_CELLS_PER_REGION"]
    split_min = config["SPLIT_MIN_CELLS"]
    split_depth = config["SPLIT_MAX_DEPTH"]

    points = vtk_to_numpy(mesh.GetPoints().GetData())
    new_rids = rids.copy()
    next_id = int(rids.max()) + 1
    region_info = {}

    for orig_id in np.unique(rids):
        cell_indices = np.where(rids == orig_id)[0]
        n_cells = len(cell_indices)
        if n_cells < min_cells:
            continue
        centroids = _cell_centroids(mesh, cell_indices)

        if n_cells <= max_per:
            pt_ids = _point_ids_for_cells(mesh, cell_indices)
            pts = points[list(pt_ids)]
            region_info[int(orig_id)] = {
                "centroid": np.mean(centroids, axis=0),
                "n_cells": n_cells,
                "bounds": _bounds(pts),
                "cell_indices": cell_indices,
                "principal_axis": _principal_axis(centroids),
            }
            continue

        subs = _pca_split(cell_indices, centroids, split_min, split_depth)
        for i, sub_cells in enumerate(subs):
            if len(sub_cells) < min_cells:
                continue
            rid = int(orig_id) if i == 0 else next_id
            if i != 0:
                next_id += 1
            new_rids[sub_cells] = rid
            local_idx = [np.where(cell_indices == c)[0][0] for c in sub_cells]
            sub_centroids = centroids[local_idx]
            pt_ids = _point_ids_for_cells(mesh, sub_cells)
            pts = points[list(pt_ids)]
            region_info[rid] = {
                "centroid": np.mean(sub_centroids, axis=0),
                "n_cells": len(sub_cells),
                "bounds": _bounds(pts),
                "cell_indices": sub_cells,
                "principal_axis": _principal_axis(sub_centroids),
            }

    arr = numpy_to_vtk(new_rids, deep=True)
    arr.SetName("RegionId")
    mesh.GetCellData().RemoveArray("RegionId")
    mesh.GetCellData().AddArray(arr)
    print(f"[label_regions] {len(region_info)} regions after split")
    return new_rids, region_info


def _cell_centroids(mesh, cell_indices):
    points = vtk_to_numpy(mesh.GetPoints().GetData())
    centroids = []
    for ci in cell_indices:
        cell = mesh.GetCell(int(ci))
        pts = [points[cell.GetPointId(i)] for i in range(cell.GetNumberOfPoints())]
        centroids.append(np.mean(pts, axis=0))
    return np.array(centroids)


def _principal_axis(centroids):
    """Compute the first principal axis (direction of maximum variance) of a point set."""
    if len(centroids) < 3:
        return np.array([1.0, 0.0, 0.0])
    centered = centroids - np.mean(centroids, axis=0)
    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        axis = vh[0]
        n = np.linalg.norm(axis)
        return axis / n if n > 1e-9 else np.array([1.0, 0.0, 0.0])
    except Exception:
        return np.array([1.0, 0.0, 0.0])


def _pca_split(cell_indices, centroids, min_cells, max_depth, depth=0):
    if len(cell_indices) <= min_cells or depth >= max_depth:
        return [cell_indices]
    mean = np.mean(centroids, axis=0)
    centered = centroids - mean
    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
    except Exception:
        return [cell_indices]
    proj = centered @ vh[0]
    med = np.median(proj)
    result = []
    for mask in (proj <= med, proj > med):
        sub = cell_indices[mask]
        if len(sub) > 0:
            result.extend(_pca_split(sub, centroids[mask], min_cells, max_depth, depth + 1))
    return result


def _point_ids_for_cells(mesh, cell_indices):
    pt_ids = set()
    for ci in cell_indices:
        cell = mesh.GetCell(int(ci))
        for i in range(cell.GetNumberOfPoints()):
            pt_ids.add(cell.GetPointId(i))
    return pt_ids


def _bounds(pts):
    mins, maxs = pts.min(axis=0), pts.max(axis=0)
    return (mins[0], maxs[0], mins[1], maxs[1], mins[2], maxs[2])


def _aabb_overlap(b1, b2):
    return (b1[0] <= b2[1] and b1[1] >= b2[0] and
            b1[2] <= b2[3] and b1[3] >= b2[2] and
            b1[4] <= b2[5] and b1[5] >= b2[4])


def _extract_region_mesh(mesh, rids, rid):
    cell_indices = np.where(rids == rid)[0]
    ids = vtk.vtkIdTypeArray()
    ids.SetNumberOfValues(len(cell_indices))
    for i, ci in enumerate(cell_indices):
        ids.SetValue(i, int(ci))
    sel_node = vtk.vtkSelectionNode()
    sel_node.SetFieldType(vtk.vtkSelectionNode.CELL)
    sel_node.SetContentType(vtk.vtkSelectionNode.INDICES)
    sel_node.SetSelectionList(ids)
    sel = vtk.vtkSelection()
    sel.AddNode(sel_node)
    ext = vtk.vtkExtractSelection()
    ext.SetInputData(0, mesh)
    ext.SetInputData(1, sel)
    ext.Update()
    geo = vtk.vtkGeometryFilter()
    geo.SetInputConnection(ext.GetOutputPort())
    geo.Update()
    return geo.GetOutput()


def _enclosed_points(surface, test):
    if surface.GetNumberOfCells() == 0 or test.GetNumberOfPoints() == 0:
        return np.zeros((0, 3))
    enc = vtk.vtkSelectEnclosedPoints()
    enc.SetInputData(test)
    enc.SetSurfaceData(surface)
    enc.SetTolerance(0.0001)
    enc.Update()
    arr = enc.GetOutput().GetPointData().GetArray("SelectedPoints")
    if arr is None:
        return np.zeros((0, 3))
    inside = vtk_to_numpy(arr)
    pts = vtk_to_numpy(enc.GetOutput().GetPoints().GetData())
    mask = inside > 0
    return pts[mask] if mask.sum() > 0 else np.zeros((0, 3))


def _estimate_normal(mesh, rids, rid, query, normals_arr):
    cell_indices = np.where(rids == rid)[0]
    points = vtk_to_numpy(mesh.GetPoints().GetData())
    best_d, best_n = float("inf"), np.array([0.0, 0.0, 1.0])
    for ci in cell_indices:
        cell = mesh.GetCell(int(ci))
        pts = [points[cell.GetPointId(j)] for j in range(cell.GetNumberOfPoints())]
        d = np.linalg.norm(np.mean(pts, axis=0) - query)
        if d < best_d:
            best_d = d
            best_n = normals_arr[ci].copy()
    n = np.linalg.norm(best_n)
    return best_n / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
