"""
Hierarchical region clustering for zoom-dependent label LOD.

Adapted from cycif_mesh_labelling/new/hierarchy.py for BioSET:
- Overall label text is passed directly as a string (not looked up from labels.py)
- get_clusters() returns None at region-level zoom, list of LabelClusters otherwise
"""

from __future__ import annotations

import numpy as np
from collections import Counter
from scipy.cluster.hierarchy import linkage, fcluster

from .settings import label_config as config


class LabelCluster:
    """A group of regions that share one label at a given zoom level."""

    def __init__(self, regions, cluster_id):
        self.cluster_id = cluster_id
        self.regions = regions
        self.n_regions = len(regions)

        total_cells = sum(r.n_cells for r in regions)
        if total_cells > 0:
            self.centroid = sum(r.centroid * r.n_cells for r in regions) / total_cells
        else:
            self.centroid = np.mean([r.centroid for r in regions], axis=0)

        avg_n = np.mean([r.normal for r in regions], axis=0)
        nl = np.linalg.norm(avg_n)
        self.normal = avg_n / nl if nl > 1e-9 else np.array([0.0, 0.0, 1.0])

        all_bounds = [r.bounds for r in regions]
        self.bounds = (
            min(b[0] for b in all_bounds), max(b[1] for b in all_bounds),
            min(b[2] for b in all_bounds), max(b[3] for b in all_bounds),
            min(b[4] for b in all_bounds), max(b[5] for b in all_bounds),
        )

        label_counts = Counter(
            r.label_text for r in regions if r.label_text is not None
        )
        self.label_text = label_counts.most_common(1)[0][0] if label_counts else None

        diag = np.array([
            self.bounds[1] - self.bounds[0],
            self.bounds[3] - self.bounds[2],
            self.bounds[5] - self.bounds[4],
        ])
        self.extent = np.linalg.norm(diag)


class RegionHierarchy:
    """Precomputed Ward-linkage dendrogram over region centroids.

    Call get_clusters(camera_distance) per frame.
    Returns None at region-level zoom (caller uses individual regions).
    Returns list[LabelCluster] at cluster or overview zoom.
    """

    def __init__(self, single_regions, composite_regions, overall_label_text=None):
        self.all_regions = single_regions + composite_regions
        self.overall_label_text = overall_label_text  # text string, not a lookup key
        self.n_regions = len(self.all_regions)

        if self.n_regions < 2:
            self.linkage = None
            return

        centroids = np.array([r.centroid for r in self.all_regions])
        print(f"[label_hierarchy] Building hierarchy over {self.n_regions} regions...")
        self.linkage = linkage(centroids, method="ward")
        self.max_distance = self.linkage[-1, 2] if len(self.linkage) > 0 else 1.0
        print(f"[label_hierarchy] Max merge distance: {self.max_distance:.2f}")

    def get_clusters(self, camera_distance):
        """Return LabelClusters for the current zoom, or None for region-level zoom.

        None → caller should use place_all_labels() with individual regions.
        """
        overview_dist = config.get("HIERARCHY_OVERVIEW_DIST", 250.0)
        region_dist = config.get("HIERARCHY_REGION_DIST", 60.0)

        if self.n_regions < 2 or self.linkage is None:
            return [
                LabelCluster([r], i)
                for i, r in enumerate(self.all_regions)
                if r.label_text is not None
            ]

        # Overview: single cluster for everything
        if camera_distance >= overview_dist:
            cluster = LabelCluster(self.all_regions, 0)
            cluster.label_text = self.overall_label_text or cluster.label_text
            return [cluster]

        # Region level: individual labels
        if camera_distance <= region_dist:
            return None

        # Cluster level: interpolate cut height
        t = (camera_distance - region_dist) / (overview_dist - region_dist)
        t = np.clip(t, 0.0, 1.0)
        t = t ** 1.5  # nonlinear: merge more aggressively at far distances

        min_cut = self.max_distance * 0.05
        max_cut = self.max_distance * 0.95
        cut_height = min_cut + t * (max_cut - min_cut)

        labels = fcluster(self.linkage, t=cut_height, criterion="distance")

        cluster_map = {}
        for i, cl in enumerate(labels):
            cluster_map.setdefault(int(cl), []).append(self.all_regions[i])

        clusters = []
        for cid, regions in cluster_map.items():
            lc = LabelCluster(regions, cid)
            if lc.label_text is not None:
                clusters.append(lc)

        return clusters
