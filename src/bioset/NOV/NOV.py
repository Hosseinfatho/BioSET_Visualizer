# NOV (Next Best View): single-file module.
# Sphere candidates, camera math, mesh-based view scoring, UI callbacks.
# Score = visibility_weight * (filled/total) - occlusion_weight * (occluded/total).

from __future__ import annotations

import math
import time
from typing import List, Tuple, TYPE_CHECKING

from bioset.streaming.lod import camera_distance_to_focal, choose_component

if TYPE_CHECKING:
    from bioset.streaming.lod import ROI

# --- Constants ---
NOV_THETA_PHI: List[Tuple[float, float]] = [
    (90.0, 0.0), (45.0, 45.0), (135.0, 45.0), (45.0, -45.0), (135.0, -45.0),
    (90.0, 30.0), (90.0, -30.0), (60.0, 0.0), (120.0, 0.0), (90.0, 60.0), (90.0, -60.0),
]
NOV_MESH_SIZE = 500
VISIBILITY_WEIGHT = 0.8
OCCLUSION_WEIGHT = 0.2

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

# --- Sphere candidates ---
def get_nov_sphere_points() -> List[Tuple[float, float]]:
    return list(NOV_THETA_PHI)

# --- Camera from sphere (theta, phi in degrees) ---
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

# --- Mesh-based view score ---
def compute_view_score_mesh(
    camera_pos: Tuple[float, float, float],
    focal: Tuple[float, float, float],
    view_up: Tuple[float, float, float],
    radius: float,
    bounds_world: Tuple[float, float, float, float, float, float],
    num_channels: int,
    *,
    mesh_size: int = NOV_MESH_SIZE,
    visibility_weight: float = VISIBILITY_WEIGHT,
    occlusion_weight: float = OCCLUSION_WEIGHT,
) -> float:
    if num_channels <= 0:
        return 0.0
    normal = _norm3((focal[0]-camera_pos[0], focal[1]-camera_pos[1], focal[2]-camera_pos[2]))
    up = _norm3((view_up[0], view_up[1], view_up[2]))
    u_axis = _norm3(_cross(normal, up))
    v_axis = _norm3(_cross(normal, u_axis))
    corners = _aabb_corners(bounds_world)
    uv = [(_dot((c[0]-focal[0], c[1]-focal[1], c[2]-focal[2]), u_axis), _dot((c[0]-focal[0], c[1]-focal[1], c[2]-focal[2]), v_axis)) for c in corners]
    hull = _hull2(uv)
    if len(hull) < 3:
        return 0.0
    total = mesh_size * mesh_size
    cell = (2.0 * radius) / mesh_size
    count = [[0] * mesh_size for _ in range(mesh_size)]
    umin, umax = min(p[0] for p in hull), max(p[0] for p in hull)
    vmin, vmax = min(p[1] for p in hull), max(p[1] for p in hull)
    i0 = max(0, int((umin + radius) / (2.0 * radius) * mesh_size))
    i1 = min(mesh_size, int((umax + radius) / (2.0 * radius) * mesh_size) + 1)
    j0 = max(0, int((vmin + radius) / (2.0 * radius) * mesh_size))
    j1 = min(mesh_size, int((vmax + radius) / (2.0 * radius) * mesh_size) + 1)
    for _ in range(num_channels):
        for i in range(i0, i1):
            for j in range(j0, j1):
                uc = -radius + (i + 0.5) * cell
                vc = -radius + (j + 0.5) * cell
                if _in_poly((uc, vc), hull):
                    count[i][j] += 1
    filled = sum(1 for row in count for c in row if c >= 1)
    occluded = sum(1 for row in count for c in row if c >= 2)
    score = visibility_weight * (filled / total) - occlusion_weight * (occluded / total)
    return max(0.0, min(1.0, score))

def normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    mx = max(scores)
    return [0.0] * len(scores) if mx <= 0 else [s / mx for s in scores]

# --- Legacy ROI scoring (optional API) ---
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

# --- Callbacks ---
def register_nov_callbacks(ctrl, state, _refs):
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

    def nov_toggle():
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None) or not streamer.renderer:
            state.nov_panel_visible = False
            print("[NOV] Skipped: no streamer or renderer")
            return
        t0 = time.perf_counter()
        cam = streamer.renderer.GetActiveCamera()
        focal = list(cam.GetFocalPoint())
        radius = camera_distance_to_focal(cam)
        if radius < 1e-6:
            radius = 1.0
        comp, active_ch = get_lod(streamer)
        points = get_nov_sphere_points()
        bounds = streamer._volume_bounds_world(comp)
        nch = max(1, len(active_ch))
        raw_scores, candidates = [], []
        for i, (t_deg, p_deg) in enumerate(points):
            pos = camera_position_from_sphere(focal, radius, t_deg, p_deg)
            vup = view_up_for_sphere_point(t_deg, p_deg)
            sc = compute_view_score_mesh((pos[0], pos[1], pos[2]), (focal[0], focal[1], focal[2]), (vup[0], vup[1], vup[2]), radius, bounds, nch)
            raw_scores.append(sc)
            candidates.append({"camera": {"position": pos, "focalPoint": focal, "viewUp": vup}, "score_raw": sc, "theta_deg": t_deg, "phi_deg": p_deg, "fixed_index": i})
        normed = normalize_scores(raw_scores)
        for i, c in enumerate(candidates):
            c["score_normalized"] = normed[i] if i < len(normed) else 0.0
        candidates.sort(key=lambda x: x["score_normalized"], reverse=True)
        state.nov_candidates = candidates
        state.nov_current_index = 0
        r_svg, cx, cy = 22, 28, 28
        sphere_xy = [[round(cx + r_svg * math.sin(_rad(t)) * math.sin(_rad(p)), 1), round(cy - r_svg * math.cos(_rad(t)), 1)] for t, p in points]
        state.nov_sphere_xy = sphere_xy
        state.nov_sphere_svg = build_nov_sphere_svg(sphere_xy, candidates[0]["fixed_index"] if candidates else 0)
        state.nov_score_display = candidates[0]["score_normalized"] if candidates else 0.0
        state.nov_view_index_display = f"1/{len(candidates)}" if candidates else "0/0"
        state.nov_panel_visible = True
        if candidates:
            apply_cam(candidates[0])
        print(f"[NOV] Done: best={(candidates[0]['score_normalized'] if candidates else 0):.2f}, elapsed={time.perf_counter()-t0:.2f}s")
        if _refs.get("view"):
            _refs["view"].update()

    def recompute():
        streamer = _refs.get("streamer")
        cands = getattr(state, "nov_candidates", []) or []
        if not streamer or not cands:
            return
        comp, active_ch = get_lod(streamer)
        bounds = streamer._volume_bounds_world(comp)
        nch = max(1, len(active_ch))
        cur = getattr(state, "nov_current_index", 0)
        fixed = cands[cur]["fixed_index"] if cur < len(cands) else 0
        for c in cands:
            pos, focal, vup = c["camera"]["position"], c["camera"]["focalPoint"], c["camera"]["viewUp"]
            r = math.sqrt((pos[0]-focal[0])**2 + (pos[1]-focal[1])**2 + (pos[2]-focal[2])**2) or 1.0
            c["score_raw"] = compute_view_score_mesh((pos[0],pos[1],pos[2]), (focal[0],focal[1],focal[2]), (vup[0],vup[1],vup[2]), r, bounds, nch)
        normed = normalize_scores([c["score_raw"] for c in cands])
        for i, c in enumerate(cands):
            c["score_normalized"] = normed[i] if i < len(normed) else 0.0
        cands.sort(key=lambda x: x["score_normalized"], reverse=True)
        state.nov_candidates = cands
        new_idx = next((k for k, c in enumerate(cands) if c["fixed_index"] == fixed), 0)
        state.nov_current_index = new_idx
        state.nov_sphere_svg = build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), fixed)
        state.nov_score_display = cands[new_idx]["score_normalized"]
        state.nov_view_index_display = f"{new_idx+1}/{len(cands)}"

    def switch(step):
        cands = getattr(state, "nov_candidates", []) or []
        if not cands:
            return
        idx = (getattr(state, "nov_current_index", 0) + step) % len(cands)
        state.nov_current_index = idx
        state.nov_sphere_svg = build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), cands[idx]["fixed_index"])
        apply_cam(cands[idx])
        state.nov_score_display = cands[idx]["score_normalized"]
        state.nov_view_index_display = f"{idx+1}/{len(cands)}"
        if _refs.get("view"):
            _refs["view"].update()

    ctrl.nov_toggle = nov_toggle
    ctrl.nov_prev = lambda: switch(-1)
    ctrl.nov_next = lambda: switch(1)
    ctrl.nov_recompute_scores_if_visible = lambda: recompute() if getattr(state, "nov_panel_visible", False) else None
