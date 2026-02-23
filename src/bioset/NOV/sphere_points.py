# sphere_points.py
"""Define candidate viewpoints on a sphere (theta, phi in degrees)."""

from __future__ import annotations

from typing import List, Tuple

# Theta: elevation 0--180 (0 = +Z pole, 180 = -Z). Split into 3: 45°, 90°, 135°.
# Phi: azimuth 0--360. Split into 6: 60° steps -> 60, 120, 180, 240, 300, 360 (0).
THETA_VALUES = (45.0, 90.0, 135.0)
PHI_STEP = 360.0 / 6  # 60°
PHI_VALUES = tuple(i * PHI_STEP for i in range(6))  # 0, 60, 120, 180, 240, 300

NOV_THETA_PHI: List[Tuple[float, float]] = [
    (theta, phi) for theta in THETA_VALUES for phi in PHI_VALUES
]


def get_nov_sphere_points() -> List[Tuple[float, float]]:
    """Return list of (theta_deg, phi_deg) for all NOV candidates (3×6 = 18 points)."""
    return list(NOV_THETA_PHI)
