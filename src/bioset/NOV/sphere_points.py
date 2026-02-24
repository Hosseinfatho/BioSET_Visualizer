# sphere_points.py
"""Define 10 candidate viewpoints: 5 front (F), 5 back (B). (theta_deg, phi_deg)."""

from __future__ import annotations

from typing import List, Tuple

# Sphere: theta in [0, 180], phi in [0, 360]. phi=0, theta=90 = original view (front).
# theta = elevation (0=+Z, 90=equator, 180=-Z), phi = azimuth (0=+X, 90=+Y).
# Order: front 5 first, then back 5.
# Front: phi=0,theta=90 (original); phi=45,theta=45; phi=45,theta=135; phi=-45,theta=45; phi=-45,theta=135.
# Back:  phi=180,theta=90; phi=135,theta=45; phi=135,theta=135; phi=225,theta=45; phi=225,theta=135.
NOV_THETA_PHI: List[Tuple[float, float]] = [
    (90.0, 0.0),    # front center (original view)
    (45.0, 45.0),
    (135.0, 45.0),
    (45.0, -45.0),  # phi -45
    (135.0, -45.0),
    (90.0, 180.0),  # back center
    (45.0, 135.0),
    (135.0, 135.0),
    (45.0, 225.0),
    (135.0, 225.0),
]


def get_nov_sphere_points() -> List[Tuple[float, float]]:
    """Return list of (theta_deg, phi_deg) for 10 NOV candidates (5 front, 5 back)."""
    return list(NOV_THETA_PHI)
