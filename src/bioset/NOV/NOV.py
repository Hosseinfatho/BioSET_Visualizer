# NOV.py
"""NOV (Next Best View) UI callbacks: toggle panel, prev/next candidate, recompute scores.
   Register with register_nov_callbacks(ctrl, state, _refs).
"""

from __future__ import annotations

import math
import time

from . import get_nov_sphere_points, camera_position_from_sphere, view_up_for_sphere_point
from .mesh_score import compute_view_score_mesh, normalize_scores
from bioset.streaming.lod import camera_distance_to_focal, choose_component


def build_nov_sphere_svg(sphere_xy, current_index):
    """Build SVG string for 5 positions on sphere; current_index is highlighted."""
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
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def register_nov_callbacks(ctrl, state, _refs):
    """Register all nov_* and _nov_* methods on ctrl. Uses state and _refs."""

    def _nov_apply_camera(camera_dict):
        """Apply camera dict (position, focalPoint, viewUp) to streamer and refresh view."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None) or not streamer.renderer:
            return
        c = camera_dict.get("camera") or camera_dict
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

    def _build_nov_sphere_svg(sphere_xy, current_index):
        return build_nov_sphere_svg(sphere_xy, current_index)

    def _nov_get_scene_lod(streamer):
        """Return (desired_comp, active_channel_ids) from streamer scene state or camera distance."""
        active_channel_ids = list(streamer.get_active_channels()) if streamer else []
        desired_comp = None
        if active_channel_ids:
            for ch_id in active_channel_ids:
                st = getattr(streamer, "state", None) and streamer.state.get(ch_id)
                if st is not None:
                    desired_comp = st.component
                    break
        if desired_comp is None and streamer and getattr(streamer, "renderer", None):
            cam = streamer.renderer.GetActiveCamera()
            radius = camera_distance_to_focal(cam)
            desired_comp = choose_component(
                radius,
                streamer.cfg.distance_rules,
                min_component=streamer.cfg.min_component,
                max_component=streamer.cfg.max_component,
            )
        return desired_comp, active_channel_ids

    def nov_toggle():
        """Compute 8 NOV candidates (sphere points), score by visible ROI, show panel and apply best view.
        Uses current scene state: active channels, LOD component, and camera from streamer."""
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
        desired_comp, active_channel_ids = _nov_get_scene_lod(streamer)
        points = get_nov_sphere_points()
        n_points = len(points)
        if desired_comp is None:
            print(f"[NOV] Start: focal={focal}, radius={radius:.1f}, {n_points} candidates (no scene state -> LOD from distance)")
        else:
            print(f"[NOV] Start: focal={focal}, radius={radius:.1f}, {n_points} candidates (scene state: comp={desired_comp}, active_channels={active_channel_ids})")
        zdim, ydim, xdim = streamer._dims_for_component(desired_comp)
        bounds = streamer._volume_bounds_world(desired_comp)
        num_channels = max(1, len(active_channel_ids))
        print(f"[NOV] LOD component={desired_comp}, dims=({xdim}, {ydim}, {zdim}), mesh scoring (visibility=0.8, occlusion=0.2)")

        raw_scores = []
        candidates = []
        for i, (theta_deg, phi_deg) in enumerate(points):
            pos = camera_position_from_sphere(focal, radius, theta_deg, phi_deg)
            view_up = view_up_for_sphere_point(theta_deg, phi_deg)
            score_raw = compute_view_score_mesh(
                camera_pos=(pos[0], pos[1], pos[2]),
                focal=(focal[0], focal[1], focal[2]),
                view_up=(view_up[0], view_up[1], view_up[2]),
                radius=radius,
                bounds_world=bounds,
                num_channels=num_channels,
            )
            raw_scores.append(score_raw)
            candidates.append({
                "camera": {
                    "position": pos,
                    "focalPoint": focal,
                    "viewUp": view_up,
                },
                "score_raw": score_raw,
                "theta_deg": theta_deg,
                "phi_deg": phi_deg,
                "fixed_index": i,
            })
            print(f"[NOV]   candidate {i+1}/{n_points} theta={theta_deg} phi={phi_deg} score={score_raw:.3f} (mesh)")
        normed = normalize_scores(raw_scores)
        for i, c in enumerate(candidates):
            c["score_normalized"] = normed[i] if i < len(normed) else 0.0
        candidates.sort(key=lambda x: x["score_normalized"], reverse=True)
        best_score = candidates[0]["score_normalized"] if candidates else 0.0
        state.nov_candidates = candidates
        state.nov_current_index = 0
        r_svg, cx_svg, cy_svg = 22, 28, 28
        sphere_xy = []
        for theta_deg, phi_deg in points:
            th = math.radians(theta_deg)
            ph = math.radians(phi_deg)
            x = math.sin(th) * math.sin(ph)
            y = math.cos(th)
            sphere_xy.append([round(cx_svg + r_svg * x, 1), round(cy_svg - r_svg * y, 1)])
        state.nov_sphere_xy = sphere_xy
        active_fixed = candidates[0]["fixed_index"] if candidates else 0
        state.nov_sphere_svg = _build_nov_sphere_svg(sphere_xy, active_fixed)
        state.nov_score_display = candidates[0]["score_normalized"] if candidates else 0.0
        state.nov_view_index_display = f"1/{len(candidates)}" if candidates else "0/5"
        state.nov_panel_visible = True
        print("[NOV] Scores (1/5=top .. 5/5=lowest):")
        for rank, c in enumerate(candidates, 1):
            print(f"[NOV]   #{rank}  score_raw={c['score_raw']:.2f}  score_norm={c['score_normalized']:.2f}")
        if candidates:
            _nov_apply_camera(candidates[0])
        elapsed = time.perf_counter() - t0
        print(f"[NOV] Done: best score={best_score:.2f}, applied view 1/{len(candidates)}, elapsed={elapsed:.2f}s")
        if _refs.get("view"):
            _refs["view"].update()

    def _nov_recompute_scores():
        """Recompute NOV scores for current candidates using only active channels in the scene. Keeps current view (by fixed_index)."""
        streamer = _refs.get("streamer")
        candidates = getattr(state, "nov_candidates", []) or []
        if not streamer or not candidates:
            return
        desired_comp, active_channel_ids = _nov_get_scene_lod(streamer)
        num_channels = max(1, len(active_channel_ids))
        bounds = streamer._volume_bounds_world(desired_comp)
        current_idx = getattr(state, "nov_current_index", 0)
        current_fixed = candidates[current_idx]["fixed_index"] if current_idx < len(candidates) else 0
        raw_scores = []
        for c in candidates:
            pos = c["camera"]["position"]
            focal = c["camera"]["focalPoint"]
            view_up = c["camera"]["viewUp"]
            radius = math.sqrt(
                (pos[0] - focal[0]) ** 2 + (pos[1] - focal[1]) ** 2 + (pos[2] - focal[2]) ** 2
            )
            if radius < 1e-6:
                radius = 1.0
            score_raw = compute_view_score_mesh(
                camera_pos=(pos[0], pos[1], pos[2]),
                focal=(focal[0], focal[1], focal[2]),
                view_up=(view_up[0], view_up[1], view_up[2]),
                radius=radius,
                bounds_world=bounds,
                num_channels=num_channels,
            )
            raw_scores.append(score_raw)
            c["score_raw"] = score_raw
        normed = normalize_scores(raw_scores)
        for i, c in enumerate(candidates):
            c["score_normalized"] = normed[i] if i < len(normed) else 0.0
        candidates.sort(key=lambda x: x["score_normalized"], reverse=True)
        state.nov_candidates = candidates
        new_idx = next((k for k, c in enumerate(candidates) if c["fixed_index"] == current_fixed), 0)
        state.nov_current_index = new_idx
        state.nov_sphere_svg = _build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), current_fixed)
        state.nov_score_display = candidates[new_idx]["score_normalized"]
        state.nov_view_index_display = f"{new_idx + 1}/{len(candidates)}"
        print(f"[NOV] Scores updated (active_channels={len(active_channel_ids)}): 1/5=top .. {len(candidates)}/5=lowest")

    def nov_recompute_scores_if_visible():
        """If NOV panel is open, recompute scores from current active channels (and optionally range)."""
        if getattr(state, "nov_panel_visible", False):
            _nov_recompute_scores()

    def _nov_switch(step, label):
        """Switch to previous (step=-1) or next (step=+1) NOV candidate and update display."""
        candidates = getattr(state, "nov_candidates", []) or []
        if not candidates:
            print(f"[NOV] {label}: no candidates")
            return
        idx = (getattr(state, "nov_current_index", 0) + step) % len(candidates)
        state.nov_current_index = idx
        active_fixed = candidates[idx]["fixed_index"]
        state.nov_sphere_svg = _build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), active_fixed)
        _nov_apply_camera(candidates[idx])
        state.nov_score_display = candidates[idx]["score_normalized"]
        state.nov_view_index_display = f"{idx + 1}/{len(candidates)}"
        print(f"[NOV] {label} -> view {idx + 1}/{len(candidates)} score={candidates[idx]['score_normalized']:.2f}")
        if _refs.get("view"):
            _refs["view"].update()

    def nov_prev():
        """Switch to previous NOV candidate and update score display."""
        _nov_switch(-1, "Prev")

    def nov_next():
        """Switch to next NOV candidate and update score display."""
        _nov_switch(1, "Next")

    # Attach to ctrl
    ctrl.nov_toggle = nov_toggle
    ctrl.nov_prev = nov_prev
    ctrl.nov_next = nov_next
    ctrl.nov_recompute_scores_if_visible = nov_recompute_scores_if_visible
