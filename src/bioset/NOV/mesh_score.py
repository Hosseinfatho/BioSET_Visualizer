# mesh_score.py
"""NOV scoring via tangent-plane mesh projection.
A tangent plane (square, side = 2*radius) is placed at the focal point; channels are
projected onto an NxN mesh. Score = 0.8 * (filled cells / total) - 0.2 * (occluded cells / total).
All parameters are easy to change at the top of the file.
"""

from __future__ import annotations

import math
from typing import List, Tuple

# ---------- Easy-to-change parameters ----------
# Mesh resolution (N x N). Plane is a square of side 2 * radius.
NOV_MESH_SIZE = 1000
# Weights: visibility (filled cells) positive, occlusion (cells with 2+ channels) negative.
VISIBILITY_WEIGHT = 0.8
OCCLUSION_WEIGHT = 0.2
# ---------- ----------------------------------


def _normalize(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    x, y, z = v
    n = math.sqrt(x * x + y * y + z * z)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (x / n, y / n, z / n)


def _cross(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _convex_hull_2d(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Return convex hull of 2D points (counterclockwise)."""
    if len(points) <= 2:
        return list(points)
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross_o(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross_o(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross_o(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _point_in_polygon_2d(q: Tuple[float, float], poly: List[Tuple[float, float]]) -> bool:
    """True if q is inside or on the boundary of poly (counterclockwise)."""
    n = len(poly)
    if n < 3:
        return False
    x, y = q
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-20) + xi):
            inside = not inside
        j = i
    return inside


def _aabb_corners(bounds: Tuple[float, float, float, float, float, float]) -> List[Tuple[float, float, float]]:
    """bounds = (xmin, xmax, ymin, ymax, zmin, zmax). Return 8 corners."""
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    return [
        (xmin, ymin, zmin), (xmax, ymin, zmin), (xmin, ymax, zmin), (xmax, ymax, zmin),
        (xmin, ymin, zmax), (xmax, ymin, zmax), (xmin, ymax, zmax), (xmax, ymax, zmax),
    ]


def compute_view_score_mesh(
    camera_pos: Tuple[float, float, float],
    focal: Tuple[float, float, float],
    view_up: Tuple[float, float, float],
    radius: float,
    bounds_world: Tuple[float, float, float, float, float, float],
    num_channels: int,
    *,
    mesh_size: int = NOV_MESH_SIZE,
    visibility_weight: float = VISIBILITY_WEIGHT,
    occlusion_weight: float = OCCLUSION_WEIGHT,
) -> float:
    """
    Score for one viewpoint using tangent-plane mesh projection.
    - Tangent plane at focal, perpendicular to (camera -> focal), square side = 2*radius.
    - Mesh is mesh_size x mesh_size.
    - Project volume AABB (same for all channels) onto the plane; for each channel, mark cells
      inside the projected polygon. filled = cells with >= 1 channel, occluded = cells with >= 2.
    - Score = visibility_weight * (filled/total) - occlusion_weight * (occluded/total).
    """
    if num_channels <= 0:
        return 0.0
    # Plane: origin = focal, normal = from camera to focal (view direction reversed)
    dx = focal[0] - camera_pos[0]
    dy = focal[1] - camera_pos[1]
    dz = focal[2] - camera_pos[2]
    normal = _normalize((dx, dy, dz))
    up = _normalize((view_up[0], view_up[1], view_up[2]))
    u_axis = _normalize(_cross(normal, up))
    v_axis = _normalize(_cross(normal, u_axis))
    # Project 8 corners of AABB to (u, v) in [-radius, radius]
    corners = _aabb_corners(bounds_world)
    uv_points = []
    for c in corners:
        vec = (c[0] - focal[0], c[1] - focal[1], c[2] - focal[2])
        u = _dot(vec, u_axis)
        v = _dot(vec, v_axis)
        uv_points.append((u, v))
    hull = _convex_hull_2d(uv_points)
    if len(hull) < 3:
        return 0.0
    # Mesh: [0, mesh_size) x [0, mesh_size), covering [-radius, radius] x [-radius, radius]
    total_cells = mesh_size * mesh_size
    cell_size = (2.0 * radius) / mesh_size
    half_cell = cell_size * 0.5
    # (u, v) -> cell (i, j): u in [-R, R] -> i = (u + R) / (2*R) * N
    count = [[0] * mesh_size for _ in range(mesh_size)]
    umin = min(p[0] for p in hull)
    umax = max(p[0] for p in hull)
    vmin = min(p[1] for p in hull)
    vmax = max(p[1] for p in hull)
    i0 = max(0, int((umin + radius) / (2.0 * radius) * mesh_size))
    i1 = min(mesh_size, int((umax + radius) / (2.0 * radius) * mesh_size) + 1)
    j0 = max(0, int((vmin + radius) / (2.0 * radius) * mesh_size))
    j1 = min(mesh_size, int((vmax + radius) / (2.0 * radius) * mesh_size) + 1)
    for _ in range(num_channels):
        for i in range(i0, i1):
            for j in range(j0, j1):
                u_center = -radius + (i + 0.5) * cell_size
                v_center = -radius + (j + 0.5) * cell_size
                if _point_in_polygon_2d((u_center, v_center), hull):
                    count[i][j] += 1
    filled = sum(1 for row in count for c in row if c >= 1)
    occluded = sum(1 for row in count for c in row if c >= 2)
    if total_cells <= 0:
        return 0.0
    filled_ratio = filled / total_cells
    occluded_ratio = occluded / total_cells
    score = visibility_weight * filled_ratio - occlusion_weight * occluded_ratio
    return max(0.0, min(1.0, score))


def normalize_scores(scores: list[float]) -> list[float]:
    """Normalize list of scores to [0, 1]. If all same or max 0, return [1,1,...] or zeros."""
    if not scores:
        return []
    mx = max(scores)
    if mx <= 0:
        return [0.0] * len(scores)
    return [s / mx for s in scores]
