# NOV: Next Best View - box ROI, resize via corner pins.

from .NOV import (
    camera_position_at_radius,
    view_up_for_angle,
    compute_view_score_mesh,
    compute_best_views,
    aabb_from_center_radius,
    bounds_intersect,
    normalize_scores,
    NOV_MESH_SIZE,
    VISIBILITY_WEIGHT,
    OCCLUSION_WEIGHT,
    register_nov_callbacks,
)

__all__ = [
    "camera_position_at_radius",
    "view_up_for_angle",
    "compute_view_score_mesh",
    "compute_best_views",
    "aabb_from_center_radius",
    "bounds_intersect",
    "normalize_scores",
    "NOV_MESH_SIZE",
    "VISIBILITY_WEIGHT",
    "OCCLUSION_WEIGHT",
    "register_nov_callbacks",
]
