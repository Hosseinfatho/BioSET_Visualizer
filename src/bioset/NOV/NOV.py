# NOV (Next Best View): lens ROI, camera candidates.

from __future__ import annotations

import math
import threading
import time
from typing import Callable, List, Optional, Tuple

from bioset.streaming.lod import camera_distance_to_focal, choose_component

# VTK for lens overlay
try:
    from vtkmodules.vtkFiltersSources import vtkCubeSource, vtkSphereSource
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkPropPicker
    from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
    _VTK_BOX_AVAILABLE = True
except Exception:
    vtkCubeSource = vtkSphereSource = vtkActor = vtkPolyDataMapper = vtkPropPicker = vtkInteractorStyleTrackballCamera = None
    _VTK_BOX_AVAILABLE = False


def _make_nov_no_right_style():
    """Interactor style that ignores right-button so right-click+drag is used only for NOV corner resize."""
    if vtkInteractorStyleTrackballCamera is None:
        return None
    class _NoRightStyle(vtkInteractorStyleTrackballCamera):
        def OnRightButtonDown(self):
            pass
        def OnRightButtonMove(self):
            pass
        def OnRightButtonUp(self):
            pass
    return _NoRightStyle()

# --- Constants ---
# Step for sampling directions on the sphere (internal; 30° spacing). Score all, sort, take top 10.
_SPHERE_STEP = 30.0
# Smaller mesh = faster ranking; 32 for much faster, 48–64 for balance, 128 for higher quality.
NOV_MESH_SIZE = 32
VISIBILITY_WEIGHT = 0.8
OCCLUSION_WEIGHT = 0.2
MIN_LENS_HALF = 25.0  # fallback when component unknown
MIN_CAMERA_RADIUS = 10.0  # fallback when view_radius tiny
# Camera distance = 5 * lens diameter for main scene / candidate list (overview)
CAMERA_DISTANCE_DIAMETER_MULT = 5.0
# In popup only: camera 1.25x lens diameter so view is closer (main scene unchanged)
POPUP_CAMERA_DISTANCE_DIAMETER_MULT = 1.25
# Minimum angular separation (degrees) between top-10 views so they are distinct
MIN_TOP10_ANGULAR_SEPARATION_DEG = 30.0


def _direction_vector_deg(t: float, p: float) -> Tuple[float, float, float]:
    """Unit direction vector from sphere center for (t, p) in degrees (same convention as _camera_pos_sphere)."""
    th, ph = _rad(t), _rad(p)
    dx = math.sin(th) * math.sin(ph)
    dy = math.cos(th)
    dz = -math.sin(th) * math.cos(ph)
    return (dx, dy, dz)


def _angular_distance_deg(t1: float, p1: float, t2: float, p2: float) -> float:
    """Angle in degrees between two viewing directions (t, p) in degrees."""
    u = _direction_vector_deg(t1, p1)
    v = _direction_vector_deg(t2, p2)
    dot = u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


def nov_min_lens_side_for_comp(comp: int) -> float:
    """Minimum lens (full) side length from LOD component: (comp+1)*3."""
    return float((comp + 1) * 3)


def nov_lens_circum_radius(length: float, width: float, depth: float) -> float:
    """Distance from lens center to corner (used for camera placement)."""
    return 0.5 * math.sqrt(length * length + width * width + depth * depth)


def nov_cube_size_from_circum_radius(radius: float) -> float:
    """For a cube, side length such that circumscribing sphere has given radius."""
    return 2.0 * radius / math.sqrt(3.0) if radius > 1e-9 else 2.0 * MIN_LENS_HALF


def nov_popup_initial_size(length: float, width: float, depth: float, comp: Optional[int]) -> Tuple[int, int]:
    """Initial NOV popup size (width_px, height_px) from lens and LOD. Uses nov_lens_circum_radius and nov_min_lens_side_for_comp."""
    circum_r = nov_lens_circum_radius(length, width, depth)
    min_side = nov_min_lens_side_for_comp(comp) if comp is not None else MIN_LENS_HALF * 2
    # Scale window from lens size; keep within [min, max] like before
    w = max(340, min(640, 300 + int(circum_r * 0.04)))
    h = max(220, min(360, 200 + int(circum_r * 0.03)))
    return (w, h)


# Corner order: 0=(-,-,-), 1=(+,-,-), 2=(-,+,-), 3=(+,+,-), 4=(-,-,+), 5=(+,-,+), 6=(-,+,+), 7=(+,+,+). Opposite of i is 7-i.
def nov_lens_corners(center: Tuple[float, float, float], length: float, width: float, depth: float) -> List[Tuple[float, float, float]]:
    """Return 8 corner positions (x,y,z) of the lens."""
    cx, cy, cz = center[0], center[1], center[2]
    hL, hW, hD = length / 2.0, width / 2.0, depth / 2.0
    return [
        (cx - hL, cy - hW, cz - hD), (cx + hL, cy - hW, cz - hD),
        (cx - hL, cy + hW, cz - hD), (cx + hL, cy + hW, cz - hD),
        (cx - hL, cy - hW, cz + hD), (cx + hL, cy - hW, cz + hD),
        (cx - hL, cy + hW, cz + hD), (cx + hL, cy + hW, cz + hD),
    ]

PIN_RADIUS_FRACTION = 0.05  # pin radius = this fraction of min(L,W,D); small spheres at corners

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
    lens: Tuple[float, float, float, float, float, float],
) -> Tuple[float, float, float, float, float, float]:
    """Intersect two AABBs; return degenerate (0,0,0,0,0,0) if no overlap."""
    x0 = max(vol[0], lens[0])
    x1 = min(vol[1], lens[1])
    y0 = max(vol[2], lens[2])
    y1 = min(vol[3], lens[3])
    z0 = max(vol[4], lens[4])
    z1 = min(vol[5], lens[5])
    if x0 >= x1 or y0 >= y1 or z0 >= z1:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return (x0, x1, y0, y1, z0, z1)

# --- Camera candidates (positions on radius around center) ---
def _camera_pos_sphere(center: Tuple[float, float, float], radius: float, t: float, p: float) -> List[float]:
    """Position on sphere at radius around center (internal spherical coords t, p in degrees)."""
    th, ph = _rad(t), _rad(p)
    dx = radius * math.sin(th) * math.sin(ph)
    dy = radius * math.cos(th)
    dz = -radius * math.sin(th) * math.cos(ph)
    return [center[0] + dx, center[1] + dy, center[2] + dz]

def _view_up_sphere(t: float, p: float) -> List[float]:
    th, ph = _rad(t), _rad(p)
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

# --- View / visibility ---
# Per-object visibility along one ray (occlusion-aware): (ray_origin, ray_dir, bounds) -> List[float] Vis(o)
SampleVisibilityFn = Callable[
    [
        Tuple[float, float, float],
        Tuple[float, float, float],
        Tuple[float, float, float, float, float, float],
    ],
    List[float],
]


def compute_visibility_per_object(
    camera_pos: Tuple[float, float, float],
    center: Tuple[float, float, float],
    view_up: Tuple[float, float, float],
    view_radius: float,
    bounds_world: Tuple[float, float, float, float, float, float],
    num_objects: int,
    sample_visibility_fn: SampleVisibilityFn,
    *,
    mesh_size: int = NOV_MESH_SIZE,
) -> List[float]:
    """Sum per-object visibility over all rays in the view plane.
    Returns list of length num_objects: Vis(o,v) for each object."""
    if num_objects <= 0:
        return []
    normal = _norm3((center[0] - camera_pos[0], center[1] - camera_pos[1], center[2] - camera_pos[2]))
    up = _norm3((view_up[0], view_up[1], view_up[2]))
    u_axis = _norm3(_cross(normal, up))
    v_axis = _norm3(_cross(normal, u_axis))
    corners = _aabb_corners(bounds_world)
    uv = [(_dot((c[0] - center[0], c[1] - center[1], c[2] - center[2]), u_axis), _dot((c[0] - center[0], c[1] - center[1], c[2] - center[2]), v_axis)) for c in corners]
    hull = _hull2(uv)
    if len(hull) < 3:
        return [0.0] * num_objects
    cell = (2.0 * view_radius) / mesh_size
    umin, umax = min(p[0] for p in hull), max(p[0] for p in hull)
    vmin, vmax = min(p[1] for p in hull), max(p[1] for p in hull)
    i0 = max(0, int((umin + view_radius) / (2.0 * view_radius) * mesh_size))
    i1 = min(mesh_size, int((umax + view_radius) / (2.0 * view_radius) * mesh_size) + 1)
    j0 = max(0, int((vmin + view_radius) / (2.0 * view_radius) * mesh_size))
    j1 = min(mesh_size, int((vmax + view_radius) / (2.0 * view_radius) * mesh_size) + 1)
    vis = [0.0] * num_objects
    cam = (camera_pos[0], camera_pos[1], camera_pos[2])
    for i in range(i0, i1):
        for j in range(j0, j1):
            uc = -view_radius + (i + 0.5) * cell
            vc = -view_radius + (j + 0.5) * cell
            if not _in_poly((uc, vc), hull):
                continue
            pt = (
                center[0] + uc * u_axis[0] + vc * v_axis[0],
                center[1] + uc * u_axis[1] + vc * v_axis[1],
                center[2] + uc * u_axis[2] + vc * v_axis[2],
            )
            ray_dir = _norm3((pt[0] - cam[0], pt[1] - cam[1], pt[2] - cam[2]))
            ray_vis = sample_visibility_fn(cam, ray_dir, bounds_world)
            for o in range(min(num_objects, len(ray_vis))):
                vis[o] += ray_vis[o]
    return vis


# --- Top 10 views by maximum entropy (and minimum single-channel dominance) ---
def _sample_directions_on_sphere() -> List[Tuple[float, float]]:
    """Sample directions on the sphere with _SPHERE_STEP spacing. Returns list of (t, p) for internal use."""
    step = _SPHERE_STEP
    out = []
    t = 30.0
    while t <= 150.0:
        p = 0.0
        while p < 360.0:
            out.append((t, p))
            p += step
        t += step
    return out


def compute_top10_views_by_entropy(
    center: Tuple[float, float, float],
    view_radius: float,
    volume_bounds: Tuple[float, float, float, float, float, float],
    channel_ids: List[int],
    *,
    camera_distance: Optional[float] = None,
    sample_visibility_fn: Optional[SampleVisibilityFn] = None,
    presence_thresh: float = 0.05,
    mesh_size: int = NOV_MESH_SIZE,
    top_k: int = 10,
    entropy_weight: float = 1.0,
    min_intensity_weight: float = 0.3,
    eps: float = 1e-12,
) -> List[dict]:
    """Compute top-k views by maximum entropy H(O|v) and minimum max_o p(o|v) (avoid single-channel dominance).
    Views are sorted descending by score. Each view is at least MIN_TOP10_ANGULAR_SEPARATION_DEG (30°) from the others.
    Returns list of dicts with camera (position, focalPoint, viewUp), fixed_index, score_normalized (first=best score)."""
    if not channel_ids or sample_visibility_fn is None:
        return []
    n_objects = len(channel_ids)
    roi_bounds = bounds_intersect(volume_bounds, aabb_from_center_radius(center, view_radius))
    if roi_bounds[1] <= roi_bounds[0] or roi_bounds[3] <= roi_bounds[2] or roi_bounds[5] <= roi_bounds[4]:
        return []
    cam_dist = camera_distance if camera_distance is not None and camera_distance >= 1e-6 else CAMERA_DISTANCE_DIAMETER_MULT * (2.0 * view_radius)
    directions = _sample_directions_on_sphere()
    scored: List[Tuple[float, float, float, List[float], int]] = []  # (score, entropy, max_p, vis, idx)
    for idx, (t, p) in enumerate(directions):
        pos = _camera_pos_sphere(center, cam_dist, t, p)
        vup = _view_up_sphere(t, p)
        vis = compute_visibility_per_object(
            (pos[0], pos[1], pos[2]),
            center,
            (vup[0], vup[1], vup[2]),
            view_radius,
            roi_bounds,
            n_objects,
            sample_visibility_fn,
            mesh_size=mesh_size,
        )
        total = sum(vis) + eps
        pv = [v / total for v in vis]
        H = -sum(p * math.log(p + eps) for p in pv if p > 0)
        max_p = max(pv) if pv else 0.0
        score = entropy_weight * H - min_intensity_weight * max_p
        scored.append((score, H, max_p, vis, idx))
    scored.sort(key=lambda x: x[0], reverse=True)
    # Greedily pick up to top_k views with at least MIN_TOP10_ANGULAR_SEPARATION_DEG between each pair
    selected_dirs: List[Tuple[float, float]] = []
    candidates: List[dict] = []
    for score, H, max_p, vis, idx in scored:
        if len(candidates) >= top_k:
            break
        t, p = directions[idx]
        if all(
            _angular_distance_deg(t, p, td, pd) >= MIN_TOP10_ANGULAR_SEPARATION_DEG
            for (td, pd) in selected_dirs
        ):
            selected_dirs.append((t, p))
            pos = _camera_pos_sphere(center, cam_dist, t, p)
            vup = _view_up_sphere(t, p)
            norm_score = max(0.0, min(1.0, (H + 1.0 - max_p) / 2.0)) if scored else 0.0
            candidates.append({
                "camera": {"position": pos, "focalPoint": list(center), "viewUp": vup},
                "score_raw": score,
                "fixed_index": len(candidates),
                "score_normalized": norm_score,
            })
    return candidates


# --- SVG: sphere mini-map from camera positions ---
def sphere_xy_from_camera_positions(
    center: Tuple[float, float, float],
    positions: List[List[float]],
    r_svg: float = 22,
    cx: float = 28,
    cy: float = 28,
) -> List[Tuple[float, float]]:
    """Compute SVG (x,y) for each camera position. Direction from center to position, projected to circle (XZ plane)."""
    result = []
    eps = 1e-12
    for pos in positions:
        dx = pos[0] - center[0]
        dy = pos[1] - center[1]
        dz = pos[2] - center[2]
        n = math.sqrt(dx * dx + dy * dy + dz * dz)
        if n < eps:
            result.append((cx, cy))
            continue
        dx, dy, dz = dx / n, dy / n, dz / n
        u = dx
        v = -dz
        x = cx + r_svg * u
        y = cy + r_svg * v
        result.append((round(x, 1), round(y, 1)))
    return result


def build_nov_sphere_svg(sphere_xy: list, current_index: int) -> str:
    """Build SVG showing camera positions on a sphere; current_index highlighted."""
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

# --- Callbacks (lens) ---
def register_nov_callbacks(ctrl, state, _refs):
    """Register nov_toggle, nov_update_rect (2D rect), nov_hide_lens. No 3D lens in scene."""
    from bioset.streaming.lod import _display_to_world

    def _nov_cleanup():
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        ren = streamer.renderer
        for vol in getattr(streamer, "volumes", {}).values():
            vol.SetPickable(1)
        rw = getattr(streamer, "render_window", None)
        if rw and _refs.get("_nov_original_style") is not None:
            i = rw.GetInteractor()
            if i:
                i.SetInteractorStyle(_refs["_nov_original_style"])
        for key in ("_nov_lens_actor",):
            actor = _refs.get(key)
            if actor and ren.HasViewProp(actor):
                ren.RemoveActor(actor)
        for _, pa in (_refs.get("_nov_pin_actors") or []):
            if ren.HasViewProp(pa):
                ren.RemoveActor(pa)

    def nov_update_rect(rx: float, ry: float, rw: float, rh: float):
        """Update 2D lens state, convert to 3D box, set clip, refresh main view and popup if open."""
        side = max(0.05, min(1.0, min(rw, rh)))
        cx_2d = rx + rw / 2.0
        cy_2d = ry + rh / 2.0
        rx = max(0.0, min(1.0 - side, cx_2d - side / 2.0))
        ry = max(0.0, min(1.0 - side, cy_2d - side / 2.0))
        state.nov_rect_x, state.nov_rect_y, state.nov_rect_w, state.nov_rect_h = rx, ry, side, side
        result = _apply_rect_to_3d()
        if _refs.get("view"):
            _refs["view"].update()
        if getattr(state, "nov_popup_open", False):
            streamer = _refs.get("streamer")
            if streamer and getattr(streamer, "sync_nov_volumes", None):
                streamer.sync_nov_volumes()
            _point_nov_camera_at_lens_center()
            if getattr(streamer, "nov_render_window", None):
                streamer.nov_render_window.Render()
            if _refs.get("nov_view") and hasattr(_refs["nov_view"], "update"):
                _refs["nov_view"].update()
        return result

    def _point_nov_camera_at_lens_center():
        """Set NOV popup camera to look at current lens center, keeping view direction and distance."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "nov_renderer", None):
            return
        center = getattr(state, "nov_lens_center", None)
        if not center or len(center) < 3:
            return
        ren = streamer.nov_renderer
        cam = ren.GetActiveCamera()
        fp = list(cam.GetFocalPoint())
        pos = list(cam.GetPosition())
        dx = pos[0] - fp[0]
        dy = pos[1] - fp[1]
        dz = pos[2] - fp[2]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist < 1e-12:
            return
        cam.SetFocalPoint(center[0], center[1], center[2])
        cam.SetPosition(
            center[0] + dx,
            center[1] + dy,
            center[2] + dz,
        )
        ren.ResetCameraClippingRange()

    def _apply_rect_to_3d():
        """Convert current 2D rect (state) to 3D lens and set_nov_lens_clip."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return None
        rx = getattr(state, "nov_rect_x", 0.35)
        ry = getattr(state, "nov_rect_y", 0.35)
        rw = getattr(state, "nov_rect_w", 0.3)
        rh = getattr(state, "nov_rect_h", 0.3)
        ren = streamer.renderer
        cam = ren.GetActiveCamera()
        fp = list(cam.GetFocalPoint())
        pos = list(cam.GetPosition())
        vx, vy, vz = fp[0] - pos[0], fp[1] - pos[1], fp[2] - pos[2]
        n = math.sqrt(vx * vx + vy * vy + vz * vz)
        if n < 1e-12:
            return None
        view_normal = (vx / n, vy / n, vz / n)
        corners_norm = [(rx, ry), (rx + rw, ry), (rx + rw, ry + rh), (rx, ry + rh)]
        world_pts = []
        for nx, ny in corners_norm:
            pt = ray_plane_intersection(ren, nx, ny, (fp[0], fp[1], fp[2]), view_normal)
            if pt is None:
                return None
            world_pts.append(pt)
        cx = sum(p[0] for p in world_pts) * 0.25
        cy = sum(p[1] for p in world_pts) * 0.25
        cz = sum(p[2] for p in world_pts) * 0.25
        L = math.sqrt(sum((world_pts[1][i] - world_pts[0][i]) ** 2 for i in range(3)))
        W = math.sqrt(sum((world_pts[3][i] - world_pts[0][i]) ** 2 for i in range(3)))
        D = 2.0 * math.sqrt(L * L + W * W)
        _, b = _volume_center_bounds(streamer)
        comp, _ = get_lod(streamer)
        min_side = max(nov_min_lens_side_for_comp(comp), min(b[1] - b[0], b[3] - b[2], b[5] - b[4]) * 0.02) if comp is not None else 1.0
        side_3d = max(L, W, min_side)
        L = W = side_3d
        D = max(D, min_side)
        center = [cx, cy, cz]
        state.nov_lens_center = list(center)
        state.nov_lens_length, state.nov_lens_width, state.nov_lens_depth = L, W, D
        if getattr(streamer, "set_nov_lens_clip", None):
            streamer.set_nov_lens_clip((center[0], center[1], center[2]), L, W, D)
        return (center, L, W, D)

    MIN_LENS_SIZE = 0.05   # minimum lens size (5% of view)
    MAX_LENS_SIZE = 0.9    # maximum lens size (90% of view)

    def nov_rect_size_step(delta: float):
        """Increase or decrease rect size by step (delta). Keeps center. Min 5%, max 90% of view."""
        rx = getattr(state, "nov_rect_x", 0.35)
        ry = getattr(state, "nov_rect_y", 0.35)
        rw = getattr(state, "nov_rect_w", 0.3)
        rh = getattr(state, "nov_rect_h", 0.3)
        new_w = max(MIN_LENS_SIZE, min(MAX_LENS_SIZE, rw + delta))
        new_h = max(MIN_LENS_SIZE, min(MAX_LENS_SIZE, rh + delta))
        cx = rx + rw / 2.0
        cy = ry + rh / 2.0
        new_x = cx - new_w / 2.0
        new_y = cy - new_h / 2.0
        new_x = max(0.0, min(1.0 - new_w, new_x))
        new_y = max(0.0, min(1.0 - new_h, new_y))
        nov_update_rect(new_x, new_y, new_w, new_h)

    def nov_hide_lens():
        _nov_cleanup()
        streamer = _refs.get("streamer")
        if streamer:
            streamer.clear_nov_lens_clip()
            if getattr(streamer, "clear_nov_view", None):
                streamer.clear_nov_view()
        if _refs.get("view"):
            _refs["view"].update()

    def apply_cam(cam_dict):
        """Apply camera to NOV popup view only (never to main scene). Uses POPUP_CAMERA_DISTANCE_DIAMETER_MULT * lens diameter; main scene keeps 5x for overview."""
        streamer = _refs.get("streamer")
        if not streamer:
            return
        ren = getattr(streamer, "nov_renderer", None)
        if not ren:
            return
        c = cam_dict.get("camera") or cam_dict
        cam = ren.GetActiveCamera()
        fp = c.get("focalPoint")
        pos = c.get("position")
        if fp and len(fp) >= 3:
            cam.SetFocalPoint(fp[0], fp[1], fp[2])
        if pos and len(pos) >= 3 and fp and len(fp) >= 3:
            # Same direction as candidate, but distance = POPUP_CAMERA_DISTANCE_DIAMETER_MULT * lens diameter
            L = getattr(state, "nov_lens_length", 0.0)
            W = getattr(state, "nov_lens_width", 0.0)
            D = getattr(state, "nov_lens_depth", 0.0)
            if L > 0 and W > 0 and D > 0:
                diam = 2.0 * nov_lens_circum_radius(L, W, D)
                popup_dist = POPUP_CAMERA_DISTANCE_DIAMETER_MULT * diam
                dx = pos[0] - fp[0]
                dy = pos[1] - fp[1]
                dz = pos[2] - fp[2]
                n = math.sqrt(dx * dx + dy * dy + dz * dz)
                if n >= 1e-12:
                    scale = popup_dist / n
                    cam.SetPosition(
                        fp[0] + dx * scale,
                        fp[1] + dy * scale,
                        fp[2] + dz * scale,
                    )
                else:
                    cam.SetPosition(pos[0], pos[1], pos[2])
            else:
                cam.SetPosition(pos[0], pos[1], pos[2])
        elif pos and len(pos) >= 3:
            cam.SetPosition(pos[0], pos[1], pos[2])
        if c.get("viewUp") and len(c.get("viewUp", [])) >= 3:
            cam.SetViewUp(c["viewUp"][:3])
        ren.ResetCameraClippingRange()
        if getattr(streamer, "nov_render_window", None):
            streamer.nov_render_window.Render()
        if _refs.get("nov_view"):
            _refs["nov_view"].update()
        if _refs.get("view"):
            _refs["view"].update()
        if getattr(streamer, "nov_render_callback", None):
            try:
                streamer.nov_render_callback()
            except Exception:
                pass

    def _target_camera_from_candidate(cam_dict):
        """Return (position, focal_point, view_up) for the NOV popup camera as apply_cam would set (with popup distance)."""
        c = cam_dict.get("camera") or cam_dict
        fp = c.get("focalPoint")
        pos = c.get("position")
        view_up = list(c.get("viewUp", [0, 1, 0])[:3]) if c.get("viewUp") else [0.0, 1.0, 0.0]
        if not fp or len(fp) < 3:
            return (None, None, view_up)
        fp = list(fp[:3])
        if not pos or len(pos) < 3:
            return (None, fp, view_up)
        pos = list(pos[:3])
        L = getattr(state, "nov_lens_length", 0.0)
        W = getattr(state, "nov_lens_width", 0.0)
        D = getattr(state, "nov_lens_depth", 0.0)
        if L > 0 and W > 0 and D > 0:
            diam = 2.0 * nov_lens_circum_radius(L, W, D)
            popup_dist = POPUP_CAMERA_DISTANCE_DIAMETER_MULT * diam
            dx = pos[0] - fp[0]
            dy = pos[1] - fp[1]
            dz = pos[2] - fp[2]
            n = math.sqrt(dx * dx + dy * dy + dz * dz)
            if n >= 1e-12:
                scale = popup_dist / n
                pos = [
                    fp[0] + dx * scale,
                    fp[1] + dy * scale,
                    fp[2] + dz * scale,
                ]
        return (pos, fp, view_up)

    def _get_current_nov_camera():
        """Return (position, focal_point, view_up) from current NOV renderer camera."""
        streamer = _refs.get("streamer")
        if not streamer:
            return (None, None, None)
        ren = getattr(streamer, "nov_renderer", None)
        if not ren:
            return (None, None, None)
        cam = ren.GetActiveCamera()
        pos = list(cam.GetPosition())
        fp = list(cam.GetFocalPoint())
        vup = list(cam.GetViewUp())
        return (pos, fp, vup)

    def _apply_camera_state(pos, fp, view_up):
        """Set NOV camera to given position, focal point, view up; render and update view."""
        streamer = _refs.get("streamer")
        if not streamer:
            return
        ren = getattr(streamer, "nov_renderer", None)
        if not ren or pos is None or fp is None:
            return
        cam = ren.GetActiveCamera()
        cam.SetFocalPoint(fp[0], fp[1], fp[2])
        cam.SetPosition(pos[0], pos[1], pos[2])
        if view_up and len(view_up) >= 3:
            cam.SetViewUp(view_up[0], view_up[1], view_up[2])
        ren.ResetCameraClippingRange()
        if getattr(streamer, "nov_render_window", None):
            streamer.nov_render_window.Render()
        if _refs.get("nov_view"):
            try:
                _refs["nov_view"].update()
            except Exception:
                pass
        if getattr(streamer, "nov_render_callback", None):
            try:
                streamer.nov_render_callback()
            except Exception:
                pass

    def _lerp(a, b, t):
        return a + (b - a) * t

    def _norm(v):
        n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        if n < 1e-12:
            return (0.0, 0.0, 0.0), 0.0
        return (v[0] / n, v[1] / n, v[2] / n), n

    def _slerp(v0, v1, t):
        """Spherical linear interpolation between unit vectors v0 and v1. Returns unit vector."""
        dot = v0[0] * v1[0] + v0[1] * v1[1] + v0[2] * v1[2]
        if dot > 0.9999:
            return [_lerp(v0[j], v1[j], t) for j in range(3)]
        if dot < -0.9999:
            return [_lerp(v0[j], (-v1[0], -v1[1], -v1[2])[j], t) for j in range(3)]
        if dot < -1.0:
            dot = -1.0
        elif dot > 1.0:
            dot = 1.0
        omega = math.acos(dot)
        sin_omega = math.sin(omega)
        if sin_omega < 1e-12:
            return [_lerp(v0[j], v1[j], t) for j in range(3)]
        a = math.sin((1.0 - t) * omega) / sin_omega
        b = math.sin(t * omega) / sin_omega
        return [a * v0[j] + b * v1[j] for j in range(3)]

    def _compute_slerp_frame(anim, t):
        """Compute (pos, fp, vup) for parameter t in [0,1] from precomputed anim dict (slerp rotation)."""
        start_fp = anim["start_fp"]
        end_fp = anim["end_fp"]
        d0, r0 = anim["d0"], anim["r0"]
        d1, r1 = anim["d1"], anim["r1"]
        direction = _slerp(d0, d1, t)
        dist = _lerp(r0, r1, t)
        fp = [_lerp(start_fp[j], end_fp[j], t) for j in range(3)]
        pos = [fp[j] + dist * direction[j] for j in range(3)]
        vup = _slerp(anim["vup0"], anim["vup1"], t)
        n = math.sqrt(vup[0] ** 2 + vup[1] ** 2 + vup[2] ** 2)
        if n >= 1e-12:
            vup = [vup[0] / n, vup[1] / n, vup[2] / n]
        return pos, fp, vup

    def _nov_animation_tick():
        """Called from app async loop every ~40ms. Advances one step of NOV camera transition and pushes frame to client."""
        anim = _refs.get("_nov_anim")
        if not anim:
            return
        step = anim["step"]
        total = anim["total"]
        if step >= total:
            target_cand = anim["target_cand"]
            try:
                _refs.pop("_nov_anim", None)
            except Exception:
                pass
            apply_cam(target_cand)
            if _refs.get("view"):
                _refs["view"].update()
            return
        t = (step + 1) / total
        pos, fp, vup = _compute_slerp_frame(anim, t)
        _apply_camera_state(pos, fp, vup)
        anim["step"] = step + 1

    def switch_smooth(step):
        """Start smooth camera rotation to next/previous candidate. Frames are driven by async loop (nov_animation_tick) so client sees each step."""
        cands = getattr(state, "nov_candidates", []) or []
        if not cands:
            return
        current_idx = getattr(state, "nov_current_index", 0)
        target_idx = (current_idx + step) % len(cands)
        if target_idx == current_idx:
            return
        target_cand = cands[target_idx]
        start_pos, start_fp, start_vup = _get_current_nov_camera()
        end_pos, end_fp, end_vup = _target_camera_from_candidate(target_cand)
        if end_pos is None or end_fp is None:
            switch(step)
            return
        if start_pos is None or start_fp is None:
            apply_cam(target_cand)
            state.nov_current_index = target_idx
            state.nov_sphere_svg = build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), target_idx)
            state.nov_score_display = target_cand["score_normalized"]
            state.nov_view_index_display = f"{target_idx + 1}/{len(cands)}"
            if _refs.get("view"):
                _refs["view"].update()
            return
        if _refs.get("_nov_anim"):
            return
        state.nov_current_index = target_idx
        state.nov_sphere_svg = build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), target_idx)
        state.nov_score_display = target_cand["score_normalized"]
        state.nov_view_index_display = f"{target_idx + 1}/{len(cands)}"
        end_vup = end_vup or [0.0, 1.0, 0.0]
        start_vup = start_vup or [0.0, 1.0, 0.0]
        d0, r0 = _norm([start_pos[j] - start_fp[j] for j in range(3)])
        d1, r1 = _norm([end_pos[j] - end_fp[j] for j in range(3)])
        if r0 < 1e-12:
            r0 = r1 if r1 >= 1e-12 else 1.0
        if r1 < 1e-12:
            r1 = r0
        vup0, _ = _norm(start_vup)
        vup1, _ = _norm(end_vup)
        _refs["_nov_anim"] = {
            "step": 0,
            "total": 120,
            "start_fp": start_fp,
            "end_fp": end_fp,
            "d0": d0,
            "r0": r0,
            "d1": d1,
            "r1": r1,
            "vup0": vup0,
            "vup1": vup1,
            "target_cand": target_cand,
        }

    def update_nov_scale_bar():
        """Compute scale bar (µm per pixel) from NOV camera and set state for UI. Uses voxel spacing (0.14, 0.14, 0.28) µm."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "nov_renderer", None) or not getattr(streamer, "nov_render_window", None):
            return
        ren = streamer.nov_renderer
        rw = streamer.nov_render_window
        cam = ren.GetActiveCamera()
        w, h = rw.GetSize()
        if w < 10 or h < 10:
            state.nov_scale_bar_label = ""
            state.nov_scale_bar_width_px = 0
            return
        pos = cam.GetPosition()
        fp = cam.GetFocalPoint()
        dist = math.sqrt(
            (pos[0] - fp[0]) ** 2 + (pos[1] - fp[1]) ** 2 + (pos[2] - fp[2]) ** 2
        )
        if dist < 1e-9:
            state.nov_scale_bar_label = ""
            state.nov_scale_bar_width_px = 0
            return
        view_angle_deg = cam.GetViewAngle()
        view_angle_rad = math.radians(view_angle_deg)
        world_height_um = 2.0 * dist * math.tan(view_angle_rad / 2.0)
        um_per_pixel = world_height_um / float(h)
        if um_per_pixel <= 0:
            state.nov_scale_bar_label = ""
            state.nov_scale_bar_width_px = 0
            return
        target_px = 90
        raw_um = target_px * um_per_pixel
        nice = (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500)
        scale_um = min(nice, key=lambda x: abs(x - raw_um))
        if raw_um < 0.25:
            scale_um = 0.5
        elif raw_um > 400:
            scale_um = 500
        bar_width_px = scale_um / um_per_pixel
        state.nov_scale_bar_label = (
            f"{scale_um:.0f} µm" if scale_um >= 1 else f"{scale_um} µm"
        )
        state.nov_scale_bar_width_px = int(round(min(bar_width_px, 400)))

    _s = _refs.get("streamer")
    if _s is not None and getattr(_s, "nov_render_callback", None) is None:
        _s.nov_render_callback = update_nov_scale_bar
    ctrl.update_nov_scale_bar = update_nov_scale_bar

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

    def nov_initial_rect_from_radius_and_comp(s):
        """Initial 2D lens (x, y, w, h) in 0-1. Size from nov_min_lens_side_for_comp(comp); displayed as rectangle."""
        comp, _ = get_lod(s)
        b = s._volume_bounds_world(comp)
        L = b[1] - b[0]
        W = b[3] - b[2]
        D = b[5] - b[4]
        diagonal = 2.0 * nov_lens_circum_radius(L, W, D)
        if diagonal < 1e-9:
            return (0.35, 0.35, 0.3, 0.3)
        min_side = nov_min_lens_side_for_comp(comp) if comp is not None else (MIN_LENS_HALF * 2.0)
        side = min_side / diagonal
        side = max(0.15, min(0.5, side))
        cx, cy = 0.5, 0.5
        rx = max(0.0, min(1.0 - side, cx - side / 2.0))
        ry = max(0.0, min(1.0 - side, cy - side / 2.0))
        return (rx, ry, side, side)

    def run_nov_for_lens(center: List[float], length: float, width: float, depth: float, compute_entropy: bool = True):
        """Show NOV panel + lens + clip + active channels. If compute_entropy is False, only show popup (no optimal view calc).
        If True, also compute optimal view by entropy (slow). Use Set button to run this after opening."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            return
        comp, active_ch = get_lod(streamer)
        active_set = set(active_ch) if active_ch else set()
        channels = getattr(state, "channels", []) or []
        state.nov_active_channel_items = [c for c in channels if c.get("id") in active_set]
        vol_bounds = streamer._volume_bounds_world(comp)
        active_list = sorted(active_ch) if active_ch else []
        circum_r = nov_lens_circum_radius(length, width, depth)
        cam_dist = CAMERA_DISTANCE_DIAMETER_MULT * (2.0 * circum_r)
        roi_bounds = bounds_intersect(vol_bounds, aabb_from_center_radius(center, circum_r))
        state.nov_panel_visible = True
        wp, hp = nov_popup_initial_size(length, width, depth, comp)
        state.nov_popup_width_px = wp
        state.nov_popup_height_px = hp
        state.nov_lens_center = list(center)
        state.nov_lens_length = length
        state.nov_lens_width = width
        state.nov_lens_depth = depth
        if streamer:
            streamer.set_nov_lens_clip((center[0], center[1], center[2]), length, width, depth)
        if not compute_entropy:
            state.nov_candidates = []
            state.nov_has_results = False
            state.nov_sphere_xy = []
            state.nov_sphere_svg = ""
            state.nov_view_index_display = "—"
            state.nov_score_display = 0.0
            # All active channels shown initially (same as main scene); user can deselect to hide in window only
            state.nov_selected_channels = list(active_set) if active_set else []
            if getattr(streamer, "sync_nov_volumes", None):
                streamer.sync_nov_volumes()
            if getattr(streamer, "set_nov_channel_visibility", None):
                streamer.set_nov_channel_visibility(state.nov_selected_channels)
            if _refs.get("view"):
                _refs["view"].update()
            return
        if not active_list:
            state.nov_candidates = []
            state.nov_has_results = False
            state.nov_sphere_xy = []
            state.nov_sphere_svg = ""
            state.nov_view_index_display = ""
            state.nov_score_display = 0.0
            if _refs.get("view"):
                _refs["view"].update()
            return
        try:
            if getattr(streamer, "prepare_nov_scoring", None):
                streamer.prepare_nov_scoring(roi_bounds, comp, channel_ids=active_list)
        except Exception:
            pass
        presence_thresh = 0.05
        def sample_vis(ray_origin, ray_dir, bounds):
            if getattr(streamer, "sample_nov_ray_channels_occlusion", None):
                return streamer.sample_nov_ray_channels_occlusion(
                    ray_origin, ray_dir, bounds,
                    channel_ids=active_list,
                    presence_thresh=presence_thresh,
                )
            return [0.0] * len(active_list)
        candidates = compute_top10_views_by_entropy(
            (center[0], center[1], center[2]),
            circum_r,
            vol_bounds,
            active_list,
            camera_distance=cam_dist,
            sample_visibility_fn=sample_vis,
            presence_thresh=presence_thresh,
            mesh_size=NOV_MESH_SIZE,
            top_k=10,
        )
        if not candidates:
            state.nov_candidates = []
            state.nov_has_results = False
            state.nov_sphere_xy = []
            state.nov_sphere_svg = ""
            state.nov_view_index_display = ""
            state.nov_score_display = 0.0
            if _refs.get("view"):
                _refs["view"].update()
            return
        center_t = (center[0], center[1], center[2])
        sphere_xy = sphere_xy_from_camera_positions(center_t, [c["camera"]["position"] for c in candidates])
        state.nov_candidates = candidates
        state.nov_has_results = True
        state.nov_current_index = 0
        state.nov_sphere_xy = sphere_xy
        state.nov_sphere_svg = build_nov_sphere_svg(sphere_xy, 0)
        state.nov_score_display = candidates[0]["score_normalized"]
        state.nov_view_index_display = f"1/{len(candidates)}"
        state.nov_popup_open = False
        apply_cam(candidates[0])
        if _refs.get("view"):
            _refs["view"].update()

    def _volume_center_bounds(streamer):
        comp, _ = get_lod(streamer)
        b = streamer._volume_bounds_world(comp)
        return [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2], b

    def _clear_nov_panel_state():
        state.nov_candidates = []
        state.nov_has_results = False
        state.nov_current_index = 0
        state.nov_view_index_display = ""
        state.nov_score_display = 0.0
        state.nov_sphere_svg = ""
        state.nov_sphere_xy = []
        state.nov_panel_visible = False
        state.nov_popup_open = False
        state.nov_dragging_lens_center = False

    def nov_toggle():
        """1st press: show lens, drag to set size, release = best views. 2nd press: turn off NOV, remove lens from scene."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            state.nov_panel_visible = False
            return
        if getattr(state, "nov_panel_visible", False):
            _clear_nov_panel_state()
            state.nov_drawing_lens = False
            state.nov_show_rect = False
            nov_hide_lens()
            return
        _clear_nov_panel_state()
        _nov_cleanup()
        state.nov_show_rect = True
        state.nov_drawing_lens = True
        rx, ry, rw, rh = nov_initial_rect_from_radius_and_comp(streamer)
        result = nov_update_rect(rx, ry, rw, rh)
        if not result:
            state.nov_show_rect = False
            if _refs.get("view"):
                _refs["view"].update()
            return
        center, L, W, D = result
        run_nov_for_lens(center, L, W, D, compute_entropy=False)
        if _refs.get("view"):
            _refs["view"].update()

    def recompute():
        streamer = _refs.get("streamer")
        cands = getattr(state, "nov_candidates", []) or []
        if not streamer or not cands:
            return
        comp, active_ch = get_lod(streamer)
        active_list = sorted(active_ch) if active_ch else []
        selected = list(getattr(state, "nov_selected_channels", []) or [])
        if not selected:
            selected = active_list
        selected = [c for c in selected if c in active_list]
        if not selected:
            selected = active_list
        if not selected:
            return
        min_side = nov_min_lens_side_for_comp(comp)
        center = getattr(state, "nov_lens_center", None)
        if not center or len(center) < 3:
            center = cands[0]["camera"]["focalPoint"]
        L = max(getattr(state, "nov_lens_length", 0.0), min_side)
        W = max(getattr(state, "nov_lens_width", 0.0), min_side)
        D = max(getattr(state, "nov_lens_depth", 0.0), min_side)
        circum_r = nov_lens_circum_radius(L, W, D)
        vol_bounds = streamer._volume_bounds_world(comp)
        cam_dist = CAMERA_DISTANCE_DIAMETER_MULT * (2.0 * circum_r)
        roi_bounds = bounds_intersect(vol_bounds, aabb_from_center_radius(center, circum_r))
        try:
            if getattr(streamer, "prepare_nov_scoring", None):
                streamer.prepare_nov_scoring(roi_bounds, comp, channel_ids=selected)
        except Exception:
            pass
        presence_thresh = 0.05
        def sample_vis(ray_origin, ray_dir, bounds):
            if getattr(streamer, "sample_nov_ray_channels_occlusion", None):
                return streamer.sample_nov_ray_channels_occlusion(
                    ray_origin, ray_dir, bounds,
                    channel_ids=selected,
                    presence_thresh=presence_thresh,
                )
            return [0.0] * len(selected)
        candidates = compute_top10_views_by_entropy(
            (center[0], center[1], center[2]),
            circum_r,
            vol_bounds,
            selected,
            camera_distance=cam_dist,
            sample_visibility_fn=sample_vis,
            presence_thresh=presence_thresh,
            mesh_size=NOV_MESH_SIZE,
            top_k=10,
        )
        if not candidates:
            return
        center_t = (center[0], center[1], center[2])
        sphere_xy = sphere_xy_from_camera_positions(center_t, [c["camera"]["position"] for c in candidates])
        state.nov_candidates = candidates
        state.nov_current_index = 0
        state.nov_sphere_xy = sphere_xy
        state.nov_sphere_svg = build_nov_sphere_svg(sphere_xy, 0)
        state.nov_score_display = candidates[0]["score_normalized"]
        state.nov_view_index_display = f"1/{len(candidates)}"
        apply_cam(candidates[0])
        if _refs.get("view"):
            _refs["view"].update()

    def switch(step):
        cands = getattr(state, "nov_candidates", []) or []
        if not cands:
            return
        idx = (getattr(state, "nov_current_index", 0) + step) % len(cands)
        state.nov_current_index = idx
        state.nov_sphere_svg = build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), idx)
        state.nov_score_display = cands[idx]["score_normalized"]
        state.nov_view_index_display = f"{idx + 1}/{len(cands)}"
        apply_cam(cands[idx])
        if _refs.get("view"):
            _refs["view"].update()

    def nov_set():
        """Use current rectangle position (2D) → sync to 3D box → read data inside → find best camera views → open popup.
        Flow: 1) User sets rectangle (drag). 2) On Set: apply 2D rect to 3D, read data in that box, compute best views."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None):
            state.nov_popup_open = True
            if _refs.get("view"):
                _refs["view"].update()
            return
        # Ensure 3D lens is from current 2D rectangle position (user may have moved/sized it before pressing Set)
        result = _apply_rect_to_3d()
        if not result:
            state.nov_popup_open = True
            if _refs.get("view"):
                _refs["view"].update()
            return
        center = list(state.nov_lens_center) if getattr(state, "nov_lens_center", None) else None
        L = getattr(state, "nov_lens_length", 0.0)
        W = getattr(state, "nov_lens_width", 0.0)
        D = getattr(state, "nov_lens_depth", 0.0)
        if not center or len(center) < 3 or L <= 0 or W <= 0 or D <= 0:
            state.nov_popup_open = True
            if _refs.get("view"):
                _refs["view"].update()
            return
        comp, active_ch = get_lod(streamer)
        active_list = sorted(active_ch) if active_ch else []
        selected = list(getattr(state, "nov_selected_channels", []) or [])
        if not selected:
            selected = active_list
        selected = [c for c in selected if c in active_list]
        if not selected:
            selected = active_list[:1]
        if not selected:
            state.nov_popup_open = True
            if getattr(streamer, "set_nov_lens_clip", None):
                streamer.set_nov_lens_clip((center[0], center[1], center[2]), L, W, D)
            if getattr(streamer, "sync_nov_volumes", None):
                streamer.sync_nov_volumes()
            if _refs.get("view"):
                _refs["view"].update()
            return
        circum_r = nov_lens_circum_radius(L, W, D)
        vol_bounds = streamer._volume_bounds_world(comp)
        cam_dist = CAMERA_DISTANCE_DIAMETER_MULT * (2.0 * circum_r)
        roi_bounds = bounds_intersect(vol_bounds, aabb_from_center_radius(center, circum_r))
        try:
            if getattr(streamer, "prepare_nov_scoring", None):
                streamer.prepare_nov_scoring(roi_bounds, comp, channel_ids=selected)
        except Exception:
            pass
        presence_thresh = 0.05
        def sample_vis(ray_origin, ray_dir, bounds):
            if getattr(streamer, "sample_nov_ray_channels_occlusion", None):
                return streamer.sample_nov_ray_channels_occlusion(
                    ray_origin, ray_dir, bounds,
                    channel_ids=selected,
                    presence_thresh=presence_thresh,
                )
            return [0.0] * len(selected)
        candidates = compute_top10_views_by_entropy(
            (center[0], center[1], center[2]),
            circum_r,
            vol_bounds,
            selected,
            camera_distance=cam_dist,
            sample_visibility_fn=sample_vis,
            presence_thresh=presence_thresh,
            mesh_size=NOV_MESH_SIZE,
            top_k=10,
        )
        if candidates:
            center_t = (center[0], center[1], center[2])
            sphere_xy = sphere_xy_from_camera_positions(center_t, [c["camera"]["position"] for c in candidates])
            state.nov_candidates = candidates
            state.nov_has_results = True
            state.nov_current_index = 0
            state.nov_sphere_xy = sphere_xy
            state.nov_sphere_svg = build_nov_sphere_svg(sphere_xy, 0)
            state.nov_score_display = candidates[0]["score_normalized"]
            state.nov_view_index_display = f"1/{len(candidates)}"
            apply_cam(candidates[0])
        if getattr(streamer, "set_nov_lens_clip", None):
            streamer.set_nov_lens_clip((center[0], center[1], center[2]), L, W, D)
        if getattr(streamer, "sync_nov_volumes", None):
            streamer.sync_nov_volumes()
        state.nov_popup_open = True
        if _refs.get("nov_view"):
            _refs["nov_view"].update()
        if _refs.get("view"):
            _refs["view"].update()
        def _delayed_nov_resize():
            if _refs.get("nov_view"):
                try:
                    _refs["nov_view"].update()
                except Exception:
                    pass
        threading.Timer(0.35, _delayed_nov_resize).start()

    def nov_reset():
        """Reset inside popup only: clear best-view results so user can move lens and press Set again. Keep popup and lens open."""
        state.nov_candidates = []
        state.nov_has_results = False
        state.nov_current_index = 0
        state.nov_view_index_display = "—"
        state.nov_score_display = 0.0
        state.nov_sphere_svg = ""
        state.nov_sphere_xy = []
        # Point NOV popup camera at lens center so view is reset; lens content stays
        _point_nov_camera_at_lens_center()
        streamer = _refs.get("streamer")
        if streamer and getattr(streamer, "sync_nov_volumes", None):
            streamer.sync_nov_volumes()
        if _refs.get("nov_view"):
            _refs["nov_view"].update()
        if _refs.get("view"):
            _refs["view"].update()

    def nov_refresh_lens_display():
        """Refresh NOV 2D rect overlay (e.g. after restoring bookmark)."""
        if _refs.get("view"):
            _refs["view"].update()

    def nov_toggle_channel(channel_id=None):
        """Toggle channel_id in nov_selected_channels (for checkbox list). If channel_id is None, use state.nov_clicked_channel_id."""
        cid = channel_id if channel_id is not None else getattr(state, "nov_clicked_channel_id", None)
        if cid is None:
            return
        sel = list(getattr(state, "nov_selected_channels", []) or [])
        if cid in sel:
            sel = [x for x in sel if x != cid]
        else:
            sel = list(sel) + [cid]
        state.nov_selected_channels = sel
        if _refs.get("view"):
            _refs["view"].update()

    def nov_update_visibility():
        """Update NOV window to show only channels in nov_selected_channels (hide deselected)."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "set_nov_channel_visibility", None):
            return
        selected = list(getattr(state, "nov_selected_channels", []) or [])
        streamer.set_nov_channel_visibility(selected)
        if _refs.get("view"):
            _refs["view"].update()

    ctrl.nov_toggle = nov_toggle
    ctrl.nov_toggle_channel = nov_toggle_channel
    ctrl.nov_set = nov_set
    ctrl.nov_reset = nov_reset
    ctrl.nov_refresh_lens_display = nov_refresh_lens_display
    ctrl.nov_update_rect = nov_update_rect
    ctrl.nov_rect_size_step = nov_rect_size_step
    ctrl.nov_rect_size_plus = lambda: nov_rect_size_step(0.03)
    ctrl.nov_rect_size_minus = lambda: nov_rect_size_step(-0.03)

    ctrl.nov_hide_lens = nov_hide_lens
    ctrl.nov_prev = lambda: switch_smooth(-1)
    ctrl.nov_next = lambda: switch_smooth(1)
    ctrl.nov_animation_tick = _nov_animation_tick
    ctrl.nov_recompute_scores_if_visible = lambda: recompute() if getattr(state, "nov_panel_visible", False) else None
    ctrl.nov_update_visibility = nov_update_visibility
