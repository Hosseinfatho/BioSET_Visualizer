"""
Interaction detection (proximity-based, not overlap).

Adapted from cycif_mesh_labelling/new/interactions.py for BioSET.
label_lookup_fn passed at build time instead of imported from labels.py.
"""

from __future__ import annotations

import numpy as np
from itertools import combinations

from .settings import label_config as config


class Interaction:
    """A proximity-based relationship between two regions."""

    _next_id = 0

    def __init__(self, midpoint, gap, axis, normal, key, label_text,
                 closest_a, closest_b):
        self.id = Interaction._next_id
        Interaction._next_id += 1

        self.midpoint = np.asarray(midpoint, dtype=np.float64)
        self.gap = float(gap)
        self.axis = np.asarray(axis, dtype=np.float64)
        self.normal = np.asarray(normal, dtype=np.float64)
        self.key = key
        self.label_text = label_text
        self.closest_a = np.asarray(closest_a, dtype=np.float64)
        self.closest_b = np.asarray(closest_b, dtype=np.float64)

    def __repr__(self):
        return f"Interaction({self.id}, '{self.key}', gap={self.gap:.2f})"


def compute_interaction_geometry(single_regions, composite_regions):
    """Find proximity pairs between regions of different identity.

    Returns list of plain dicts (identity_a, identity_b, key, midpoint,
    gap, axis, normal, closest_a, closest_b).
    """
    threshold = config.get("INTERACTION_DISTANCE", 5.0)
    all_regions = single_regions + composite_regions

    groups = {}
    for r in all_regions:
        identity = "+".join(sorted(r.channels)) if r.is_composite else r.channel
        groups.setdefault(identity, []).append(r)

    print(f"[label_interactions] {len(groups)} identity groups, threshold={threshold}")

    results = []
    for (id_a, regions_a), (id_b, regions_b) in combinations(groups.items(), 2):
        key = _norm_interaction_key(id_a, id_b)

        for ra in regions_a:
            inflated_a = _inflate_aabb(ra.bounds, threshold)
            for rb in regions_b:
                if not _aabb_overlap(inflated_a, rb.bounds):
                    continue
                if _aabb_overlap(ra.bounds, rb.bounds):
                    dist, _, _ = _aabb_distance(ra.bounds, rb.bounds)
                    if dist < 1e-6:
                        continue

                dist, pt_a, pt_b = _aabb_distance(ra.bounds, rb.bounds)
                if dist > threshold:
                    continue

                midpoint = (pt_a + pt_b) / 2.0
                axis = pt_b - pt_a
                al = np.linalg.norm(axis)
                axis = axis / al if al > 1e-9 else np.array([0.0, 0.0, 1.0])
                avg_n = (ra.normal + rb.normal) / 2.0
                nl = np.linalg.norm(avg_n)
                normal = avg_n / nl if nl > 1e-9 else np.array([0.0, 0.0, 1.0])

                results.append({
                    "identity_a": id_a, "identity_b": id_b, "key": key,
                    "midpoint": midpoint, "gap": dist,
                    "axis": axis, "normal": normal,
                    "closest_a": pt_a, "closest_b": pt_b,
                })

    print(f"[label_interactions] {len(results)} proximity pairs found")
    return results


def build_interactions(geometry_data, label_lookup_fn):
    """Build Interaction objects, filtering to those with a label."""
    Interaction._next_id = 0
    interactions = []
    for g in geometry_data:
        label_text = label_lookup_fn(g["key"])
        if label_text is None:
            continue
        ix = Interaction(
            midpoint=g["midpoint"], gap=g["gap"],
            axis=g["axis"], normal=g["normal"],
            key=g["key"], label_text=label_text,
            closest_a=g["closest_a"], closest_b=g["closest_b"],
        )
        interactions.append(ix)
    print(f"[label_interactions] {len(interactions)} interactions with labels")
    return interactions


def detect_interactions(single_regions, composite_regions, label_lookup_fn):
    geo = compute_interaction_geometry(single_regions, composite_regions)
    return build_interactions(geo, label_lookup_fn)


# ==========================================================================
# Helpers
# ==========================================================================

def _norm_coloc(key):
    return "+".join(sorted(key.split("+")))


def _norm_interaction_key(id_a, id_b):
    na, nb = _norm_coloc(id_a), _norm_coloc(id_b)
    return "/".join(sorted([na, nb]))


def _aabb_distance(b1, b2):
    closest_1 = np.zeros(3)
    closest_2 = np.zeros(3)
    sq_dist = 0.0
    for i in range(3):
        min1, max1 = b1[i*2], b1[i*2+1]
        min2, max2 = b2[i*2], b2[i*2+1]
        if max1 < min2:
            closest_1[i], closest_2[i] = max1, min2
            sq_dist += (min2 - max1) ** 2
        elif max2 < min1:
            closest_1[i], closest_2[i] = min1, max2
            sq_dist += (min1 - max2) ** 2
        else:
            lo, hi = max(min1, min2), min(max1, max2)
            mid = (lo + hi) / 2.0
            closest_1[i] = closest_2[i] = mid
    return np.sqrt(sq_dist), closest_1, closest_2


def _aabb_overlap(b1, b2):
    return (b1[0] <= b2[1] and b1[1] >= b2[0] and
            b1[2] <= b2[3] and b1[3] >= b2[2] and
            b1[4] <= b2[5] and b1[5] >= b2[4])


def _inflate_aabb(b, amount):
    return (b[0]-amount, b[1]+amount,
            b[2]-amount, b[3]+amount,
            b[4]-amount, b[5]+amount)
