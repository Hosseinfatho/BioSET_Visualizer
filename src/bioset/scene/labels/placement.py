"""
Label rendering with zoom-based heuristic type selection.

Adapted from cycif_mesh_labelling/new/labeling.py for BioSET:
- Imports from .settings and .regions instead of settings/regions
- label_lookup_fn passed as parameter instead of imported from labels.py
- Module-level locator/dilation caches work the same way (keyed by marker_name)
"""

from __future__ import annotations

import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk
from vtkmodules.vtkFiltersCore import vtkPolyDataNormals, vtkTriangleFilter
from vtkmodules.vtkFiltersModeling import vtkLinearExtrusionFilter
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


def _style_label_actor(actor, color):
    """Apply consistent prominent styling to any label actor.

    Fully ambient (unaffected by scene lighting), clean and readable
    at any angle.
    """
    prop = actor.GetProperty()
    prop.SetColor(*color)
    prop.SetAmbient(1.0)
    prop.SetDiffuse(0.0)
    prop.SetSpecular(0.0)
    prop.LightingOff()
    prop.EdgeVisibilityOff()


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
    """Find the visible surface point for this region.

    Strategy:
      1. Ray from camera through centroid → first hit on this channel's mesh.
      2. If that misses, try a local normal projection from above the centroid.

    No AABB ownership check — for 3D tissue volumes, the camera hits the
    top surface (high Z) while region cell centroids are in the interior
    (low Z). The channel's cell locator already constrains hits to the
    correct channel mesh.

    Returns (hit_point, normal) or (None, None).
    """
    centroid = region.centroid
    target_channel = region.channels[0] if region.is_composite else region.channel
    ch = next((c for c in channels if c["marker_name"] == target_channel), None)
    if ch is None:
        return None, None

    locator = _ensure_channel_locator(ch)
    normals_arr = ch["cell_normals"]

    if np.dot(centroid - cam_pos, cam_fwd) <= 0:
        return None, None

    def _extract_normal(cell_id, hit_point):
        normal = normals_arr[cell_id].copy()
        nl = np.linalg.norm(normal)
        if nl > 1e-9:
            normal /= nl
        if np.dot(normal, cam_pos - hit_point) < 0:
            normal = -normal
        return normal

    # --- Attempt 1: ray from camera through centroid ---
    direction = centroid - cam_pos
    dist = np.linalg.norm(direction)
    if dist > 1e-9:
        ray_end = cam_pos + (direction / dist) * dist * 2.0
        cell_id, hit_point = _ray_cast(locator, cam_pos, ray_end)
        if cell_id is not None:
            return hit_point, _extract_normal(cell_id, hit_point)

    # --- Attempt 2: local normal projection ---
    n = region.normal
    b = region.bounds
    diag = np.sqrt((b[1]-b[0])**2 + (b[3]-b[2])**2 + (b[5]-b[4])**2)
    offset = max(diag * 0.5, 5.0)
    ray_start = centroid + n * offset
    ray_end = centroid - n * offset
    cell_id, hit_point = _ray_cast(locator, ray_start, ray_end)
    if cell_id is not None:
        normal = _extract_normal(cell_id, hit_point)
        if np.dot(normal, cam_pos - hit_point) > 0:
            return hit_point, normal

    return None, None


def _is_occluded(hit_point, region, channels, cam_pos):
    """Check if hit_point is occluded by any OTHER channel's mesh.

    Casts a ray from camera toward hit_point on each other channel.
    If any other channel's surface is hit significantly closer → region is hidden.
    
    For composite regions, skips ALL participating channels (not just the first)
    since co-loc sites are by definition where those channels overlap.
    """
    # Build set of channels to skip — all channels this region belongs to
    skip_channels = set(region.channels) if region.is_composite else {region.channel}
    dist_to_hit = np.linalg.norm(hit_point - cam_pos)

    # Tolerance must be generous — in dense tissue, channels at the same
    # surface can differ by sub-unit distances due to mesh geometry differences.
    tolerance = max(dist_to_hit * 0.02, 1.0)  # 2% of distance or 1 unit minimum

    for ch in channels:
        if ch["marker_name"] in skip_channels:
            continue
        other_loc = _ensure_channel_locator(ch)
        direction = hit_point - cam_pos
        dl = np.linalg.norm(direction)
        if dl < 1e-9:
            continue
        ray_end = cam_pos + (direction / dl) * dl * 1.01
        _, other_hit = _ray_cast(other_loc, cam_pos, ray_end)
        if other_hit is not None:
            other_dist = np.linalg.norm(other_hit - cam_pos)
            if other_dist < dist_to_hit - tolerance:
                return True
    return False


# ==========================================================================
# Text template cache
# ==========================================================================

_text_cache = {}


def get_text_mesh(text):
    """Return (polydata, 2D_bounds) for a label string. Cached.

    The text is extruded along Z to give it 3D thickness.
    2D bounds are the original XY extent (before extrusion).
    The Z dimension encodes depth — used as normal-offset during
    surface deformation.
    """
    if text not in _text_cache:
        vt = vtk.vtkVectorText()
        vt.SetText(text)
        vt.Update()

        # Store 2D bounds before extrusion (XY extent of flat text)
        flat_bounds = vt.GetOutput().GetBounds()

        depth = config.get("TEXT_EXTRUSION_DEPTH", 0.15)

        extrude = vtkLinearExtrusionFilter()
        extrude.SetInputConnection(vt.GetOutputPort())
        extrude.SetExtrusionTypeToVectorExtrusion()
        extrude.SetVector(0, 0, 1)
        extrude.SetScaleFactor(depth)
        extrude.Update()

        tri = vtkTriangleFilter()
        tri.SetInputConnection(extrude.GetOutputPort())
        tri.Update()

        nf = vtkPolyDataNormals()
        nf.SetInputConnection(tri.GetOutputPort())
        nf.ComputePointNormalsOn()
        nf.ComputeCellNormalsOn()
        nf.ConsistencyOn()
        nf.AutoOrientNormalsOn()
        nf.SplittingOn()
        nf.SetFeatureAngle(60.0)
        nf.Update()

        pd = vtk.vtkPolyData()
        pd.DeepCopy(nf.GetOutput())

        _text_cache[text] = (pd, flat_bounds)
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
    smooth = config.get("SMOOTH_ITERATIONS", 100)

    print(f"[label_placement] Dilating {marker} (amount={amount}, smooth={smooth})...")
    print(f"[label_placement]   Original mesh: {mesh.GetNumberOfPoints()} pts, "
          f"bounds={mesh.GetBounds()}")

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

    print(f"[label_placement] Dilated {marker}: {dilated.GetNumberOfPoints()} pts, "
          f"bounds={dilated.GetBounds()}")

    result = (dilated, cell_normals, loc)
    _dilated_cache[marker] = result
    return result


def clear_channel_caches(marker_name=None):
    """Clear cached locators, dilated meshes, and text meshes. Call when meshes are reloaded."""
    if marker_name is None:
        _channel_locators.clear()
        _dilated_cache.clear()
        _text_cache.clear()
    else:
        _channel_locators.pop(marker_name, None)
        _dilated_cache.pop(marker_name, None)


# ==========================================================================
# Viewport culling
# ==========================================================================

def _is_in_viewport(renderer, point):
    """Check if a 3D point projects to within the render window viewport."""
    rw = renderer.GetRenderWindow()
    if rw is None:
        return True
    size = rw.GetSize()
    if size[0] < 2 or size[1] < 2:
        return True  # window not ready yet
    renderer.SetWorldPoint(point[0], point[1], point[2], 1.0)
    renderer.WorldToDisplay()
    dx, dy, dz = renderer.GetDisplayPoint()
    if dz < 0 or dz > 1:
        return False
    margin = 50
    return (-margin <= dx <= size[0] + margin and
            -margin <= dy <= size[1] + margin)


# ==========================================================================
# Visibility culling
# ==========================================================================

def get_visible_regions(regions, cam_pos, cam_fwd, max_labels, renderer=None):
    """Return front-facing regions in viewport, sorted by distance, capped at max_labels."""
    visible = []
    for r in regions:
        if r.label_text is None:
            continue
        if np.dot(r.centroid - cam_pos, cam_fwd) <= 0:
            continue
        if renderer is not None and not _is_in_viewport(renderer, r.centroid):
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
    m.ScalarVisibilityOff()
    f.SetMapper(m)
    f.SetCamera(camera)
    f.SetScale(s, s, s)
    f.SetPosition(pos.tolist())
    _style_label_actor(f, (r, g, b))
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
    tm.ScalarVisibilityOff()
    f.SetMapper(tm)
    f.SetCamera(camera)
    f.SetScale(s, s, s)
    f.SetPosition(top.tolist())
    _style_label_actor(f, config["FLAGPOLE_COLOR"])
    f.SetPickable(False)
    renderer.AddActor(f)
    actors.append(f)

    return actors


# ==========================================================================
# SURFACE renderer (walk + deform vtkVectorText)
# ==========================================================================

def _walk(start, start_n, tangent, locator, normals, n_steps, step, cam_pos):
    """Walk along the dilated surface, recording camera-facing samples.

    When the walk hits a back-facing cell or falls off the mesh,
    extrapolates straight in the last tangent direction for extra arc
    length so labels don't need to shrink.
    """
    cos_thresh = config.get("WALK_NORMAL_COS_THRESHOLD", 0.5)
    max_extrapolate = config.get("WALK_EXTRAPOLATE_STEPS", 10)
    positions, norms, tangs = [start.copy()], [start_n.copy()], [tangent.copy()]
    pos, normal, tang = start.copy(), start_n.copy(), tangent.copy()
    skips = 0
    steps_used = 0

    for _ in range(n_steps):
        steps_used += 1
        cand = pos + tang * step
        rs = cand + normal * step * 2.0
        re = cand - normal * step * 2.0
        cid, hp = _ray_cast(locator, rs, re)

        if cid is None:
            break  # fell off mesh → extrapolate below

        nn = normals[cid].copy()
        nl = np.linalg.norm(nn)
        if nl > 1e-9:
            nn /= nl

        if np.dot(nn, cam_pos - hp) < 0:
            break  # back-facing → extrapolate below

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

    # Extrapolate straight in the last tangent direction
    n_extra = min(max_extrapolate, n_steps - steps_used)
    for _ in range(n_extra):
        pos = pos + tang * step
        positions.append(pos.copy())
        norms.append(normal.copy())
        tangs.append(tang.copy())

    return positions, norms, tangs


def _quick_probe(start, start_n, tangent, locator, normals, step, cam_pos, max_steps=8):
    """Fast walk to estimate available arc length in one direction.
    Includes extrapolation estimate when walk hits back-facing/miss."""
    cos_thresh = config.get("WALK_NORMAL_COS_THRESHOLD", 0.5)
    max_extrapolate = config.get("WALK_EXTRAPOLATE_STEPS", 10)
    pos, normal = start.copy(), start_n.copy()
    tang = tangent.copy()
    arc = 0.0
    skips = 0
    steps_used = 0

    for _ in range(max_steps):
        steps_used += 1
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
        arc += np.linalg.norm(hp - pos)
        pos, normal = hp, nn
        tang = tang - np.dot(tang, normal) * normal
        tl = np.linalg.norm(tang)
        if tl < 1e-6:
            break
        tang /= tl

    # Add extrapolation arc estimate
    n_extra = min(max_extrapolate, max_steps - steps_used)
    arc += n_extra * step

    return arc


def _deform_text(text_pd, bounds, positions, normals, tangents, height, cam_up):
    """Deform extruded 3D vtkVectorText onto a surface polyline.

    Coordinate mapping:
      X → along the polyline arc (text width)
      Y → bitangent direction (letter height, parallel-transported from cam_up)
      Z → surface normal direction (extrusion depth → pops text out)

    Uses parallel transport for the bitangent to prevent twist.
    """
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

    # Parallel transport bitangent from cam_up
    t0 = tangents[0] if isinstance(tangents[0], np.ndarray) else np.array(tangents[0])
    bt = cam_up - np.dot(cam_up, t0) * t0
    btl = np.linalg.norm(bt)
    if btl < 1e-9:
        bt = np.cross(normals[0], t0)
        btl = np.linalg.norm(bt)
        if btl > 1e-9:
            bt = bt / btl
            if np.dot(bt, cam_up) < 0:
                bt = -bt
        else:
            bt = cam_up.copy()
    else:
        bt = bt / btl

    bts = [bt.copy()]
    for i in range(1, nf):
        t_i = tangents[i] if isinstance(tangents[i], np.ndarray) else np.array(tangents[i])
        bt = bt - np.dot(bt, t_i) * t_i
        btl = np.linalg.norm(bt)
        if btl > 1e-9:
            bt = bt / btl
        else:
            bt = cam_up - np.dot(cam_up, t_i) * t_i
            btl = np.linalg.norm(bt)
            bt = bt / btl if btl > 1e-9 else bts[-1].copy()
        bts.append(bt.copy())

    # Compute local outward normal at each sample: cross(tangent, bitangent)
    local_normals = []
    for i in range(nf):
        t_i = tangents[i] if isinstance(tangents[i], np.ndarray) else np.array(tangents[i])
        ln = np.cross(t_i, bts[i])
        lnl = np.linalg.norm(ln)
        local_normals.append(ln / lnl if lnl > 1e-9 else np.array(normals[i]))

    pa = np.array(positions)
    ba = np.array(bts)
    na = np.array(local_normals)
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
        w = pt[2]  # extrusion depth (0 = base face, depth = top face)

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
        bt_interp = _norm((1 - fr) * ba[seg] + fr * ba[seg + 1])
        n_interp = _norm((1 - fr) * na[seg] + fr * na[seg + 1])
        new[i] = p + bt_interp * (v * height) + n_interp * (w * height)

    vp = vtk.vtkPoints()
    vp.SetData(numpy_to_vtk(new, deep=True))
    out.SetPoints(vp)
    return out


def render_surface(region, anchor_pos, anchor_normal, channel, cam_pos, cam_up, cam_fwd, renderer):
    """Surface-conforming label using probe-then-commit direction selection."""
    _, dil_normals, dil_locator = preprocess_channel(channel)
    text_pd, text_bounds = get_text_mesh(region.label_text)

    dilation = config["DILATION_AMOUNT"]
    r, g, b = config["SURFACE_LABEL_COLOR"]

    # Size label by region extent
    bnds = region.bounds
    region_diag = np.sqrt((bnds[1]-bnds[0])**2 + (bnds[3]-bnds[2])**2 + (bnds[5]-bnds[4])**2)
    height = np.clip(
        region_diag * config.get("SURFACE_HEIGHT_FACTOR", 0.12),
        config.get("SURFACE_MIN_HEIGHT", 0.6),
        config.get("SURFACE_MAX_HEIGHT", 3.0),
    )

    tw, th = text_bounds[1] - text_bounds[0], text_bounds[3] - text_bounds[2]
    text_aspect = tw / th if th > 1e-9 else 4.0
    text_width = height * text_aspect

    # === DIAGNOSTIC: trace every step ===
    _dbg = f"[SURFACE_DBG] '{region.label_text[:30]}' ch={channel['marker_name']}"
    print(f"{_dbg} region_diag={region_diag:.2f} height={height:.3f} text_width={text_width:.2f}")
    print(f"{_dbg} anchor={anchor_pos} normal={anchor_normal}")
    print(f"{_dbg} dilation={dilation}")

    # Project anchor onto dilated surface
    proj_s = anchor_pos + anchor_normal * dilation * 3.0
    proj_e = anchor_pos - anchor_normal * dilation
    dc, dh = _ray_cast(dil_locator, proj_s, proj_e)
    if dc is None:
        print(f"{_dbg} FAIL: dilated projection ray missed. "
              f"proj_s={proj_s}, proj_e={proj_e}, ray_len={np.linalg.norm(proj_s-proj_e):.3f}")
        # Try wider ray
        proj_s2 = anchor_pos + anchor_normal * dilation * 10.0
        proj_e2 = anchor_pos - anchor_normal * dilation * 5.0
        dc2, dh2 = _ray_cast(dil_locator, proj_s2, proj_e2)
        if dc2 is not None:
            print(f"{_dbg}   ... wider ray HIT at dist={np.linalg.norm(dh2-anchor_pos):.3f}")
        else:
            # Check if dilated mesh even has cells near anchor
            closest = [0.0]*3
            cid_ref = vtk.mutable(0)
            sub = vtk.mutable(0)
            d2 = vtk.mutable(0.0)
            dil_locator.FindClosestPoint(anchor_pos.tolist(), closest, cid_ref, sub, d2)
            print(f"{_dbg}   ... closest dilated point dist={np.sqrt(float(d2.get())):.3f} "
                  f"at {closest}")
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    dn = dil_normals[dc].copy()
    if np.dot(dn, cam_pos - dh) < 0:
        dn = -dn

    step = config["WALK_STEP"]
    steps = config["WALK_STEPS"]
    n_probes = config.get("SURFACE_PROBE_DIRECTIONS", 6)
    probe_steps = config.get("SURFACE_PROBE_STEPS", 8)

    print(f"{_dbg} dilated hit OK at {dh}, walk_step={step}, walk_steps={steps}")

    # --- Probe directions ---
    if hasattr(region, 'principal_axis') and region.principal_axis is not None:
        paxis = region.principal_axis.copy()
        base_tang = paxis - np.dot(paxis, dn) * dn
        tl = np.linalg.norm(base_tang)
        if tl < 1e-6:
            cam_right = _norm(np.cross(cam_fwd, cam_up))
            base_tang = cam_right - np.dot(cam_right, dn) * dn
            tl = np.linalg.norm(base_tang)
        if tl < 1e-6:
            base_tang = cam_up - np.dot(cam_up, dn) * dn
    else:
        cam_right = _norm(np.cross(cam_fwd, cam_up))
        base_tang = cam_right - np.dot(cam_right, dn) * dn
        tl = np.linalg.norm(base_tang)
        if tl < 1e-6:
            base_tang = cam_up - np.dot(cam_up, dn) * dn
    base_tang = _norm(base_tang)

    best_arc = -1.0
    best_tang = base_tang

    for i in range(n_probes):
        angle = np.pi * i / n_probes
        cos_a, sin_a = np.cos(angle), np.sin(angle)
        tang = (base_tang * cos_a +
                np.cross(dn, base_tang) * sin_a +
                dn * np.dot(dn, base_tang) * (1 - cos_a))
        tang = _norm(tang)
        arc_fwd = _quick_probe(dh, dn, tang, dil_locator, dil_normals,
                                step, cam_pos, probe_steps)
        arc_bwd = _quick_probe(dh, dn, -tang, dil_locator, dil_normals,
                                step, cam_pos, probe_steps)
        total = arc_fwd + arc_bwd
        if total > best_arc:
            best_arc = total
            best_tang = tang.copy()

    print(f"{_dbg} best_arc={best_arc:.2f} (need text_width={text_width:.2f})")

    # --- Full walk ---
    pf, nf_fwd, tf = _walk(dh, dn, best_tang, dil_locator, dil_normals, steps, step, cam_pos)
    pb, nb_bwd, tb = _walk(dh, dn, -best_tang, dil_locator, dil_normals, steps, step, cam_pos)
    tb = [-t for t in tb]

    if len(pb) > 1:
        ap = pb[::-1] + pf[1:]
        an = nb_bwd[::-1] + nf_fwd[1:]
        at = tb[::-1] + tf[1:]
    else:
        ap, an, at = pf, nf_fwd, tf

    print(f"{_dbg} walk: fwd={len(pf)} bwd={len(pb)} total={len(ap)} samples")

    if len(ap) < 2:
        print(f"{_dbg} FAIL: walk too short ({len(ap)} samples)")
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    # Orient left-to-right
    cam_right = _norm(np.cross(cam_fwd, cam_up))
    chord_dir = np.array(ap[-1]) - np.array(ap[0])
    if np.dot(chord_dir, cam_right) < 0:
        ap = ap[::-1]
        an = an[::-1]
        at = [-t for t in at[::-1]]

    chord = np.linalg.norm(np.array(ap[-1]) - np.array(ap[0]))
    arc_total = sum(np.linalg.norm(np.array(ap[i]) - np.array(ap[i - 1]))
                    for i in range(1, len(ap)))

    ratio = arc_total / chord if chord > 1e-9 else 0.0
    print(f"{_dbg} arc_total={arc_total:.2f} chord={chord:.2f} ratio={ratio:.2f}")

    if chord < 1e-9 or ratio > config["LOOP_THRESHOLD"]:
        print(f"{_dbg} FAIL: loop detected (arc/chord={ratio:.2f} > {config['LOOP_THRESHOLD']})")
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
        print(f"{_dbg} FAIL: trimmed to < 2 samples")
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    deformed = _deform_text(text_pd, text_bounds, ap, an, at, height, cam_up)
    if deformed is None:
        print(f"{_dbg} FAIL: _deform_text returned None")
        return render_flagpole(region, anchor_pos, anchor_normal,
                               renderer.GetActiveCamera(), renderer)

    # Check deformed geometry bounds
    db = deformed.GetBounds()
    deformed_diag = np.sqrt((db[1]-db[0])**2 + (db[3]-db[2])**2 + (db[5]-db[4])**2)
    print(f"{_dbg} SUCCESS: deformed {deformed.GetNumberOfPoints()} pts, "
          f"bounds_diag={deformed_diag:.3f}, bounds=({db[0]:.1f},{db[1]:.1f},{db[2]:.1f},{db[3]:.1f},{db[4]:.1f},{db[5]:.1f})")

    mapper = vtkPolyDataMapper()
    mapper.SetInputData(deformed)
    mapper.ScalarVisibilityOff()
    actor = vtkActor()
    actor.SetMapper(mapper)
    _style_label_actor(actor, (r, g, b))
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
                     interactions=None, label_lookup_fn=None):
    """Place labels with strict priority ordering.

    Priority 1: Co-localization labels (surface/flagpole/billboard by zoom)
                + their individual marker flagpoles always alongside.
    Priority 2: Interaction labels (flagpole/billboard by gap size).
    Priority 3: Remaining single-channel regions (not in any co-loc).
    """
    camera = renderer.GetActiveCamera()
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    cam_focal = np.array(camera.GetFocalPoint(), dtype=np.float64)
    cam_up = _norm(np.array(camera.GetViewUp(), dtype=np.float64))
    cam_fwd = _norm(cam_focal - cam_pos)
    cam_dist = np.linalg.norm(cam_pos - cam_focal)

    print(f"[LABEL_DBG] cam_dist={cam_dist:.1f} "
          f"MID_THRESHOLD={config['MID_THRESHOLD']} "
          f"FAR_THRESHOLD={config['FAR_THRESHOLD']} "
          f"→ {'SURFACE' if cam_dist <= config['MID_THRESHOLD'] else 'FLAGPOLE' if cam_dist <= config['FAR_THRESHOLD'] else 'BILLBOARD'}")

    max_labels = config["MAX_VISIBLE_LABELS"]

    ch_by_name = {ch["marker_name"]: ch for ch in channels}

    actors = []
    placed_rects = []
    counts = {"SURFACE": 0, "FLAGPOLE": 0, "BILLBOARD": 0,
              "coloc": 0, "ix": 0, "nudged": 0, "rejected": 0}

    # ================================================================
    # PASS 1: Co-localization labels (highest priority)
    # ================================================================

    visible_composites = get_visible_regions(composite_regions, cam_pos, cam_fwd,
                                              max_labels, renderer)

    for region in visible_composites:
        anchor_pos, anchor_normal = _snap_to_surface(region, channels, cam_pos, cam_fwd)
        if anchor_pos is None:
            counts["rejected"] += 1
            continue
        if _is_occluded(anchor_pos, region, channels, cam_pos):
            counts["rejected"] += 1
            continue

        label_type = pick_label_type(region, cam_dist)

        # --- Main co-loc label ---
        if label_type == "SURFACE":
            rect = _estimate_screen_rect(renderer, anchor_pos, region.label_text, "SURFACE")
            if _any_overlap(rect, placed_rects):
                counts["rejected"] += 1
                continue
            placed_rects.append(rect)
            primary_ch = ch_by_name.get(region.channels[0])
            if primary_ch:
                actors.extend(render_surface(region, anchor_pos, anchor_normal,
                                              primary_ch, cam_pos, cam_up, cam_fwd, renderer))
            else:
                actors.extend(render_flagpole(region, anchor_pos, anchor_normal,
                                              camera, renderer))
            counts["SURFACE"] += 1
        else:
            label_pos = _compute_label_position(label_type, anchor_pos, anchor_normal, cam_pos)
            rect = _estimate_screen_rect(renderer, label_pos, region.label_text, label_type)
            if _any_overlap(rect, placed_rects):
                nudged_rect, (dx, dy) = _try_nudge(rect, placed_rects)
                if nudged_rect is None:
                    counts["rejected"] += 1
                    continue
                rect = nudged_rect
                anchor_pos = anchor_pos + _screen_offset_to_world(renderer, anchor_pos, dx, dy)
                counts["nudged"] += 1
            placed_rects.append(rect)
            if label_type == "BILLBOARD":
                actors.extend(render_billboard(region, anchor_pos, anchor_normal,
                                               camera, renderer))
                counts["BILLBOARD"] += 1
            else:
                actors.extend(render_flagpole(region, anchor_pos, anchor_normal,
                                               camera, renderer))
                counts["FLAGPOLE"] += 1

        counts["coloc"] += 1

        # --- Individual marker flagpoles alongside the co-loc label ---
        # Always placed regardless of zoom level.
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

    # ================================================================
    # PASS 2: Interaction labels
    # ================================================================

    if interactions:
        ix_actors = place_interaction_labels(interactions, placed_rects, renderer)
        actors.extend(ix_actors)
        counts["ix"] = len(ix_actors)

    # ================================================================
    # PASS 3: Single-channel regions (skip those in co-locs)
    # ================================================================

    non_coloc_singles = [r for r in single_regions if not r.has_coloc]
    visible_singles = get_visible_regions(non_coloc_singles, cam_pos, cam_fwd,
                                           max_labels, renderer)

    for region in visible_singles:
        label_type = pick_label_type(region, cam_dist)
        anchor_pos, anchor_normal = _snap_to_surface(region, channels, cam_pos, cam_fwd)

        if anchor_pos is None or _is_occluded(anchor_pos, region, channels, cam_pos):
            # No valid surface point — force flagpole/billboard, never surface.
            # The fabricated centroid anchor is in empty space; render_surface
            # would fail the dilated projection ray and fall back to flagpole anyway.
            to_cam = _norm(cam_pos - region.centroid)
            anchor_pos = region.centroid + to_cam * 2.0
            anchor_normal = to_cam
            if label_type == "SURFACE":
                label_type = "FLAGPOLE"

        if label_type == "SURFACE":
            rect = _estimate_screen_rect(renderer, anchor_pos, region.label_text, "SURFACE")
            if _any_overlap(rect, placed_rects):
                counts["rejected"] += 1
                continue
            placed_rects.append(rect)
            ch = ch_by_name.get(region.channel)
            if ch:
                actors.extend(render_surface(region, anchor_pos, anchor_normal,
                                              ch, cam_pos, cam_up, cam_fwd, renderer))
            else:
                actors.extend(render_flagpole(region, anchor_pos, anchor_normal,
                                              camera, renderer))
            counts["SURFACE"] += 1
        else:
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
                counts["nudged"] += 1

            placed_rects.append(rect)
            if label_type == "BILLBOARD":
                actors.extend(render_billboard(region, anchor_pos, anchor_normal,
                                               camera, renderer))
                counts["BILLBOARD"] += 1
            else:
                actors.extend(render_flagpole(region, anchor_pos, anchor_normal,
                                               camera, renderer))
                counts["FLAGPOLE"] += 1

    print(f"[label_placement] dist={cam_dist:.1f} "
          f"S={counts['SURFACE']} F={counts['FLAGPOLE']} B={counts['BILLBOARD']} "
          f"coloc={counts['coloc']} ix={counts['ix']} "
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
    m.ScalarVisibilityOff()
    f.SetMapper(m)
    f.SetCamera(camera)
    f.SetScale(scale, scale, scale)
    f.SetPosition(pos.tolist())
    _style_label_actor(f, (r, g, b))
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
    tm.ScalarVisibilityOff()
    f.SetMapper(tm)
    f.SetCamera(camera)
    f.SetScale(scale, scale, scale)
    f.SetPosition(top.tolist())
    _style_label_actor(f, (r, g, b))
    f.SetPickable(False)
    renderer.AddActor(f)
    actors.append(f)

    return actors


def _cluster_interactions(interactions, cluster_radius):
    """Group interactions by key, then greedy radius-based spatial clustering.

    Returns list of dicts representing one clustered interaction site:
        {label_text, centroid, median_gap, normal, n_members, key}
    """
    from collections import defaultdict

    by_key = defaultdict(list)
    for ix in interactions:
        if ix.label_text is not None:
            by_key[ix.key].append(ix)

    clustered = []

    for key, members in by_key.items():
        assigned = [False] * len(members)

        for i in range(len(members)):
            if assigned[i]:
                continue

            cluster = [members[i]]
            assigned[i] = True

            changed = True
            while changed:
                changed = False
                for j in range(len(members)):
                    if assigned[j]:
                        continue
                    for cm in cluster:
                        if np.linalg.norm(members[j].midpoint - cm.midpoint) < cluster_radius:
                            cluster.append(members[j])
                            assigned[j] = True
                            changed = True
                            break

            all_points = []
            for ix in cluster:
                all_points.append(ix.closest_a)
                all_points.append(ix.closest_b)
            all_points = np.array(all_points)
            centroid = all_points.mean(axis=0)

            gaps = sorted(ix.gap for ix in cluster)
            median_gap = gaps[len(gaps) // 2]

            avg_n = np.mean([ix.normal for ix in cluster], axis=0)
            nl = np.linalg.norm(avg_n)
            normal = avg_n / nl if nl > 1e-9 else np.array([0.0, 0.0, 1.0])

            clustered.append({
                "label_text": cluster[0].label_text,
                "centroid": centroid,
                "median_gap": median_gap,
                "normal": normal,
                "n_members": len(cluster),
                "key": key,
            })

    return clustered


def place_interaction_labels(interactions, placed_rects, renderer):
    """Place interaction labels with spatial clustering and nudging."""
    from .interactions import Interaction as _Interaction

    camera = renderer.GetActiveCamera()
    cam_pos = np.array(camera.GetPosition(), dtype=np.float64)
    cam_focal = np.array(camera.GetFocalPoint(), dtype=np.float64)
    cam_fwd = _norm(cam_focal - cam_pos)

    min_gap = config.get("INTERACTION_BILLBOARD_MIN_GAP", 2.0)
    max_labels = config.get("MAX_INTERACTION_LABELS", 8)
    cluster_radius = config.get("INTERACTION_CLUSTER_RADIUS", 6.0)

    clusters = _cluster_interactions(interactions, cluster_radius)
    clusters.sort(key=lambda c: -c["n_members"])
    clusters = clusters[:max_labels]

    actors = []
    n_billboard = n_flagpole = n_nudged = n_rejected = 0

    for cl in clusters:
        centroid = cl["centroid"]

        if np.dot(centroid - cam_pos, cam_fwd) <= 0:
            continue
        if not _is_in_viewport(renderer, centroid):
            continue

        if cl["median_gap"] >= min_gap:
            label_type = "BILLBOARD"
            to_cam = _norm(cam_pos - centroid)
            label_pos = centroid + to_cam * config.get("INTERACTION_BILLBOARD_OFFSET", 2.0)
        else:
            label_type = "FLAGPOLE"
            to_cam = _norm(cam_pos - centroid)
            pole_dir = _norm(cl["normal"] + to_cam)
            label_pos = centroid + pole_dir * config.get("INTERACTION_FLAGPOLE_HEIGHT", 3.0)

        rect = _estimate_screen_rect(renderer, label_pos, cl["label_text"], label_type)

        if _any_overlap(rect, placed_rects):
            nudged_rect, (dx, dy) = _try_nudge(rect, placed_rects)
            if nudged_rect is None:
                n_rejected += 1
                continue
            rect = nudged_rect
            n_nudged += 1

        placed_rects.append(rect)

        merged_ix = _Interaction(
            midpoint=centroid,
            gap=cl["median_gap"],
            axis=np.array([0.0, 0.0, 1.0]),
            normal=cl["normal"],
            key=cl["key"],
            label_text=cl["label_text"],
            closest_a=centroid,
            closest_b=centroid,
        )

        if cl["median_gap"] >= min_gap:
            actors.extend(render_interaction_billboard(merged_ix, camera, renderer))
            n_billboard += 1
        else:
            actors.extend(render_interaction_flagpole(merged_ix, camera, renderer))
            n_flagpole += 1

    print(f"[label_placement] interactions: {len(interactions)} raw -> {len(clusters)} clusters, "
          f"billboard={n_billboard} flagpole={n_flagpole} "
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

    base_scale = config.get("CLUSTER_LABEL_BASE_SCALE", 1.0)
    extent_factor = config.get("CLUSTER_EXTENT_SCALE_FACTOR", 50.0)
    offset = config.get("CLUSTER_LABEL_OFFSET", 5.0)
    r, g, b = config.get("CLUSTER_LABEL_COLOR", (1.0, 1.0, 0.8))

    scale = base_scale * (1.0 + cluster.extent / extent_factor)
    to_camera = _norm(cam_pos - cluster.centroid)
    pos = cluster.centroid + to_camera * offset

    f = vtkFollower()
    m = vtkPolyDataMapper()
    m.SetInputData(text_pd)
    m.ScalarVisibilityOff()
    f.SetMapper(m)
    f.SetCamera(camera)
    f.SetScale(scale, scale, scale)
    f.SetPosition(pos.tolist())
    _style_label_actor(f, (r, g, b))
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
                                                 channels, renderer,
                                                 interactions=interactions,
                                                 label_lookup_fn=label_lookup_fn)
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
        if not _is_in_viewport(renderer, cl.centroid):
            continue
        d = np.linalg.norm(cl.centroid - cam_pos)
        visible.append((d, cl))
    visible.sort(key=lambda x: x[0])
    visible = [cl for _, cl in visible[:max_labels]]

    actors = []
    placed_rects = []
    n_placed = n_nudged = n_rejected = 0

    for cl in visible:
        cam_up_cl = _norm(np.array(camera.GetViewUp(), dtype=np.float64))
        cam_right_cl = _norm(np.cross(cam_fwd, cam_up_cl))
        to_camera = _norm(cam_pos - cl.centroid)
        cluster_offset = config.get("CLUSTER_LABEL_OFFSET", 5.0)
        label_pos = cl.centroid + to_camera * cluster_offset

        cx, cy = _world_to_screen(renderer, label_pos)
        offset_pt = label_pos + cam_right_cl * (cl.extent / 2)
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