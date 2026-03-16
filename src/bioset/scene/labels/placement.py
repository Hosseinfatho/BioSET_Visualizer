"""
Label rendering with zoom-based heuristic type selection.

Adapted from cycif_mesh_labelling/new/labeling.py for BioSET:
- Imports from .settings and .label_regions instead of settings/regions
- label_lookup_fn passed as parameter instead of imported from labels.py
- Module-level locator/dilation caches work the same way (keyed by marker_name)
"""

from __future__ import annotations

import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk
from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
from vtkmodules.vtkFiltersSources import vtkSphereSource, vtkLineSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkFollower, vtkPolyDataMapper

from .settings import label_config as config
from .regions import Region


# ==========================================================================
# Helpers
# ==========================================================================

def _norm(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def _ray_cast(locator, start, end):
    t = vtk.mutable(0.0)
    hit = [0.0, 0.0, 0.0]
    pcoords = [0.0, 0.0, 0.0]
    sub_id = vtk.mutable(0)
    cell_id = vtk.mutable(0)
    if locator.IntersectWithLine(start.tolist(), end.tolist(), 1e-4,
                                  t, hit, pcoords, sub_id, cell_id):
        return int(cell_id.get()), np.array(hit, dtype=np.float64)
    return None, None


# ==========================================================================
# Surface snapping
# ==========================================================================

_channel_locators = {}  # marker_name -> vtkCellLocator on original mesh


def _ensure_channel_locator(channel):
    name = channel["marker_name"]
    if name not in _channel_locators:
        loc = vtk.vtkCellLocator()
        loc.SetDataSet(channel["mesh"])
        loc.BuildLocator()
        _channel_locators[name] = loc
    return _channel_locators[name]


def _snap_to_surface(region, channels, cam_pos, cam_fwd):
    """Ray-cast from camera through region centroid. Returns (hit_point, normal) or (None, None)."""
    centroid = region.centroid
    target_channel = region.channels[0] if region.is_composite else region.channel
    ch = next((c for c in channels if c["marker_name"] == target_channel), None)
    if ch is None:
        return None, None

    locator = _ensure_channel_locator(ch)
    normals_arr = ch["cell_normals"]

    if np.dot(centroid - cam_pos, cam_fwd) <= 0:
        return None, None

    direction = centroid - cam_pos
    dist = np.linalg.norm(direction)
    if dist < 1e-9:
        return None, None
    ray_end = cam_pos + (direction / dist) * dist * 2.0

    cell_id, hit_point = _ray_cast(locator, cam_pos, ray_end)
    if cell_id is None:
        return None, None

    # Region ownership check
    b = region.bounds
    diag = np.sqrt((b[1]-b[0])**2 + (b[3]-b[2])**2 + (b[5]-b[4])**2)
    pad = max(diag * 0.1, 0.2)
    if not (b[0] - pad <= hit_point[0] <= b[1] + pad and
            b[2] - pad <= hit_point[1] <= b[3] + pad and
            b[4] - pad <= hit_point[2] <= b[5] + pad):
        return None, None

    normal = normals_arr[cell_id].copy()
    nl = np.linalg.norm(normal)
    if nl > 1e-9:
        normal /= nl
    if np.dot(normal, cam_pos - hit_point) < 0:
        normal = -normal

    return hit_point, normal


# ==========================================================================
# Text template cache
# ==========================================================================

_text_cache = {}


def get_text_mesh(text):
    """Return (polydata, bounds) for a label string. Cached."""
    if text not in _text_cache:
        vt = vtk.vtkVectorText()
        vt.SetText(text)
        vt.Update()
        pd = vtk.vtkPolyData()
        pd.DeepCopy(vt.GetOutput())
        _text_cache[text] = (pd, pd.GetBounds())
    return _text_cache[text]


# ==========================================================================
# Preprocessing: dilated proxy meshes for surface walk
# ==========================================================================

_dilated_cache = {}  # marker_name -> (dilated_mesh, cell_normals, locator)


def preprocess_channel(channel):
    """Build dilated proxy mesh + locator for SURFACE label walks. Cached per marker."""
    marker = channel["marker_name"]
    if marker in _dilated_cache:
        return _dilated_cache[marker]

    mesh = channel["mesh"]
    amount = config["DILATION_AMOUNT"]
    smooth = config.get("SMOOTH_ITERATIONS", 50)

    print(f"[label_placement] Dilating {marker} (amount={amount}, smooth={smooth})...")

    nf = vtkPolyDataNormals()
    nf.SetInputData(mesh)
    nf.ComputePointNormalsOn()
    nf.ComputeCellNormalsOn()
    nf.ConsistencyOn()
    nf.AutoOrientNormalsOn()
    nf.SplittingOff()
    nf.Update()

    normed = nf.GetOutput()
    normed.GetPointData().SetActiveVectors("Normals")

    warper = vtk.vtkWarpVector()
    warper.SetInputData(normed)
    warper.SetScaleFactor(amount)
    warper.Update()

    if smooth > 0:
        smoother = vtk.vtkSmoothPolyDataFilter()
        smoother.SetInputConnection(warper.GetOutputPort())
        smoother.SetNumberOfIterations(smooth)
        smoother.SetRelaxationFactor(0.1)
        smoother.FeatureEdgeSmoothingOff()
        smoother.BoundarySmoothingOn()
        smoother.Update()
        dilated = vtk.vtkPolyData()
        dilated.DeepCopy(smoother.GetOutput())
    else:
        dilated = vtk.vtkPolyData()
        dilated.DeepCopy(warper.GetOutput())

    nf2 = vtkPolyDataNormals()
    nf2.SetInputData(dilated)
    nf2.ComputeCellNormalsOn()
    nf2.ComputePointNormalsOff()
    nf2.ConsistencyOn()
    nf2.AutoOrientNormalsOn()
    nf2.SplittingOff()
    nf2.Update()
    cell_normals = vtk_to_numpy(nf2.GetOutput().GetCellData().GetNormals()).copy()

    loc = vtk.vtkCellLocator()
    loc.SetDataSet(dilated)
    loc.BuildLocator()

    print(f"[label_placement] Dilated {marker}: {dilated.GetNumberOfPoints()} pts")

    result = (dilated, cell_normals, loc)
    _dilated_cache[marker] = result
    return result


def clear_channel_caches(marker_name=None):
    """Clear cached locators and dilated meshes. Call when meshes are reloaded."""
    if marker_name is None:
        _channel_locators.clear()
        _dilated_cache.clear()
    else:
        _channel_locators.pop(marker_name, None)
        _dilated_cache.pop(marker_name, None)


# ==========================================================================
# Visibility culling
# ==========================================================================

def get_visible_regions(regions, cam_pos, cam_fwd, max_labels):
    """Return front-facing regions sorted by distance, capped at max_labels."""
    visible = []
    for r in regions:
        if r.label_text is None:
            continue
        if np.dot(r.centroid - cam_pos, cam_fwd) <= 0:
            continue
        dist = np.linalg.norm(r.centroid - cam_pos)
        visible.append((dist, r))
    visible.sort(key=lambda x: x[0])
    return [r for _, r in visible[:max_labels]]


# ==========================================================================
# Zoom heuristic
# ==========================================================================

def pick_label_type(region, camera_distance):
    far = config["FAR_THRESHOLD"]
    mid = config["MID_THRESHOLD"]
    if camera_distance > far:
        return "BILLBOARD"
    elif camera_distance > mid:
        return "FLAGPOLE"
    else:
        return "SURFACE"


# ==========================================================================
# BILLBOARD renderer
# ==========================================================================

def render_billboard(region, anchor_pos, anchor_normal, camera, renderer):
    text_pd, _ = get_text_mesh(region.label_text)
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    to_camera = _norm(cam_pos - anchor_pos)
    pos = anchor_pos + to_camera * config["BILLBOARD_OFFSET"]
    s = config["BILLBOARD_TEXT_SCALE"]
    r, g, b = config["BILLBOARD_COLOR"]

    f = vtkFollower()
    m = vtkPolyDataMapper()
    m.SetInputData(text_pd)
    f.SetMapper(m)
    f.SetCamera(camera)
    f.SetScale(s, s, s)
    f.SetPosition(pos.tolist())
    f.GetProperty().SetColor(r, g, b)
    f.GetProperty().LightingOff()
    f.SetPickable(False)
    renderer.AddActor(f)
    return [f]


# ==========================================================================
# FLAGPOLE renderer
# ==========================================================================

def render_flagpole(region, anchor_pos, anchor_normal, camera, renderer):
    text_pd, _ = get_text_mesh(region.label_text)
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    to_camera = _norm(cam_pos - anchor_pos)
    pole_dir = _norm(anchor_normal + to_camera)

    base = anchor_pos + pole_dir * 1.0
    top = base + pole_dir * config["FLAGPOLE_HEIGHT"]
    actors = []

    # Dot
    sp = vtkSphereSource()
    sp.SetCenter(base.tolist())
    sp.SetRadius(config["FLAGPOLE_DOT_RADIUS"])
    sp.SetPhiResolution(8)
    sp.SetThetaResolution(8)
    sp.Update()
    dm = vtkPolyDataMapper()
    dm.SetInputConnection(sp.GetOutputPort())
    da = vtkActor()
    da.SetMapper(dm)
    da.GetProperty().SetColor(*config["FLAGPOLE_DOT_COLOR"])
    da.GetProperty().LightingOff()
    da.SetPickable(False)
    renderer.AddActor(da)
    actors.append(da)

    # Line
    ln = vtkLineSource()
    ln.SetPoint1(base.tolist())
    ln.SetPoint2(top.tolist())
    ln.Update()
    lm = vtkPolyDataMapper()
    lm.SetInputConnection(ln.GetOutputPort())
    la = vtkActor()
    la.SetMapper(lm)
    la.GetProperty().SetColor(*config["FLAGPOLE_LINE_COLOR"])
    la.GetProperty().SetLineWidth(config["FLAGPOLE_LINE_WIDTH"])
    la.GetProperty().LightingOff()
    la.SetPickable(False)
    renderer.AddActor(la)
    actors.append(la)

    # Text follower
    s = config["FLAGPOLE_TEXT_SCALE"]
    f = vtkFollower()
    tm = vtkPolyDataMapper()
    tm.SetInputData(text_pd)
    f.SetMapper(tm)
    f.SetCamera(camera)
    f.SetScale(s, s, s)
    f.SetPosition(top.tolist())
    f.GetProperty().SetColor(*config["FLAGPOLE_COLOR"])
    f.GetProperty().LightingOff()
    f.SetPickable(False)
    renderer.AddActor(f)
    actors.append(f)

    return actors


# ==========================================================================
# SURFACE renderer (walk + deform vtkVectorText)
# ==========================================================================

def _walk(start, start_n, tangent, locator, normals, n_steps, step, cam_pos):
    cos_thresh = config.get("WALK_NORMAL_COS_THRESHOLD", 0.5)
    positions, norms, tangs = [start.copy()], [start_n.copy()], [tangent.copy()]
    pos, normal, tang = start.copy(), start_n.copy(), tangent.copy()
    skips = 0

    for _ in range(n_steps):
        cand = pos + tang * step
        rs = cand + normal * step * 2.0
        re = cand - normal * step * 2.0
        cid, hp = _ray_cast(locator, rs, re)
        if cid is None:
            break
        nn = normals[cid].copy()
        nl = np.linalg.norm(nn)
        if nl > 1e-9:
            nn /= nl
        if np.dot(nn, cam_pos - hp) < 0:
            break
        if np.dot(normal, nn) < cos_thresh:
            skips += 1
            if skips >= 3:
                break
            pos = hp
            continue
        skips = 0
        pos, normal = hp, nn
        tang = tang - np.dot(tang, normal) * normal
        tl = np.linalg.norm(tang)
        if tl < 1e-6:
            break
        tang /= tl
        positions.append(pos.copy())
        norms.append(normal.copy())
        tangs.append(tang.copy())
    return positions, norms, tangs


def _deform_text(text_pd, bounds, positions, normals, tangents, height):
    nf = len(positions)
    if nf < 2:
        return None
    arc = [0.0]
    for i in range(1, nf):
        arc.append(arc[-1] + np.linalg.norm(positions[i] - positions[i - 1]))
    total = arc[-1]
    if total < 1e-9:
        return None
    arc_n = [a / total for a in arc]

    bts = []
    for i in range(nf):
        bt = np.cross(normals[i], tangents[i])
        btl = np.linalg.norm(bt)
        bts.append(bt / btl if btl > 1e-9 else bt)

    pa, ba = np.array(positions), np.array(bts)
    xmin, xmax, ymin, ymax = bounds[0], bounds[1], bounds[2], bounds[3]
    tw, th = xmax - xmin, ymax - ymin
    if tw < 1e-9 or th < 1e-9:
        return None

    out = vtk.vtkPolyData()
    out.DeepCopy(text_pd)
    pts = vtk_to_numpy(out.GetPoints().GetData()).copy()
    new = np.zeros_like(pts)

    for i, pt in enumerate(pts):
        u = np.clip((pt[0] - xmin) / tw, 0.0, 1.0)
        v = (pt[1] - ymin) / th - 0.5
        seg = 0
        for j in range(1, nf):
            if arc_n[j] >= u:
                seg = j - 1
                break
        else:
            seg = nf - 2
        sl = arc_n[seg + 1] - arc_n[seg]
        fr = np.clip((u - arc_n[seg]) / sl if sl > 1e-9 else 0.0, 0.0, 1.0)
        p = (1 - fr) * pa[seg] + fr * pa[seg + 1]
        bt = _norm((1 - fr) * ba[seg] + fr * ba[seg + 1])
        new[i] = p + bt * (v * height)

    vp = vtk.vtkPoints()
    vp.SetData(numpy_to_vtk(new, deep=True))
    out.SetPoints(vp)
    return out


def render_surface(region, anchor_pos, anchor_normal, channel, cam_pos, cam_up, cam_fwd, renderer):
    """Surface-conforming label using dilated proxy mesh walk."""
    _, dil_normals, dil_locator = preprocess_channel(channel)
    text_pd, text_bounds = get_text_mesh(region.label_text)

    dilation = config["DILATION_AMOUNT"]
    height = config["SURFACE_LABEL_HEIGHT"]
    r, g, b = config["SURFACE_LABEL_COLOR"]

    tw, th = text_bounds[1] - text_bounds[0], text_bounds[3] - text_bounds[2]
    text_width = height * (tw / th if th > 1e-9 else 4.0)

    # Project anchor onto dilated surface
    proj_s = anchor_pos + anchor_normal * dilation * 3.0
    proj_e = anchor_pos - anchor_normal * dilation
    dc, dh = _ray_cast(dil_locator, proj_s, proj_e)
    if dc is None:
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    dn = dil_normals[dc].copy()
    if np.dot(dn, cam_pos - dh) < 0:
        dn = -dn

    cam_right = _norm(np.cross(cam_fwd, cam_up))
    tang = cam_right - np.dot(cam_right, dn) * dn
    tl = np.linalg.norm(tang)
    if tl < 1e-6:
        tang = cam_up - np.dot(cam_up, dn) * dn
    tang = _norm(tang)

    step = config["WALK_STEP"]
    steps = config["WALK_STEPS"]

    pf, nf_fwd, tf = _walk(dh, dn, tang, dil_locator, dil_normals, steps, step, cam_pos)
    pb, nb_bwd, tb = _walk(dh, dn, -tang, dil_locator, dil_normals, steps, step, cam_pos)
    tb = [-t for t in tb]

    if len(pb) > 1:
        ap = pb[::-1] + pf[1:]
        an = nb_bwd[::-1] + nf_fwd[1:]
        at = tb[::-1] + tf[1:]
    else:
        ap, an, at = pf, nf_fwd, tf

    if len(ap) < 2:
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    chord = np.linalg.norm(np.array(ap[-1]) - np.array(ap[0]))
    arc_total = sum(np.linalg.norm(np.array(ap[i]) - np.array(ap[i - 1]))
                    for i in range(1, len(ap)))
    if arc_total > 1e-9 and chord / arc_total < config["LOOP_THRESHOLD"]:
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    # If the walk is too short relative to the text width, the deformation maps
    # the full text onto a tiny arc — every letter gets scrunched. Fall back to
    # flagpole instead. (Catches early termination from curvature rejection,
    # front-face culling, or hitting the region edge.)
    min_fraction = config.get("SURFACE_MIN_WALK_FRACTION", 0.6)
    if arc_total < text_width * min_fraction:
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    # Trim to text width
    if arc_total > text_width:
        arc = [0.0]
        for i in range(1, len(ap)):
            arc.append(arc[-1] + np.linalg.norm(np.array(ap[i]) - np.array(ap[i - 1])))
        mid, half = arc_total / 2.0, text_width / 2.0
        keep = [i for i in range(len(ap)) if arc[i] >= mid - half and arc[i] <= mid + half]
        if len(keep) >= 2:
            ap = [ap[i] for i in keep]
            an = [an[i] for i in keep]
            at = [at[i] for i in keep]

    if len(ap) < 2:
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    deformed = _deform_text(text_pd, text_bounds, ap, an, at, height)
    if deformed is None:
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    mapper = vtkPolyDataMapper()
    mapper.SetInputData(deformed)
    actor = vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(r, g, b)
    actor.GetProperty().LightingOff()
    actor.SetPickable(False)
    renderer.AddActor(actor)
    return [actor]


# ==========================================================================
# Screen-space overlap rejection
# ==========================================================================

def _world_to_screen(renderer, point):
    renderer.SetWorldPoint(point[0], point[1], point[2], 1.0)
    renderer.WorldToDisplay()
    disp = renderer.GetDisplayPoint()
    return disp[0], disp[1]


def _compute_label_position(label_type, anchor_pos, anchor_normal, cam_pos):
    to_camera = _norm(cam_pos - anchor_pos)
    if label_type == "BILLBOARD":
        return anchor_pos + to_camera * config["BILLBOARD_OFFSET"]
    elif label_type == "FLAGPOLE":
        pole_dir = _norm(anchor_normal + to_camera)
        return anchor_pos + pole_dir * (1.0 + config["FLAGPOLE_HEIGHT"])
    else:
        return anchor_pos


def _estimate_screen_rect(renderer, label_pos, label_text, label_type):
    padding = config.get("LABEL_SCREEN_PADDING", 15)
    cx, cy = _world_to_screen(renderer, label_pos)

    text_pd, text_bounds = get_text_mesh(label_text)
    tw = text_bounds[1] - text_bounds[0]
    th = text_bounds[3] - text_bounds[2]

    if label_type == "SURFACE":
        h = config["SURFACE_LABEL_HEIGHT"]
        w = h * (tw / th if th > 1e-9 else 4.0)
    elif label_type == "FLAGPOLE":
        s = config["FLAGPOLE_TEXT_SCALE"]
        h = s * th + config["FLAGPOLE_HEIGHT"]
        w = s * tw
    else:
        s = config["BILLBOARD_TEXT_SCALE"]
        h = s * th
        w = s * tw

    camera = renderer.GetActiveCamera()
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    cam_up = _norm(np.array(camera.GetViewUp(), dtype=np.float64))
    cam_fwd = _norm(np.array(camera.GetFocalPoint(), dtype=np.float64) - cam_pos)
    cam_right = _norm(np.cross(cam_fwd, cam_up))

    right_pt = label_pos + cam_right * w
    up_pt = label_pos + cam_up * h
    rx, ry = _world_to_screen(renderer, right_pt)
    ux, uy = _world_to_screen(renderer, up_pt)

    half_w = max(abs(rx - cx) / 2.0 + padding, 25.0)
    half_h = max(abs(uy - cy) / 2.0 + padding, 12.0)

    return (cx, cy, half_w, half_h)


def _rects_overlap(r1, r2):
    return (abs(r1[0] - r2[0]) < r1[2] + r2[2] and
            abs(r1[1] - r2[1]) < r1[3] + r2[3])


def _any_overlap(rect, placed_rects):
    return any(_rects_overlap(rect, p) for p in placed_rects)


def _try_nudge(original_rect, placed_rects):
    cx, cy, hw, hh = original_rect
    if not _any_overlap(original_rect, placed_rects):
        return original_rect, (0.0, 0.0)

    directions = [(1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)]
    for scale in (1.0, 1.5, 2.0, 3.0):
        for dx, dy in directions:
            nudged = (cx + dx*hw*scale, cy + dy*hh*scale, hw, hh)
            if not _any_overlap(nudged, placed_rects):
                return nudged, (dx*hw*scale, dy*hh*scale)
    return None, (0.0, 0.0)


def _screen_offset_to_world(renderer, anchor_pos, dx_px, dy_px):
    renderer.SetWorldPoint(anchor_pos[0], anchor_pos[1], anchor_pos[2], 1.0)
    renderer.WorldToDisplay()
    sx, sy, sz = renderer.GetDisplayPoint()
    renderer.SetDisplayPoint(sx + dx_px, sy + dy_px, sz)
    renderer.DisplayToWorld()
    wx, wy, wz, ww = renderer.GetWorldPoint()
    if abs(ww) > 1e-9:
        return np.array([wx/ww, wy/ww, wz/ww]) - anchor_pos
    return np.zeros(3)


# ==========================================================================
# Main per-frame entry point
# ==========================================================================

def place_all_labels(single_regions, composite_regions, channels, renderer,
                     label_lookup_fn=None):
    """Place labels with nudge-based overlap resolution.

    Surface labels first (fixed geometry). Flagpoles/billboards nudged
    if overlapping. label_lookup_fn used for composite sub-marker labels.
    """
    camera = renderer.GetActiveCamera()
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    cam_focal = np.array(camera.GetFocalPoint(), dtype=np.float64)
    cam_up = _norm(np.array(camera.GetViewUp(), dtype=np.float64))
    cam_fwd = _norm(cam_focal - cam_pos)
    cam_dist = np.linalg.norm(cam_pos - cam_focal)

    max_labels = config["MAX_VISIBLE_LABELS"]
    all_regions = single_regions + composite_regions
    visible = get_visible_regions(all_regions, cam_pos, cam_fwd, max_labels)

    ch_by_name = {ch["marker_name"]: ch for ch in channels}

    actors = []
    placed_rects = []
    counts = {"SURFACE": 0, "FLAGPOLE": 0, "BILLBOARD": 0, "nudged": 0, "rejected": 0}

    # Pass 1: surface labels (fixed geometry, hard reject if overlap)
    surface_regions = []
    other_regions = []
    for region in visible:
        if pick_label_type(region, cam_dist) == "SURFACE":
            surface_regions.append(region)
        else:
            other_regions.append((region, pick_label_type(region, cam_dist)))

    for region in surface_regions:
        anchor_pos, anchor_normal = _snap_to_surface(region, channels, cam_pos, cam_fwd)
        if anchor_pos is None:
            counts["rejected"] += 1
            continue

        rect = _estimate_screen_rect(renderer, anchor_pos, region.label_text, "SURFACE")
        if _any_overlap(rect, placed_rects):
            counts["rejected"] += 1
            continue
        placed_rects.append(rect)

        if region.is_composite:
            primary_ch = ch_by_name.get(region.channels[0])
            if primary_ch:
                actors.extend(render_surface(region, anchor_pos, anchor_normal,
                                              primary_ch, cam_pos, cam_up, cam_fwd, renderer))
            else:
                actors.extend(render_flagpole(region, anchor_pos, anchor_normal,
                                              camera, renderer))
            counts["SURFACE"] += 1

            # Fanned-out sub-flagpoles for individual markers
            cam_right = _norm(np.cross(cam_fwd, cam_up))
            n_markers = len(region.channels)
            fan_spacing = config.get("FLAGPOLE_FAN_SPACING", 2.0)

            for idx, marker in enumerate(region.channels):
                marker_text = label_lookup_fn(marker) if label_lookup_fn else None
                if marker_text is None:
                    continue
                lateral_offset = (idx - (n_markers - 1) / 2.0) * fan_spacing
                offset_pos = anchor_pos + cam_right * lateral_offset

                sub_label_pos = _compute_label_position("FLAGPOLE", offset_pos, anchor_normal, cam_pos)
                sub_rect = _estimate_screen_rect(renderer, sub_label_pos, marker_text, "FLAGPOLE")

                if _any_overlap(sub_rect, placed_rects):
                    nudged_rect, (dx, dy) = _try_nudge(sub_rect, placed_rects)
                    if nudged_rect is None:
                        counts["rejected"] += 1
                        continue
                    sub_rect = nudged_rect
                    offset_pos = offset_pos + _screen_offset_to_world(renderer, offset_pos, dx, dy)
                    counts["nudged"] += 1

                placed_rects.append(sub_rect)
                sub = Region(channel=marker, label_text=marker_text,
                             centroid=offset_pos, bounds=region.bounds,
                             normal=anchor_normal, n_cells=0)
                actors.extend(render_flagpole(sub, offset_pos, anchor_normal, camera, renderer))
                counts["FLAGPOLE"] += 1
        else:
            ch = ch_by_name.get(region.channel)
            if ch:
                actors.extend(render_surface(region, anchor_pos, anchor_normal,
                                              ch, cam_pos, cam_up, cam_fwd, renderer))
            else:
                actors.extend(render_flagpole(region, anchor_pos, anchor_normal,
                                              camera, renderer))
            counts["SURFACE"] += 1

    # Pass 2: flagpoles and billboards with nudging
    for region, label_type in other_regions:
        anchor_pos, anchor_normal = _snap_to_surface(region, channels, cam_pos, cam_fwd)
        if anchor_pos is None:
            to_cam = _norm(cam_pos - region.centroid)
            anchor_pos = region.centroid + to_cam * 2.0
            anchor_normal = to_cam

        label_pos = _compute_label_position(label_type, anchor_pos, anchor_normal, cam_pos)
        rect = _estimate_screen_rect(renderer, label_pos, region.label_text, label_type)

        if _any_overlap(rect, placed_rects):
            nudged_rect, (dx, dy) = _try_nudge(rect, placed_rects)
            if nudged_rect is None:
                counts["rejected"] += 1
                continue
            rect = nudged_rect
            world_offset = _screen_offset_to_world(renderer, anchor_pos, dx, dy)
            anchor_pos = anchor_pos + world_offset
            label_pos = _compute_label_position(label_type, anchor_pos, anchor_normal, cam_pos)
            counts["nudged"] += 1

        placed_rects.append(rect)

        if label_type == "BILLBOARD":
            actors.extend(render_billboard(region, anchor_pos, anchor_normal, camera, renderer))
            counts["BILLBOARD"] += 1
        elif label_type == "FLAGPOLE":
            actors.extend(render_flagpole(region, anchor_pos, anchor_normal, camera, renderer))
            counts["FLAGPOLE"] += 1

    print(f"[label_placement] dist={cam_dist:.1f} visible={len(visible)}/{len(all_regions)} "
          f"S={counts['SURFACE']} F={counts['FLAGPOLE']} B={counts['BILLBOARD']} "
          f"nudged={counts['nudged']} rejected={counts['rejected']}")

    return actors, placed_rects


# ==========================================================================
# Interaction renderers
# ==========================================================================

def render_interaction_billboard(interaction, camera, renderer):
    text_pd, _ = get_text_mesh(interaction.label_text)
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    offset = config.get("INTERACTION_BILLBOARD_OFFSET", 2.0)
    scale = config.get("INTERACTION_BILLBOARD_SCALE", 0.5)
    r, g, b = config.get("INTERACTION_BILLBOARD_COLOR", (1.0, 0.8, 0.5))

    to_camera = _norm(cam_pos - interaction.midpoint)
    pos = interaction.midpoint + to_camera * offset

    f = vtkFollower()
    m = vtkPolyDataMapper()
    m.SetInputData(text_pd)
    f.SetMapper(m)
    f.SetCamera(camera)
    f.SetScale(scale, scale, scale)
    f.SetPosition(pos.tolist())
    f.GetProperty().SetColor(r, g, b)
    f.GetProperty().LightingOff()
    f.SetPickable(False)
    renderer.AddActor(f)
    return [f]


def render_interaction_flagpole(interaction, camera, renderer):
    text_pd, _ = get_text_mesh(interaction.label_text)
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    height = config.get("INTERACTION_FLAGPOLE_HEIGHT", 3.0)
    scale = config.get("INTERACTION_FLAGPOLE_SCALE", 0.4)
    r, g, b = config.get("INTERACTION_FLAGPOLE_COLOR", (1.0, 0.8, 0.5))

    to_camera = _norm(cam_pos - interaction.midpoint)
    pole_dir = _norm(interaction.normal + to_camera)
    base = interaction.midpoint
    top = base + pole_dir * height

    actors = []

    sp = vtkSphereSource()
    sp.SetCenter(base.tolist())
    sp.SetRadius(config.get("FLAGPOLE_DOT_RADIUS", 0.15))
    sp.SetPhiResolution(8)
    sp.SetThetaResolution(8)
    sp.Update()
    dm = vtkPolyDataMapper()
    dm.SetInputConnection(sp.GetOutputPort())
    da = vtkActor()
    da.SetMapper(dm)
    da.GetProperty().SetColor(1.0, 0.6, 0.2)
    da.GetProperty().LightingOff()
    da.SetPickable(False)
    renderer.AddActor(da)
    actors.append(da)

    ln = vtkLineSource()
    ln.SetPoint1(base.tolist())
    ln.SetPoint2(top.tolist())
    ln.Update()
    lm = vtkPolyDataMapper()
    lm.SetInputConnection(ln.GetOutputPort())
    la = vtkActor()
    la.SetMapper(lm)
    la.GetProperty().SetColor(0.6, 0.4, 0.2)
    la.GetProperty().SetLineWidth(1.5)
    la.GetProperty().LightingOff()
    la.SetPickable(False)
    renderer.AddActor(la)
    actors.append(la)

    f = vtkFollower()
    tm = vtkPolyDataMapper()
    tm.SetInputData(text_pd)
    f.SetMapper(tm)
    f.SetCamera(camera)
    f.SetScale(scale, scale, scale)
    f.SetPosition(top.tolist())
    f.GetProperty().SetColor(r, g, b)
    f.GetProperty().LightingOff()
    f.SetPickable(False)
    renderer.AddActor(f)
    actors.append(f)

    return actors


def place_interaction_labels(interactions, placed_rects, renderer):
    """Place interaction labels (pass 3). Nudges to avoid existing labels."""
    camera = renderer.GetActiveCamera()
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    cam_focal = np.array(camera.GetFocalPoint(), dtype=np.float64)
    cam_fwd = _norm(cam_focal - cam_pos)
    min_gap = config.get("INTERACTION_BILLBOARD_MIN_GAP", 2.0)

    actors = []
    n_billboard = n_flagpole = n_nudged = n_rejected = 0

    for ix in interactions:
        if ix.label_text is None:
            continue
        if np.dot(ix.midpoint - cam_pos, cam_fwd) <= 0:
            continue

        if ix.gap >= min_gap:
            label_type = "BILLBOARD"
            to_cam = _norm(cam_pos - ix.midpoint)
            label_pos = ix.midpoint + to_cam * config.get("INTERACTION_BILLBOARD_OFFSET", 2.0)
        else:
            label_type = "FLAGPOLE"
            to_cam = _norm(cam_pos - ix.midpoint)
            pole_dir = _norm(ix.normal + to_cam)
            label_pos = ix.midpoint + pole_dir * config.get("INTERACTION_FLAGPOLE_HEIGHT", 3.0)

        rect = _estimate_screen_rect(renderer, label_pos, ix.label_text, label_type)

        if _any_overlap(rect, placed_rects):
            nudged_rect, (dx, dy) = _try_nudge(rect, placed_rects)
            if nudged_rect is None:
                n_rejected += 1
                continue
            rect = nudged_rect
            n_nudged += 1

        placed_rects.append(rect)

        if ix.gap >= min_gap:
            actors.extend(render_interaction_billboard(ix, camera, renderer))
            n_billboard += 1
        else:
            actors.extend(render_interaction_flagpole(ix, camera, renderer))
            n_flagpole += 1

    print(f"[label_placement] interactions: billboard={n_billboard} flagpole={n_flagpole} "
          f"nudged={n_nudged} rejected={n_rejected}")
    return actors


def remove_actors(actors, renderer):
    for a in actors:
        renderer.RemoveActor(a)
    actors.clear()


# ==========================================================================
# Cluster renderer
# ==========================================================================

def render_cluster_label(cluster, camera, renderer):
    if cluster.label_text is None:
        return []
    text_pd, _ = get_text_mesh(cluster.label_text)
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)

    base_scale = config.get("CLUSTER_LABEL_BASE_SCALE", 0.5)
    extent_factor = config.get("CLUSTER_EXTENT_SCALE_FACTOR", 20.0)
    offset = config.get("CLUSTER_LABEL_OFFSET", 5.0)
    r, g, b = config.get("CLUSTER_LABEL_COLOR", (1.0, 1.0, 0.8))

    scale = base_scale * (1.0 + cluster.extent / extent_factor)
    to_camera = _norm(cam_pos - cluster.centroid)
    pos = cluster.centroid + to_camera * offset

    f = vtkFollower()
    m = vtkPolyDataMapper()
    m.SetInputData(text_pd)
    f.SetMapper(m)
    f.SetCamera(camera)
    f.SetScale(scale, scale, scale)
    f.SetPosition(pos.tolist())
    f.GetProperty().SetColor(r, g, b)
    f.GetProperty().LightingOff()
    f.SetPickable(False)
    renderer.AddActor(f)
    return [f]


# ==========================================================================
# Hierarchical entry point
# ==========================================================================

def place_labels_hierarchical(hierarchy, single_regions, composite_regions,
                               channels, renderer, interactions=None,
                               label_lookup_fn=None):
    """Top-level label placement using zoom-dependent hierarchy.

    Far: cluster/overview billboard labels.
    Close: individual region labels (surface/flagpole/billboard) + interaction labels.
    """
    camera = renderer.GetActiveCamera()
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    cam_focal = np.array(camera.GetFocalPoint(), dtype=np.float64)
    cam_fwd = _norm(cam_focal - cam_pos)
    cam_dist = np.linalg.norm(cam_pos - cam_focal)

    clusters = hierarchy.get_clusters(cam_dist)

    # None → individual region labels (close zoom)
    if clusters is None:
        actors, placed_rects = place_all_labels(single_regions, composite_regions,
                                                 channels, renderer, label_lookup_fn)
        if interactions:
            ix_actors = place_interaction_labels(interactions, placed_rects, renderer)
            actors.extend(ix_actors)
        return actors

    # Cluster or overview
    max_labels = config["MAX_VISIBLE_LABELS"]
    padding = config.get("LABEL_SCREEN_PADDING", 15)

    visible = []
    for cl in clusters:
        if cl.label_text is None:
            continue
        if np.dot(cl.centroid - cam_pos, cam_fwd) <= 0:
            continue
        d = np.linalg.norm(cl.centroid - cam_pos)
        visible.append((d, cl))
    visible.sort(key=lambda x: x[0])
    visible = [cl for _, cl in visible[:max_labels]]

    actors = []
    placed_rects = []
    n_placed = n_nudged = n_rejected = 0

    for cl in visible:
        to_camera = _norm(cam_pos - cl.centroid)
        cluster_offset = config.get("CLUSTER_LABEL_OFFSET", 5.0)
        label_pos = cl.centroid + to_camera * cluster_offset

        cx, cy = _world_to_screen(renderer, label_pos)
        offset_pt = label_pos + np.array([cl.extent / 2, 0, 0])
        ox, oy = _world_to_screen(renderer, offset_pt)
        half_w = max(abs(ox - cx), 30.0) + padding
        half_h = max(half_w * 0.3, 15.0) + padding
        rect = (cx, cy, half_w, half_h)

        if _any_overlap(rect, placed_rects):
            nudged_rect, (dx, dy) = _try_nudge(rect, placed_rects)
            if nudged_rect is None:
                n_rejected += 1
                continue
            rect = nudged_rect
            n_nudged += 1

        placed_rects.append(rect)
        actors.extend(render_cluster_label(cl, camera, renderer))
        n_placed += 1

    level = "OVERVIEW" if len(clusters) == 1 else f"CLUSTER({len(clusters)})"
    print(f"[label_placement] dist={cam_dist:.1f} level={level} "
          f"clusters={len(visible)} placed={n_placed} "
          f"nudged={n_nudged} rejected={n_rejected}")

    return actors
