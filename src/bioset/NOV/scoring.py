# scoring.py
"""ROI-based viewpoint score for Next-Best-View (NOV).

Two modes:
- compute_view_score(area, occlusion, alpha, beta): raw formula
  Score = max(0, alpha*area - beta*occlusion). Problem: area and occlusion are in
  voxel² (millions), so with alpha=beta=0.5 the term is negative whenever
  area < occlusion (typical for partial views), so score is always 0.

- compute_view_score_fraction(area, total_xy): recommended for NOV
  Score = area / total_xy (visible fraction in [0, 1]). Ranks views by how much
  of the volume is visible; then normalize_scores() gives [0,1] relative to best.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..streaming.lod import ROI


def compute_view_score(
    visible_roi_area: float,
    occlusion: float = 0.0,
    alpha: float = 0.5,
    beta: float = 0.5,
) -> float:
    """
    Score = max(0, alpha * Visible_ROI_Area - beta * Occlusion).
    With raw voxel counts, this often gives 0 when area < occlusion. Prefer
    compute_view_score_fraction() for NOV.
    """
    return max(0.0, alpha * visible_roi_area - beta * occlusion)


def compute_view_score_fraction(visible_roi_area: float, total_xy_area: float) -> float:
    """
    Score = visible fraction = area / total_xy (in [0, 1]).
    Use this for NOV so views are ranked by how much of the volume is visible.
    """
    if total_xy_area <= 0:
        return 0.0
    return float(visible_roi_area) / float(total_xy_area)


def visible_roi_area_from_roi(roi: "ROI") -> float:
    """Area in voxel space (can be normalized by full volume area elsewhere)."""
    w = max(0, roi.x1 - roi.x0)
    h = max(0, roi.y1 - roi.y0)
    return float(w * h)


def normalize_scores(scores: list[float]) -> list[float]:
    """Normalize list of scores to [0, 1]. If all same or max 0, return [1,1,...] or zeros."""
    if not scores:
        return []
    mx = max(scores)
    if mx <= 0:
        return [0.0] * len(scores)
    return [s / mx for s in scores]
