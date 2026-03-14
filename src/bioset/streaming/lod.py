# camera.py
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ROI:
    x0: int
    x1: int
    y0: int
    y1: int


def camera_distance_to_focal(camera) -> float:
    px, py, pz = camera.GetPosition()
    fx, fy, fz = camera.GetFocalPoint()
    dx, dy, dz = (px - fx), (py - fy), (pz - fz)
    return math.sqrt(dx*dx + dy*dy + dz*dz)


def choose_heatmap_level(
    distance: float,
    rules,
    *,
    min_level: int = 0,
    max_level: int = 3,
) -> int:
    """
    Map camera distance to heatmap hierarchy level using config rules.
    Rules are (distance_threshold, level) pairs, sorted descending by threshold.
    """
    chosen = max_level
    for thresh, level in rules:
        if distance >= float(thresh):
            chosen = int(level)
            break
    return max(min_level, min(max_level, chosen))


def choose_component(distance: float, rules, *, min_component: int, max_component: int) -> int:
    chosen = max_component
    for thresh, comp in rules:
        if distance >= float(thresh):
            chosen = int(comp)
            break
    return max(min_component, min(max_component, chosen))


def scale_roi_to_component(
    roi_dict: dict,
    from_component: int,
    to_component: int,
    *,
    x_dim: int | None = None,
    y_dim: int | None = None,
) -> dict:
    """
    Convert ROI from one LOD component to another (voxel coordinates).
    Higher component number = fewer voxels (e.g. comp 6 is half the resolution of comp 5).
    Returns a dict with x0, x1, y0, y1 valid for to_component.
    """
    x0 = int(roi_dict.get("x0", 0))
    x1 = int(roi_dict.get("x1", 1))
    y0 = int(roi_dict.get("y0", 0))
    y1 = int(roi_dict.get("y1", 1))
    if from_component == to_component:
        return {"x0": x0, "x1": x1, "y0": y0, "y1": y1}
    # Higher component number = fewer voxels (e.g. comp 6 is 1/64 of comp 0 in each axis).
    if to_component > from_component:
        scale = 2 ** (to_component - from_component)
        x0_c = x0 // scale
        x1_c = max(x0_c + 1, (x1 + scale - 1) // scale)
        y0_c = y0 // scale
        y1_c = max(y0_c + 1, (y1 + scale - 1) // scale)
    else:
        scale = 2 ** (from_component - to_component)
        x0_c = x0 * scale
        x1_c = x1 * scale
        y0_c = y0 * scale
        y1_c = y1 * scale
    if x_dim is not None:
        x0_c = max(0, min(x0_c, x_dim - 1))
        x1_c = max(x0_c + 1, min(x_dim, x1_c))
    if y_dim is not None:
        y0_c = max(0, min(y0_c, y_dim - 1))
        y1_c = max(y0_c + 1, min(y_dim, y1_c))
    return {"x0": x0_c, "x1": x1_c, "y0": y0_c, "y1": y1_c}


def _display_to_world(renderer, x: float, y: float, z_norm: float):
    renderer.SetDisplayPoint(x, y, z_norm)
    renderer.DisplayToWorld()
    wx, wy, wz, w = renderer.GetWorldPoint()
    if abs(w) > 1e-9:
        return (wx / w, wy / w, wz / w)
    return (wx, wy, wz)


def compute_visible_xy_roi_vox(
    renderer,
    *,
    bounds_world: Tuple[float, float, float, float, float, float],
    sx: float,
    sy: float,
    x_dim: int,
    y_dim: int,
    margin_vox: int = 0,
    display_samples: int = 2,
) -> ROI:
    """
    Estimate visible XY region by casting rays from display points into the volume.
    - display_samples: n for an n×n grid (2 = 4 corners; 3 = 9 pts; 5 = 25 pts). More = more accurate.
    - For each (dx, dy): ray near->far, intersect with z=zmin and z=zmax, collect world x,y.
    - Visible ROI = min/max x,y of all intersections, converted to voxel indices.
    """
    xmin, xmax, ymin, ymax, zmin, zmax = bounds_world

    w, h = renderer.GetSize()
    n = max(1, min(display_samples, 32))
    if n == 2:
        display_pts = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    else:
        display_pts = []
        denom = max(1, n - 1)
        for iy in range(n):
            for ix in range(n):
                dx = (w - 1) * ix / denom
                dy = (h - 1) * iy / denom
                display_pts.append((dx, dy))

    pts = []
    for (dx, dy) in display_pts:
        near = _display_to_world(renderer, dx, dy, 0.0)
        far = _display_to_world(renderer, dx, dy, 1.0)

        nz = near[2]
        fz = far[2]
        if abs(fz - nz) < 1e-9:
            continue

        for z_plane in (zmin, zmax):
            t = (z_plane - nz) / (fz - nz)
            if t < -0.25 or t > 1.25:
                continue
            xw = near[0] + t * (far[0] - near[0])
            yw = near[1] + t * (far[1] - near[1])
            pts.append((xw, yw))

    if not pts:
        return ROI(0, x_dim, 0, y_dim)

    xw0 = max(xmin, min(p[0] for p in pts))
    xw1 = min(xmax, max(p[0] for p in pts))
    yw0 = max(ymin, min(p[1] for p in pts))
    yw1 = min(ymax, max(p[1] for p in pts))

    x0 = int(math.floor(xw0 / sx))
    x1 = int(math.ceil(xw1 / sx))
    y0 = int(math.floor(yw0 / sy))
    y1 = int(math.ceil(yw1 / sy))

    x0 -= margin_vox
    y0 -= margin_vox
    x1 += margin_vox
    y1 += margin_vox

    x0 = max(0, min(x_dim - 1, x0))
    y0 = max(0, min(y_dim - 1, y0))
    x1 = max(x0 + 1, min(x_dim, x1))
    y1 = max(y0 + 1, min(y_dim, y1))

    return ROI(x0, x1, y0, y1)
