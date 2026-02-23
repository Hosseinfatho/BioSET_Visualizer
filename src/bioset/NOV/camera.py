# camera.py
"""Compute camera position and view-up from sphere (theta, phi) and center/radius."""

from __future__ import annotations

import math
from typing import List, Tuple

def _deg2rad(d: float) -> float:
    return d * math.pi / 180.0


def camera_position_from_sphere(
    center: Tuple[float, float, float],
    radius: float,
    theta_deg: float,
    phi_deg: float,
) -> List[float]:
    """
    Camera position on sphere around center.
    Theta: 0 = +Z, 180 = -Z. Phi: azimuth in XY, 0 = +X, 90 = +Y.
    Returns [x, y, z] world position.
    """
    th = _deg2rad(theta_deg)
    ph = _deg2rad(phi_deg)
    # Unit direction from center toward camera (camera is on sphere)
    dx = radius * math.sin(th) * math.cos(ph)
    dy = radius * math.sin(th) * math.sin(ph)
    dz = radius * math.cos(th)
    return [
        center[0] + dx,
        center[1] + dy,
        center[2] + dz,
    ]


def view_up_for_sphere_point(theta_deg: float, phi_deg: float) -> List[float]:
    """
    Consistent view-up for a viewpoint on the sphere.
    Prefer world +Z as up where possible; otherwise use tangent that points "up".
    """
    th = _deg2rad(theta_deg)
    ph = _deg2rad(phi_deg)
    # Normal at camera (pointing toward center): n = -direction
    nx = -math.sin(th) * math.cos(ph)
    ny = -math.sin(th) * math.sin(ph)
    nz = -math.cos(th)
    # World up
    ux, uy, uz = 0.0, 0.0, 1.0
    # If view direction is nearly parallel to Z, use +Y as up
    if abs(nz) > 0.99:
        ux, uy, uz = 0.0, 1.0, 0.0
    # View up = up - (up·n)n, then normalize
    dot = ux * nx + uy * ny + uz * nz
    vx = ux - dot * nx
    vy = uy - dot * ny
    vz = uz - dot * nz
    norm = math.sqrt(vx * vx + vy * vy + vz * vz)
    if norm < 1e-9:
        return [0.0, 1.0, 0.0]
    return [vx / norm, vy / norm, vz / norm]
