# sphere_points.py
"""Five front viewpoints only (theta_deg, phi_deg). Right-handed: X right, Y up, Z into screen.
phi=0 = -Z (viewer), theta=0 = +Y, theta=90 = equator, theta=180 = -Y."""

from __future__ import annotations

from typing import List, Tuple

NOV_THETA_PHI: List[Tuple[float, float]] = [
    (90.0, 0.0),     # front center (on -Z axis)
    (45.0, 45.0),    # top right to center
    (135.0, 45.0),   # bottom right to center
    (45.0, -45.0),   # top left to center
    (135.0, -45.0),  # bottom left to center
]


def get_nov_sphere_points() -> List[Tuple[float, float]]:
    """Return list of (theta_deg, phi_deg) for 5 front NOV candidates."""
    return list(NOV_THETA_PHI)
