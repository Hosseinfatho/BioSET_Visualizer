# NOV (Next Best View): box ROI, camera candidates. Resize by right-click and drag on corner pins.

from __future__ import annotations

import math
from typing import List, Tuple, TYPE_CHECKING

from bioset.streaming.lod import camera_distance_to_focal, choose_component

if TYPE_CHECKING:
    from bioset.streaming.lod import ROI

# VTK for box overlay and corner pins
try:
    from vtkmodules.vtkFiltersSources import vtkCubeSource, vtkSphereSource
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkPropPicker
    _VTK_BOX_AVAILABLE = True
except Exception:
    vtkCubeSource = vtkSphereSource = vtkActor = vtkPolyDataMapper = vtkPropPicker = None
    _VTK_BOX_AVAILABLE = False

# --- Constants ---
# 10 camera angles (theta, phi): 5 front + 5 back; sorted by score in compute_best_views
NOV_THETA_PHI: List[Tuple[float, float]] = [
    (90.0, 180.0), (45.0, 135.0), (135.0, 135.0), (45.0, 225.0), (135.0, 225.0),   # front
    (90.0, 0.0), (45.0, 45.0), (135.0, 45.0), (45.0, -45.0), (135.0, -45.0),       # back
]
NOV_MESH_SIZE = 500
VISIBILITY_WEIGHT = 0.8
OCCLUSION_WEIGHT = 0.2
MIN_BOX_HALF = 25.0  # minimum half-extent for box
MIN_CAMERA_RADIUS = 50.0  # minimum radius for camera placement around box
CAMERA_DISTANCE_MULTIPLIER = 5.0  # camera distance from focal

def box_circum_radius(length: float, width: float, depth: float) -> float:
    """Distance from box center to corner (used for camera placement)."""
    return 0.5 * math.sqrt(length * length + width * width + depth * depth)

def cube_size_from_circum_radius(radius: float) -> float:
    """For a cube, side length such that circumscribing sphere has given radius."""
    return 2.0 * radius / math.sqrt(3.0) if radius > 1e-9 else 2.0 * MIN_BOX_HALF

# Corner order: 0=(-,-,-), 1=(+,-,-), 2=(-,+,-), 3=(+,+,-), 4=(-,-,+), 5=(+,-,+), 6=(-,+,+), 7=(+,+,+). Opposite of i is 7-i.
def box_corners(center: Tuple[float, float, float], length: float, width: float, depth: float) -> List[Tuple[float, float, float]]:
    """Return 8 corner positions (x,y,z) of the box."""
    cx, cy, cz = center[0], center[1], center[2]
    hL, hW, hD = length / 2.0, width / 2.0, depth / 2.0
    return [
        (cx - hL, cy - hW, cz - hD), (cx + hL, cy - hW, cz - hD),
        (cx - hL, cy + hW, cz - hD), (cx + hL, cy + hW, cz - hD),
        (cx - hL, cy - hW, cz + hD), (cx + hL, cy - hW, cz + hD),
        (cx - hL, cy + hW, cz + hD), (cx + hL, cy + hW, cz + hD),
    ]

PIN_RADIUS_FRACTION = 0.06  # pin radius = this fraction of min(L,W,D)

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

# --- ROI bounds (center + radius for camera scoring) ---
def aabb_from_center_radius(center: Tuple[float, float, float], radius: float) -> Tuple[float, float, float, float, float, float]:
    """AABB from center and radius (xmin,xmax, ymin,ymax, zmin,zmax)."""
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

# --- Camera candidates (positions on radius around center) ---
def get_nov_camera_angles() -> List[Tuple[float, float]]:
    return list(NOV_THETA_PHI)

def camera_position_at_radius(center: Tuple[float, float, float], radius: float, theta_deg: float, phi_deg: float) -> List[float]:
    """Position at given radius around center (theta, phi in degrees)."""
    th, ph = _rad(theta_deg), _rad(phi_deg)
    dx = radius * math.sin(th) * math.sin(ph)
    dy = radius * math.cos(th)
    dz = -radius * math.sin(th) * math.cos(ph)
    return [center[0] + dx, center[1] + dy, center[2] + dz]

def view_up_for_angle(theta_deg: float, phi_deg: float) -> List[float]:
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

# --- Best views (cameras on radius around center) ---
def compute_best_views(
    center: Tuple[float, float, float],
    view_radius: float,
    volume_bounds: Tuple[float, float, float, float, float, float],
    num_channels: int,
    *,
    camera_distance: float | None = None,
) -> List[dict]:
    """Compute candidate camera positions at camera_distance around center; score by visibility of ROI (view_radius)."""
    if view_radius < 1e-6:
        view_radius = MIN_CAMERA_RADIUS
    roi_bounds = bounds_intersect(volume_bounds, aabb_from_center_radius(center, view_radius))
    if roi_bounds[1] <= roi_bounds[0] or roi_bounds[3] <= roi_bounds[2] or roi_bounds[5] <= roi_bounds[4]:
        return []
    cam_dist = camera_distance if camera_distance is not None and camera_distance >= 1e-6 else max(CAMERA_DISTANCE_MULTIPLIER * view_radius, MIN_CAMERA_RADIUS)
    points = get_nov_camera_angles()
    raw_scores, candidates = [], []
    for i, (t_deg, p_deg) in enumerate(points):
        pos = camera_position_at_radius(center, cam_dist, t_deg, p_deg)
        vup = view_up_for_angle(t_deg, p_deg)
        sc = compute_view_score_mesh((pos[0], pos[1], pos[2]), center, (vup[0], vup[1], vup[2]), view_radius, roi_bounds, num_channels)
        raw_scores.append(sc)
        candidates.append({
            "camera": {"position": pos, "focalPoint": list(center), "viewUp": vup},
            "score_raw": sc, "theta_deg": t_deg, "phi_deg": p_deg, "fixed_index": i,
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

# --- Callbacks (box + corner pins) ---
def register_nov_callbacks(ctrl, state, _refs):
    """Register nov_toggle, nov_handle_click/drag/release (corner resize), nov_hide_box."""
    from bioset.streaming.lod import _display_to_world

    def _ensure_nov_box_actor():
        if not _VTK_BOX_AVAILABLE or vtkCubeSource is None:
            return None, None
        if "_nov_box_actor" not in _refs:
            src = vtkCubeSource()
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(src.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(0.4, 1.0, 0.45)
            actor.GetProperty().SetOpacity(0.1)
            actor.GetProperty().SetAmbient(0.9)
            actor.GetProperty().SetDiffuse(0.1)
            actor.GetProperty().SetSpecular(0.1)
            actor.GetProperty().SetSpecularPower(20.0)
            actor.GetProperty().BackfaceCullingOff()
            _refs["_nov_box_source"] = src
            _refs["_nov_box_actor"] = actor
        return _refs.get("_nov_box_source"), _refs.get("_nov_box_actor")

    def _ensure_nov_corner_pins():
        """Create 8 small sphere actors for box corners; return list of actors."""
        if not _VTK_BOX_AVAILABLE or vtkSphereSource is None:
            return []
        if "_nov_pin_actors" not in _refs:
            pins = []
            for _ in range(8):
                sp = vtkSphereSource()
                sp.SetPhiResolution(12)
                sp.SetThetaResolution(12)
                sp.SetRadius(1.0)
                sp.SetCenter(0, 0, 0)
                mp = vtkPolyDataMapper()
                mp.SetInputConnection(sp.GetOutputPort())
                ac = vtkActor()
                ac.SetMapper(mp)
                ac.GetProperty().SetColor(0.2, 0.9, 0.35)
                ac.GetProperty().SetOpacity(0.95)
                ac.GetProperty().SetAmbient(0.9)
                ac.GetProperty().SetSpecular(0.3)
                pins.append((sp, ac))
            _refs["_nov_pin_actors"] = pins
        return _refs["_nov_pin_actors"]

    def _update_nov_box(center, length: float, width: float, depth: float, visible: bool):
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        ren = streamer.renderer
        src, actor = _ensure_nov_box_actor()
        if not src or not actor:
            return
        if not visible or not center or len(center) < 3 or length <= 0 or width <= 0 or depth <= 0:
            if ren.HasViewProp(actor):
                ren.RemoveActor(actor)
            for _, pin_actor in _ensure_nov_corner_pins():
                if ren.HasViewProp(pin_actor):
                    ren.RemoveActor(pin_actor)
            return
        _, b = _volume_center_bounds(streamer)
        min_ext = min(b[1] - b[0], b[3] - b[2], b[5] - b[4])
        min_side = max(MIN_BOX_HALF * 2, min_ext * 0.02)
        L = max(float(length), min_side)
        W = max(float(width), min_side)
        D = max(float(depth), min_side)
        src.SetCenter(center[0], center[1], center[2])
        src.SetXLength(L)
        src.SetYLength(W)
        src.SetZLength(D)
        src.Update()
        if not ren.HasViewProp(actor):
            ren.AddActor(actor)
        # Update corner pins: position and size
        pins = _ensure_nov_corner_pins()
        if pins:
            pin_radius = max(min(L, W, D) * PIN_RADIUS_FRACTION, 2.0)
            corners = box_corners([center[0], center[1], center[2]], L, W, D)
            for i, (sp_src, pin_actor) in enumerate(pins):
                sp_src.SetRadius(pin_radius)
                sp_src.Update()
                pin_actor.SetPosition(corners[i][0], corners[i][1], corners[i][2])
                if not ren.HasViewProp(pin_actor):
                    ren.AddActor(pin_actor)
        if _refs.get("view"):
            _refs["view"].update()

    def nov_hide_box():
        _update_nov_box(None, 0.0, 0.0, 0.0, False)
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

    def display_to_display_coords(renderer, x: float, y: float):
        """Convert client (x,y) 0-1 to VTK display coords (origin bottom-left)."""
        w, h = renderer.GetSize()
        if w < 1 or h < 1:
            return None, None
        if 0 <= x <= 1 and 0 <= y <= 1:
            dx = x * (w - 1)
            dy = (1.0 - y) * (h - 1)
        else:
            dx = min(max(x, 0), w - 1)
            dy = min(max((1.0 - y) * (h - 1) if 0 <= y <= 1 else h - 1 - y, 0), h - 1)
        return dx, dy

    def display_to_world_xy(renderer, x: float, y: float):
        dx, dy = display_to_display_coords(renderer, x, y)
        if dx is None:
            return None
        pt = _display_to_world(renderer, dx, dy, 0.5)
        return pt

    def pick_corner_pin(renderer, x: float, y: float):
        """Pick at (x,y) client coords; return corner index 0..7 if a pin was hit, else None."""
        if not _VTK_BOX_AVAILABLE or vtkPropPicker is None:
            return None
        dx, dy = display_to_display_coords(renderer, x, y)
        if dx is None:
            return None
        picker = vtkPropPicker()
        picker.Pick(dx, dy, 0.0, renderer)
        picked = picker.GetActor()
        if picked is None:
            return None
        pins = _refs.get("_nov_pin_actors") or []
        for i, (_, pin_actor) in enumerate(pins):
            if picked == pin_actor:
                return i
        return None

    def ray_plane_intersection(renderer, x: float, y: float, plane_origin: Tuple[float, float, float], plane_normal: Tuple[float, float, float]):
        """Intersect ray from camera through (x,y) with plane; return world point or None."""
        dx, dy = display_to_display_coords(renderer, x, y)
        if dx is None:
            return None
        p0 = _display_to_world(renderer, dx, dy, 0.0)
        p1 = _display_to_world(renderer, dx, dy, 1.0)
        ox, oy, oz = p0[0], p0[1], p0[2]
        dx_w = p1[0] - ox
        dy_w = p1[1] - oy
        dz_w = p1[2] - oz
        n = math.sqrt(dx_w * dx_w + dy_w * dy_w + dz_w * dz_w)
        if n < 1e-12:
            return None
        rx, ry, rz = dx_w / n, dy_w / n, dz_w / n
        nx, ny, nz = plane_normal[0], plane_normal[1], plane_normal[2]
        denom = rx * nx + ry * ny + rz * nz
        if abs(denom) < 1e-12:
            return None
        px, py, pz = plane_origin[0], plane_origin[1], plane_origin[2]
        t = ((px - ox) * nx + (py - oy) * ny + (pz - oz) * nz) / denom
        return (ox + t * rx, oy + t * ry, oz + t * rz)

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

    def run_nov_for_box(center: List[float], length: float, width: float, depth: float):
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        comp, active_ch = get_lod(streamer)
        vol_bounds = streamer._volume_bounds_world(comp)
        nch = max(1, len(active_ch))
        circum_r = box_circum_radius(length, width, depth)
        cam_dist = max(CAMERA_DISTANCE_MULTIPLIER * circum_r, circum_r + 1.0)
        candidates = compute_best_views((center[0], center[1], center[2]), circum_r, vol_bounds, nch, camera_distance=cam_dist)
        if not candidates:
            state.nov_candidates = []
            state.nov_panel_visible = True
            if _refs.get("view"):
                _refs["view"].update()
            return
        state.nov_candidates = candidates
        state.nov_panel_visible = True
        state.nov_box_center = list(center)
        state.nov_box_length = length
        state.nov_box_width = width
        state.nov_box_depth = depth
        _update_nov_box(state.nov_box_center, length, width, depth, True)
        apply_cam(candidates[0])
        if _refs.get("view"):
            _refs["view"].update()

    def _volume_center_bounds(streamer):
        comp, _ = get_lod(streamer)
        b = streamer._volume_bounds_world(comp)
        return [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2], b

    def _clear_nov_panel_state():
        state.nov_candidates = []
        state.nov_panel_visible = False

    def nov_toggle():
        """1st press: show box, drag to set size, release = best views. 2nd press: turn off NOV, remove box from scene."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            state.nov_panel_visible = False
            return
        if getattr(state, "nov_panel_visible", False):
            _clear_nov_panel_state()
            state.nov_drawing_box = False
            nov_hide_box()
            return
        _clear_nov_panel_state()
        _update_nov_box(None, 0.0, 0.0, 0.0, False)
        cam = streamer.renderer.GetActiveCamera()
        focal = list(cam.GetFocalPoint())
        cam_pos = cam.GetPosition()
        cam_z = cam_pos[2] if len(cam_pos) >= 3 else 0.0
        disp = getattr(state, "bookmark_display_snapshot", None)
        if disp and isinstance(disp, dict) and disp.get("views"):
            idx = max(0, int(getattr(state, "bookmark_current_view_index", 0)))
            views = disp.get("views") or []
            if idx < len(views):
                c = (views[idx] or {}).get("camera") or {}
                pos = c.get("position")
                if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                    try:
                        cam_z = float(pos[2])
                    except (TypeError, ValueError):
                        pass
        state.nov_drawing_box = True
        state.nov_box_center = focal
        init_r = max(0.05 * abs(cam_z), MIN_BOX_HALF * math.sqrt(3.0))
        init_side = cube_size_from_circum_radius(init_r)
        state.nov_box_length = state.nov_box_width = state.nov_box_depth = init_side
        _update_nov_box(state.nov_box_center, state.nov_box_length, state.nov_box_width, state.nov_box_depth, True)
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
        if not getattr(state, "nov_drawing_box", False):
            return
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        center = getattr(state, "nov_box_center", None)
        if not center or len(center) < 3:
            return
        pin_idx = pick_corner_pin(streamer.renderer, float(x), float(y))
        if pin_idx is None:
            return
        corners = box_corners(center, getattr(state, "nov_box_length", 0), getattr(state, "nov_box_width", 0), getattr(state, "nov_box_depth", 0))
        fixed_corner = corners[7 - pin_idx]
        state.nov_dragging_corner = pin_idx
        _refs["_nov_fixed_corner"] = fixed_corner
        if _refs.get("view"):
            _refs["view"].update()

    def nov_handle_drag(*args):
        x, y = _parse_xy(*args)
        if x is None:
            return
        corner_idx = getattr(state, "nov_dragging_corner", None)
        if corner_idx is None:
            return
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        fixed = _refs.get("_nov_fixed_corner")
        if not fixed or len(fixed) < 3:
            return
        cam = streamer.renderer.GetActiveCamera()
        fp = cam.GetFocalPoint()
        pos = cam.GetPosition()
        vx = fp[0] - pos[0]
        vy = fp[1] - pos[1]
        vz = fp[2] - pos[2]
        n = math.sqrt(vx * vx + vy * vy + vz * vz)
        if n < 1e-12:
            return
        view_normal = (vx / n, vy / n, vz / n)
        # Plane through current moving corner (we use fixed + offset so plane moves with drag)
        center = getattr(state, "nov_box_center", None)
        if not center or len(center) < 3:
            return
        L = getattr(state, "nov_box_length", 0)
        W = getattr(state, "nov_box_width", 0)
        D = getattr(state, "nov_box_depth", 0)
        corners = box_corners(center, L, W, D)
        moving_corner = corners[corner_idx]
        pt = ray_plane_intersection(streamer.renderer, float(x), float(y), moving_corner, view_normal)
        if pt is None:
            return
        # New box = AABB of fixed corner and new point
        min_x = min(fixed[0], pt[0])
        max_x = max(fixed[0], pt[0])
        min_y = min(fixed[1], pt[1])
        max_y = max(fixed[1], pt[1])
        min_z = min(fixed[2], pt[2])
        max_z = max(fixed[2], pt[2])
        _, b = _volume_center_bounds(streamer)
        min_side = max(MIN_BOX_HALF * 2, min(b[1] - b[0], b[3] - b[2], b[5] - b[4]) * 0.02)
        new_L = max(max_x - min_x, min_side)
        new_W = max(max_y - min_y, min_side)
        new_D = max(max_z - min_z, min_side)
        new_cx = (min_x + max_x) / 2.0
        new_cy = (min_y + max_y) / 2.0
        new_cz = (min_z + max_z) / 2.0
        state.nov_box_center = [new_cx, new_cy, new_cz]
        state.nov_box_length = new_L
        state.nov_box_width = new_W
        state.nov_box_depth = new_D
        _update_nov_box(state.nov_box_center, new_L, new_W, new_D, True)
        if _refs.get("view"):
            _refs["view"].update()

    def nov_handle_wheel(*args):
        """Move box center along Z with mouse wheel (delta > 0 forward, < 0 backward)."""
        delta = 1.0
        if args and isinstance(args[0], (int, float)):
            delta = float(args[0])
        if not getattr(state, "nov_drawing_box", False):
            return
        center = getattr(state, "nov_box_center", None)
        if not center or len(center) < 3:
            return
        streamer = _refs.get("streamer")
        if not streamer:
            return
        _, b = _volume_center_bounds(streamer)
        zmin, zmax = b[4], b[5]
        step = (zmax - zmin) * 0.05
        new_z = center[2] + delta * step
        state.nov_box_center = [center[0], center[1], max(zmin, min(zmax, new_z))]
        L = getattr(state, "nov_box_length", 2.0 * MIN_BOX_HALF)
        W = getattr(state, "nov_box_width", 2.0 * MIN_BOX_HALF)
        D = getattr(state, "nov_box_depth", 2.0 * MIN_BOX_HALF)
        _update_nov_box(state.nov_box_center, L, W, D, True)
        if _refs.get("view"):
            _refs["view"].update()

    def nov_handle_release(*args):
        corner_idx = getattr(state, "nov_dragging_corner", None)
        if corner_idx is None:
            if _refs.get("view"):
                _refs["view"].update()
            return
        state.nov_dragging_corner = None
        _refs["_nov_fixed_corner"] = None
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            if _refs.get("view"):
                _refs["view"].update()
            return
        center = getattr(state, "nov_box_center", None)
        L = max(getattr(state, "nov_box_length", 0.0), 2.0 * MIN_BOX_HALF)
        W = max(getattr(state, "nov_box_width", 0.0), 2.0 * MIN_BOX_HALF)
        D = max(getattr(state, "nov_box_depth", 0.0), 2.0 * MIN_BOX_HALF)
        if center and len(center) >= 3:
            run_nov_for_box(center, L, W, D)
        if _refs.get("view"):
            _refs["view"].update()

    def recompute():
        streamer = _refs.get("streamer")
        cands = getattr(state, "nov_candidates", []) or []
        if not streamer or not cands:
            return
        center = getattr(state, "nov_box_center", None)
        L = max(getattr(state, "nov_box_length", 0.0), 2.0 * MIN_BOX_HALF)
        W = max(getattr(state, "nov_box_width", 0.0), 2.0 * MIN_BOX_HALF)
        D = max(getattr(state, "nov_box_depth", 0.0), 2.0 * MIN_BOX_HALF)
        if not center or len(center) < 3:
            center = cands[0]["camera"]["focalPoint"]
        circum_r = box_circum_radius(L, W, D)
        comp, active_ch = get_lod(streamer)
        vol_bounds = streamer._volume_bounds_world(comp)
        nch = max(1, len(active_ch))
        cam_dist = max(CAMERA_DISTANCE_MULTIPLIER * circum_r, circum_r + 1.0)
        candidates = compute_best_views((center[0], center[1], center[2]), circum_r, vol_bounds, nch, camera_distance=cam_dist)
        if not candidates:
            return
        state.nov_candidates = candidates
        if _refs.get("view"):
            _refs["view"].update()

    ctrl.nov_toggle = nov_toggle
    ctrl.nov_handle_click = nov_handle_click
    ctrl.nov_handle_drag = nov_handle_drag
    ctrl.nov_handle_release = nov_handle_release
    ctrl.nov_handle_wheel = nov_handle_wheel
    ctrl.nov_wheel_forward = lambda: nov_handle_wheel(1.0)
    ctrl.nov_wheel_backward = lambda: nov_handle_wheel(-1.0)
    ctrl.nov_hide_box = nov_hide_box
    ctrl.nov_recompute_scores_if_visible = lambda: recompute() if getattr(state, "nov_panel_visible", False) else None
