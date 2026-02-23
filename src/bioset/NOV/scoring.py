# scoring.py
"""ROI-based viewpoint score: Score = alpha * Visible_ROI_Area - beta * Occlusion."""

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
    Score = alpha * Visible_ROI_Area - beta * Occlusion.
    Default alpha=beta=0.5. Caller should normalize scores to [0, 1] across candidates if desired.
    """
    return max(0.0, alpha * visible_roi_area - beta * occlusion)


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
