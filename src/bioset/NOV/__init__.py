# NOV: Next Best View - single module.

from .NOV import (
    get_nov_sphere_points,
    camera_position_from_sphere,
    view_up_for_sphere_point,
    compute_view_score,
    compute_view_score_fraction,
    visible_roi_area_from_roi,
    compute_view_score_mesh,
    normalize_scores,
    NOV_MESH_SIZE,
    VISIBILITY_WEIGHT,
    OCCLUSION_WEIGHT,
    register_nov_callbacks,
    build_nov_sphere_svg,
)

__all__ = [
    "get_nov_sphere_points",
    "camera_position_from_sphere",
    "view_up_for_sphere_point",
    "compute_view_score",
    "compute_view_score_fraction",
    "visible_roi_area_from_roi",
    "compute_view_score_mesh",
    "normalize_scores",
    "NOV_MESH_SIZE",
    "VISIBILITY_WEIGHT",
    "OCCLUSION_WEIGHT",
    "register_nov_callbacks",
    "build_nov_sphere_svg",
]
