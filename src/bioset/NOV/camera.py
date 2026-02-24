# camera.py
"""Compute camera position and view-up from sphere (theta, phi) and center/radius.

Right-handed coords (OpenGL-style): X right, Y up, Z into screen (depth).
- phi = 0 on Z axis, rotation around Y: phi=0 = -Z (out of screen), phi=180 = +Z (into screen).
- theta: 0 = +Y (top), 90 = equator (XZ), 180 = -Y (bottom).
Camera position = center + R * (sin(theta)*sin(phi), cos(theta), -sin(theta)*cos(phi)).
"""

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
    Right-handed: X right, Y up, Z into screen. phi=0 = -Z (viewer side), theta=90 = equator.
    Returns [x, y, z] world position.
    """
    th = _deg2rad(theta_deg)
    ph = _deg2rad(phi_deg)
    # Unit direction from center toward camera (phi=0 -> -Z, rotate around Y)
    dx = radius * math.sin(th) * math.sin(ph)
    dy = radius * math.cos(th)
    dz = -radius * math.sin(th) * math.cos(ph)
    return [
        center[0] + dx,
        center[1] + dy,
        center[2] + dz,
    ]


def view_up_for_sphere_point(theta_deg: float, phi_deg: float) -> List[float]:
    """
    View-up so image is upright. World +Y is up; if view is along Y, use +Z.
    """
    th = _deg2rad(theta_deg)
    ph = _deg2rad(phi_deg)
    # Direction from camera toward center (normal)
    nx = math.sin(th) * math.sin(ph)
    ny = math.cos(th)
    nz = -math.sin(th) * math.cos(ph)
    ux, uy, uz = 0.0, 1.0, 0.0
    if abs(ny) > 0.99:
        ux, uy, uz = 0.0, 0.0, 1.0
    dot = ux * nx + uy * ny + uz * nz
    vx = ux - dot * nx
    vy = uy - dot * ny
    vz = uz - dot * nz
    norm = math.sqrt(vx * vx + vy * vy + vz * vz)
    if norm < 1e-9:
        return [0.0, 0.0, 1.0]
    return [vx / norm, vy / norm, vz / norm]
