# NOV (Next Best View): sphere ROI, camera candidates, mesh-based scoring.
# User draws a sphere (right-click + drag); best camera views for that sphere only.

from __future__ import annotations

import math
import time
from typing import List, Tuple, TYPE_CHECKING

from bioset.streaming.lod import camera_distance_to_focal, choose_component

if TYPE_CHECKING:
    from bioset.streaming.lod import ROI

# VTK for sphere overlay: wireframe sphere (mesh type, was visible before)
try:
    from vtkmodules.vtkFiltersSources import vtkSphereSource
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper
    _VTK_SPHERE_AVAILABLE = True
except Exception:
    vtkSphereSource = vtkActor = vtkPolyDataMapper = None
    _VTK_SPHERE_AVAILABLE = False

# --- Constants ---
# 10 points: 5 front (theta, phi) + 5 back; sorted by score high→low in compute_best_views_for_sphere
# Front: phi=180,90 | 135,45 | 135,135 | 225,45 | 225,135
# Back:  phi=0,90   | 45,45  | 45,135  | -45,45 | -45,135
NOV_THETA_PHI: List[Tuple[float, float]] = [
    (90.0, 180.0), (45.0, 135.0), (135.0, 135.0), (45.0, 225.0), (135.0, 225.0),   # front
    (90.0, 0.0), (45.0, 45.0), (135.0, 45.0), (45.0, -45.0), (135.0, -45.0),       # back
]
NOV_MESH_SIZE = 500
VISIBILITY_WEIGHT = 0.8
OCCLUSION_WEIGHT = 0.2
MIN_SPHERE_RADIUS = 1.0

# --- Math helpers ---
def _rad(d: float) -> float:
    return d * math.pi / 180.0

def _norm3(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    n = math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])
    return (v[0]/n, v[1]/n, v[2]/n) if n >= 1e-12 else (0.0, 0.0, 0.0)

def _cross(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])

def _dot(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]

def _hull2(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if len(points) <= 2:
        return list(points)
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    def cross_o(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross_o(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross_o(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]

def _in_poly(q: Tuple[float, float], poly: List[Tuple[float, float]]) -> bool:
    n, x, y = len(poly), q[0], q[1]
    if n < 3:
        return False
    inside, j = False, n - 1
    for i in range(n):
        xi, yi, xj, yj = poly[i][0], poly[i][1], poly[j][0], poly[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-20) + xi):
            inside = not inside
        j = i
    return inside

def _aabb_corners(b: Tuple[float, float, float, float, float, float]) -> List[Tuple[float, float, float]]:
    x0, x1, y0, y1, z0, z1 = b
    return [(x0,y0,z0),(x1,y0,z0),(x0,y1,z0),(x1,y1,z0),(x0,y0,z1),(x1,y0,z1),(x0,y1,z1),(x1,y1,z1)]

# --- ROI sphere ---
def sphere_aabb(center: Tuple[float, float, float], radius: float) -> Tuple[float, float, float, float, float, float]:
    """AABB of sphere (xmin,xmax, ymin,ymax, zmin,zmax)."""
    cx, cy, cz = center
    return (cx - radius, cx + radius, cy - radius, cy + radius, cz - radius, cz + radius)

def bounds_intersect(
    vol: Tuple[float, float, float, float, float, float],
    box: Tuple[float, float, float, float, float, float],
) -> Tuple[float, float, float, float, float, float]:
    """Intersect two AABBs; return degenerate (0,0,0,0,0,0) if no overlap."""
    x0 = max(vol[0], box[0])
    x1 = min(vol[1], box[1])
    y0 = max(vol[2], box[2])
    y1 = min(vol[3], box[3])
    z0 = max(vol[4], box[4])
    z1 = min(vol[5], box[5])
    if x0 >= x1 or y0 >= y1 or z0 >= z1:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return (x0, x1, y0, y1, z0, z1)

# --- Camera candidates ---
def get_nov_sphere_points() -> List[Tuple[float, float]]:
    return list(NOV_THETA_PHI)

def camera_position_from_sphere(center: Tuple[float, float, float], radius: float, theta_deg: float, phi_deg: float) -> List[float]:
    th, ph = _rad(theta_deg), _rad(phi_deg)
    dx = radius * math.sin(th) * math.sin(ph)
    dy = radius * math.cos(th)
    dz = -radius * math.sin(th) * math.cos(ph)
    return [center[0] + dx, center[1] + dy, center[2] + dz]

def view_up_for_sphere_point(theta_deg: float, phi_deg: float) -> List[float]:
    th, ph = _rad(theta_deg), _rad(phi_deg)
    nx = math.sin(th) * math.sin(ph)
    ny = math.cos(th)
    nz = -math.sin(th) * math.cos(ph)
    ux, uy, uz = 0.0, 1.0, 0.0
    if abs(ny) > 0.99:
        ux, uy, uz = 0.0, 0.0, 1.0
    dot = ux*nx + uy*ny + uz*nz
    vx, vy, vz = ux - dot*nx, uy - dot*ny, uz - dot*nz
    n = math.sqrt(vx*vx + vy*vy + vz*vz)
    return [vx/n, vy/n, vz/n] if n >= 1e-9 else [0.0, 0.0, 1.0]

# --- View score (plane at focal, perpendicular to camera–focal line) ---
def compute_view_score_mesh(
    camera_pos: Tuple[float, float, float],
    center: Tuple[float, float, float],
    view_up: Tuple[float, float, float],
    view_radius: float,
    bounds_world: Tuple[float, float, float, float, float, float],
    num_channels: int,
    *,
    mesh_size: int = NOV_MESH_SIZE,
    visibility_weight: float = VISIBILITY_WEIGHT,
    occlusion_weight: float = OCCLUSION_WEIGHT,
) -> float:
    """Score one viewpoint: plane at center (focal), perpendicular to (camera_pos -> center); mesh square has half-size view_radius (total extent 2*view_radius)."""
    if num_channels <= 0:
        return 0.0
    normal = _norm3((center[0]-camera_pos[0], center[1]-camera_pos[1], center[2]-camera_pos[2]))
    up = _norm3((view_up[0], view_up[1], view_up[2]))
    u_axis = _norm3(_cross(normal, up))
    v_axis = _norm3(_cross(normal, u_axis))
    corners = _aabb_corners(bounds_world)
    uv = [(_dot((c[0]-center[0], c[1]-center[1], c[2]-center[2]), u_axis), _dot((c[0]-center[0], c[1]-center[1], c[2]-center[2]), v_axis)) for c in corners]
    hull = _hull2(uv)
    if len(hull) < 3:
        return 0.0
    total = mesh_size * mesh_size
    cell = (2.0 * view_radius) / mesh_size
    umin, umax = min(p[0] for p in hull), max(p[0] for p in hull)
    vmin, vmax = min(p[1] for p in hull), max(p[1] for p in hull)
    i0 = max(0, int((umin + view_radius) / (2.0 * view_radius) * mesh_size))
    i1 = min(mesh_size, int((umax + view_radius) / (2.0 * view_radius) * mesh_size) + 1)
    j0 = max(0, int((vmin + view_radius) / (2.0 * view_radius) * mesh_size))
    j1 = min(mesh_size, int((vmax + view_radius) / (2.0 * view_radius) * mesh_size) + 1)
    filled = 0
    for i in range(i0, i1):
        for j in range(j0, j1):
            uc = -view_radius + (i + 0.5) * cell
            vc = -view_radius + (j + 0.5) * cell
            if _in_poly((uc, vc), hull):
                filled += 1
    occluded = filled * (num_channels - 1) / num_channels if num_channels >= 2 else 0
    score = visibility_weight * (filled / total) - occlusion_weight * (occluded / total)
    return max(0.0, min(1.0, score))

def normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    mx = max(scores)
    return [0.0] * len(scores) if mx <= 0 else [s / mx for s in scores]

# --- Best views for sphere ROI ---
def compute_best_views_for_sphere(
    sphere_center: Tuple[float, float, float],
    sphere_radius: float,
    volume_bounds: Tuple[float, float, float, float, float, float],
    num_channels: int,
    *,
    camera_distance: float | None = None,
) -> List[dict]:
    """Camera at 2*sphere_radius from focal (center); scoring plane at focal, perpendicular to view, extent 2*sphere_radius."""
    if sphere_radius < 1e-6:
        sphere_radius = MIN_SPHERE_RADIUS
    roi_bounds = bounds_intersect(volume_bounds, sphere_aabb(sphere_center, sphere_radius))
    if roi_bounds[1] <= roi_bounds[0] or roi_bounds[3] <= roi_bounds[2] or roi_bounds[5] <= roi_bounds[4]:
        return []
    # Camera distance = 2 * sphere radius; plane for scoring = at focal, perpendicular to camera–focal, extent 2*radius
    cam_r = camera_distance if camera_distance is not None and camera_distance >= 1e-6 else max(2.0 * sphere_radius, MIN_SPHERE_RADIUS)
    center = (sphere_center[0], sphere_center[1], sphere_center[2])
    points = get_nov_sphere_points()
    raw_scores, candidates = [], []
    for i, (t_deg, p_deg) in enumerate(points):
        pos = camera_position_from_sphere(center, cam_r, t_deg, p_deg)
        vup = view_up_for_sphere_point(t_deg, p_deg)
        sc = compute_view_score_mesh((pos[0], pos[1], pos[2]), center, (vup[0], vup[1], vup[2]), sphere_radius, roi_bounds, num_channels)
        raw_scores.append(sc)
        side = "F" if i < 5 else "B"
        candidates.append({
            "camera": {"position": pos, "focalPoint": list(center), "viewUp": vup},
            "score_raw": sc, "theta_deg": t_deg, "phi_deg": p_deg, "fixed_index": i, "side": side,
        })
    normed = normalize_scores(raw_scores)
    for i, c in enumerate(candidates):
        c["score_normalized"] = normed[i] if i < len(normed) else 0.0
    candidates.sort(key=lambda x: x["score_normalized"], reverse=True)
    return candidates

# --- Legacy (optional) ---
def compute_view_score(visible_roi_area: float, occlusion: float = 0.0, alpha: float = 0.5, beta: float = 0.5) -> float:
    return max(0.0, alpha * visible_roi_area - beta * occlusion)

def compute_view_score_fraction(visible_roi_area: float, total_xy_area: float) -> float:
    return float(visible_roi_area) / float(total_xy_area) if total_xy_area > 0 else 0.0

def visible_roi_area_from_roi(roi: "ROI") -> float:
    return float(max(0, roi.x1 - roi.x0) * max(0, roi.y1 - roi.y0))

# --- SVG ---
def build_nov_sphere_svg(sphere_xy: list, current_index: int) -> str:
    if not sphere_xy:
        return ""
    parts = [
        '<svg width="28" height="28" viewBox="0 0 56 56" style="display:block">',
        '<circle cx="28" cy="28" r="22" fill="none" stroke="rgba(255,255,255,0.4)" stroke-width="1.5"/>',
    ]
    for i, (x, y) in enumerate(sphere_xy):
        active = i == current_index
        r = 4 if active else 2.5
        fill = "#fff" if active else "rgba(255,255,255,0.5)"
        stroke = "#1976d2" if active else "transparent"
        sw = 1.5 if active else 0
        parts.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
    parts.append("</svg>")
    return "".join(parts)

# --- Callbacks (sphere draw + NOV run) ---
def register_nov_callbacks(ctrl, state, _refs):
    """Register nov_toggle (enter sphere mode), nov_handle_click/drag/release, nov_prev, nov_next, nov_recompute_scores_if_visible."""
    from bioset.streaming.lod import _display_to_world

    def _ensure_nov_sphere_actor():
        if not _VTK_SPHERE_AVAILABLE or vtkSphereSource is None:
            return None, None
        if "_nov_sphere_actor" not in _refs:
            src = vtkSphereSource()
            src.SetPhiResolution(12)
            src.SetThetaResolution(12)
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(src.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetRepresentationToWireframe()
            actor.GetProperty().SetColor(0.0, 1.0, 0.5)
            actor.GetProperty().SetLineWidth(3.0)
            actor.GetProperty().SetOpacity(0.95)
            _refs["_nov_sphere_source"] = src
            _refs["_nov_sphere_actor"] = actor
        return _refs.get("_nov_sphere_source"), _refs.get("_nov_sphere_actor")

    def _update_nov_sphere(center, radius: float, visible: bool):
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        ren = streamer.renderer
        src, actor = _ensure_nov_sphere_actor()
        if not src or not actor:
            return
        if not visible or not center or len(center) < 3:
            if ren.HasViewProp(actor):
                ren.RemoveActor(actor)
            return
        _, b = _volume_center_bounds(streamer)
        min_ext = min(b[1] - b[0], b[3] - b[2], b[5] - b[4])
        min_r = max(MIN_SPHERE_RADIUS, min_ext * 0.02)
        r = max(float(radius), min_r)
        src.SetCenter(center[0], center[1], center[2])
        src.SetRadius(r)
        src.Update()
        if not ren.HasViewProp(actor):
            ren.AddActor(actor)

    def nov_hide_sphere():
        _update_nov_sphere(None, 0.0, False)
        if _refs.get("view"):
            _refs["view"].update()

    def apply_cam(cam_dict):
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None) or not streamer.renderer:
            return
        c = cam_dict.get("camera") or cam_dict
        cam = streamer.renderer.GetActiveCamera()
        if c.get("position") and len(c["position"]) >= 3:
            cam.SetPosition(c["position"][:3])
        if c.get("focalPoint") and len(c.get("focalPoint", [])) >= 3:
            cam.SetFocalPoint(c["focalPoint"][:3])
        if c.get("viewUp") and len(c.get("viewUp", [])) >= 3:
            cam.SetViewUp(c["viewUp"][:3])
        streamer.renderer.ResetCameraClippingRange()
        if _refs.get("view"):
            _refs["view"].update()

    def display_to_world_xy(renderer, x: float, y: float):
        w, h = renderer.GetSize()
        if w < 1 or h < 1:
            return None
        # Normalize to VTK display: origin bottom-left, 0..w-1, 0..h-1
        if 0 <= x <= 1 and 0 <= y <= 1:
            dx, dy = x * (w - 1), (1.0 - y) * (h - 1)
        else:
            dx, dy = min(max(x, 0), w - 1), min(max(h - 1 - y, 0), h - 1)
        pt = _display_to_world(renderer, dx, dy, 0.5)
        return pt

    def get_lod(s):
        active = list(s.get_active_channels()) if s else []
        comp = None
        if active:
            for ch_id in active:
                st = getattr(s, "state", None) and s.state.get(ch_id)
                if st is not None:
                    comp = st.component
                    break
        if comp is None and s and getattr(s, "renderer", None):
            cam = s.renderer.GetActiveCamera()
            r = camera_distance_to_focal(cam)
            comp = choose_component(r, s.cfg.distance_rules, min_component=s.cfg.min_component, max_component=s.cfg.max_component)
        return comp, active

    def run_nov_for_sphere(center: List[float], radius: float):
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        comp, active_ch = get_lod(streamer)
        vol_bounds = streamer._volume_bounds_world(comp)
        nch = max(1, len(active_ch))
        candidates = compute_best_views_for_sphere((center[0], center[1], center[2]), radius, vol_bounds, nch)
        if not candidates:
            state.nov_candidates = []
            state.nov_panel_visible = True
            if _refs.get("view"):
                _refs["view"].update()
            return
        points = get_nov_sphere_points()
        r_svg, cx, cy = 22, 28, 28
        sphere_xy = [[round(cx + r_svg * math.sin(_rad(t)) * math.sin(_rad(p)), 1), round(cy - r_svg * math.cos(_rad(t)), 1)] for t, p in points]
        state.nov_candidates = candidates
        state.nov_current_index = 0
        state.nov_sphere_xy = sphere_xy
        state.nov_sphere_svg = build_nov_sphere_svg(sphere_xy, candidates[0]["fixed_index"])
        state.nov_view_side = candidates[0].get("side", "F")
        state.nov_score_display = candidates[0]["score_normalized"]
        state.nov_view_index_display = f"1/{len(candidates)}"
        state.nov_panel_visible = True
        state.nov_sphere_center = list(center)
        state.nov_sphere_radius = radius
        _update_nov_sphere(state.nov_sphere_center, radius, True)
        apply_cam(candidates[0])
        if _refs.get("view"):
            _refs["view"].update()

    def _volume_center_bounds(streamer):
        comp, _ = get_lod(streamer)
        b = streamer._volume_bounds_world(comp)
        return [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2], b

    def nov_toggle():
        """Enter sphere-draw mode: sphere center = focal point; right-click = new center, right-drag = radius, right-release = run NOV. If already in NOV (viewing candidates), reset and start again."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            state.nov_panel_visible = False
            return
        # Reset previous NOV run: remove sphere from screen, hide < >, like first time
        state.nov_candidates = []
        state.nov_current_index = 0
        state.nov_view_index_display = ""
        state.nov_score_display = 0.0
        state.nov_sphere_svg = ""
        state.nov_view_side = ""
        state.nov_sphere_xy = []
        state.nov_drag_started = False
        state.nov_panel_visible = False
        _update_nov_sphere(None, 0.0, False)
        cam = streamer.renderer.GetActiveCamera()
        focal = list(cam.GetFocalPoint())
        _, b = _volume_center_bounds(streamer)
        ext = min(b[1] - b[0], b[3] - b[2], b[5] - b[4])
        state.nov_drawing_sphere = True
        state.nov_sphere_center = focal
        state.nov_sphere_radius = max(ext * 0.15, MIN_SPHERE_RADIUS)
        if _refs.get("view"):
            _refs["view"].update()

    def _parse_xy(*args):
        if len(args) == 1 and isinstance(args[0], (list, tuple)) and len(args[0]) >= 2:
            return float(args[0][0]), float(args[0][1])
        if len(args) >= 2:
            return float(args[0]), float(args[1])
        return None, None

    def nov_handle_click(*args):
        x, y = _parse_xy(*args)
        if x is None:
            return
        if not getattr(state, "nov_drawing_sphere", False):
            return
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        pt = display_to_world_xy(streamer.renderer, x, y)
        if pt is None:
            return
        state.nov_sphere_center = [pt[0], pt[1], pt[2]]
        state.nov_sphere_radius = 0.0
        state.nov_drag_started = True
        _update_nov_sphere(state.nov_sphere_center, 0.0, True)
        if _refs.get("view"):
            _refs["view"].update()

    def nov_handle_drag(*args):
        x, y = _parse_xy(*args)
        if x is None:
            return
        if not getattr(state, "nov_drag_started", False) or not getattr(state, "nov_sphere_center", None):
            return
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        pt = display_to_world_xy(streamer.renderer, x, y)
        if pt is None:
            return
        c = state.nov_sphere_center
        r = math.sqrt((pt[0]-c[0])**2 + (pt[1]-c[1])**2 + (pt[2]-c[2])**2)
        state.nov_sphere_radius = max(r, 0.0)
        _update_nov_sphere(state.nov_sphere_center, state.nov_sphere_radius, True)
        if _refs.get("view"):
            _refs["view"].update()

    def nov_handle_wheel(*args):
        """Move sphere center along Z with mouse wheel (delta > 0 forward, < 0 backward)."""
        delta = 1.0
        if args and isinstance(args[0], (int, float)):
            delta = float(args[0])
        if not getattr(state, "nov_drawing_sphere", False):
            return
        center = getattr(state, "nov_sphere_center", None)
        if not center or len(center) < 3:
            return
        streamer = _refs.get("streamer")
        if not streamer:
            return
        _, b = _volume_center_bounds(streamer)
        zmin, zmax = b[4], b[5]
        step = (zmax - zmin) * 0.05
        new_z = center[2] + delta * step
        state.nov_sphere_center = [center[0], center[1], max(zmin, min(zmax, new_z))]
        _update_nov_sphere(state.nov_sphere_center, getattr(state, "nov_sphere_radius", 0.0) or MIN_SPHERE_RADIUS, True)
        if _refs.get("view"):
            _refs["view"].update()

    def nov_handle_release(*args):
        x, y = _parse_xy(*args)
        if not getattr(state, "nov_drag_started", False):
            state.nov_drawing_sphere = False
            state.nov_drag_started = False
            if _refs.get("view"):
                _refs["view"].update()
            return
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            state.nov_drawing_sphere = False
            state.nov_drag_started = False
            if _refs.get("view"):
                _refs["view"].update()
            return
        center = getattr(state, "nov_sphere_center", None)
        radius = max(getattr(state, "nov_sphere_radius", 0.0), MIN_SPHERE_RADIUS)
        state.nov_drawing_sphere = False
        state.nov_drag_started = False
        if center and len(center) >= 3:
            run_nov_for_sphere(center, radius)

    def recompute():
        streamer = _refs.get("streamer")
        cands = getattr(state, "nov_candidates", []) or []
        if not streamer or not cands:
            return
        center = getattr(state, "nov_sphere_center", None)
        radius = max(getattr(state, "nov_sphere_radius", 0.0), MIN_SPHERE_RADIUS)
        if not center or len(center) < 3:
            center = cands[0]["camera"]["focalPoint"]
        comp, active_ch = get_lod(streamer)
        vol_bounds = streamer._volume_bounds_world(comp)
        nch = max(1, len(active_ch))
        cur = getattr(state, "nov_current_index", 0)
        fixed = cands[cur]["fixed_index"] if cur < len(cands) else 0
        cam_r = max(2.0 * radius, MIN_SPHERE_RADIUS)
        candidates = compute_best_views_for_sphere((center[0], center[1], center[2]), radius, vol_bounds, nch, camera_distance=cam_r)
        if not candidates:
            return
        normed = normalize_scores([c["score_raw"] for c in candidates])
        for i, c in enumerate(candidates):
            c["score_normalized"] = normed[i] if i < len(normed) else 0.0
        candidates.sort(key=lambda x: x["score_normalized"], reverse=True)
        state.nov_candidates = candidates
        new_idx = next((k for k, c in enumerate(candidates) if c["fixed_index"] == fixed), 0)
        state.nov_current_index = new_idx
        state.nov_sphere_svg = build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), candidates[new_idx]["fixed_index"])
        state.nov_view_side = candidates[new_idx].get("side", "F")
        state.nov_score_display = candidates[new_idx]["score_normalized"]
        state.nov_view_index_display = f"{new_idx+1}/{len(candidates)}"
        if _refs.get("view"):
            _refs["view"].update()

    def switch(step):
        cands = getattr(state, "nov_candidates", []) or []
        if not cands:
            return
        idx = (getattr(state, "nov_current_index", 0) + step) % len(cands)
        state.nov_current_index = idx
        state.nov_sphere_svg = build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), cands[idx]["fixed_index"])
        state.nov_view_side = cands[idx].get("side", "F")
        apply_cam(cands[idx])
        state.nov_score_display = cands[idx]["score_normalized"]
        state.nov_view_index_display = f"{idx+1}/{len(cands)}"
        if _refs.get("view"):
            _refs["view"].update()

    ctrl.nov_toggle = nov_toggle
    ctrl.nov_handle_click = nov_handle_click
    ctrl.nov_handle_drag = nov_handle_drag
    ctrl.nov_handle_release = nov_handle_release
    ctrl.nov_handle_wheel = nov_handle_wheel
    ctrl.nov_wheel_forward = lambda: nov_handle_wheel(1.0)
    ctrl.nov_wheel_backward = lambda: nov_handle_wheel(-1.0)
    ctrl.nov_hide_sphere = nov_hide_sphere
    ctrl.nov_prev = lambda: switch(-1)
    ctrl.nov_next = lambda: switch(1)
    ctrl.nov_recompute_scores_if_visible = lambda: recompute() if getattr(state, "nov_panel_visible", False) else None
