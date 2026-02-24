# NOV: Next Best View - camera position scoring and candidate generation.

from .sphere_points import get_nov_sphere_points
from .camera import camera_position_from_sphere, view_up_for_sphere_point
from .scoring import compute_view_score, compute_view_score_fraction
from .mesh_score import compute_view_score_mesh, normalize_scores, NOV_MESH_SIZE, VISIBILITY_WEIGHT, OCCLUSION_WEIGHT

__all__ = [
    "get_nov_sphere_points",
    "camera_position_from_sphere",
    "view_up_for_sphere_point",
    "compute_view_score",
    "compute_view_score_fraction",
    "compute_view_score_mesh",
    "normalize_scores",
    "NOV_MESH_SIZE",
    "VISIBILITY_WEIGHT",
    "OCCLUSION_WEIGHT",
]
