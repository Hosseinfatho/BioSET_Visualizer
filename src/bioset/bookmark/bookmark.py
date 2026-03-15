# bookmark.py
"""Bookmark UI callbacks: view snapshots, per-dataset recordings, export screenshot.
   Register with register_bookmark_callbacks(ctrl, state, _refs).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from .snapshot_io import (
    load_snapshot_by_name,
    save_snapshot,
    snapshot_names,
    snapshot_categories,
    load_snapshots_by_category,
    delete_snapshot_by_name,
    delete_snapshot_in_category,
    save_screenshot,
    thumbnail_path,
    thumbnail_path_or_fallback,
    save_thumbnail,
)
from .ov_snapshot_io import (
    ov_save_snapshot,
    ov_snapshot_names,
    ov_snapshot_categories,
    ov_load_snapshot_by_name,
    ov_load_snapshots_by_category,
)
from bioset.scene.volumes import build_tf_with_range


def capture_screenshot_png_bytes(streamer):
    """Capture VTK view as PNG bytes (scene only, no UI). Returns bytes or None.
    Standalone for use by callbacks.capture_screenshot and bookmark export."""
    try:
        import vtk
        from vtk.util.numpy_support import vtk_to_numpy
        if not streamer or not hasattr(streamer, "renderer"):
            return None
        rw = streamer.renderer.GetRenderWindow()
        rw.Render()
        w2i = vtk.vtkWindowToImageFilter()
        w2i.SetInput(rw)
        w2i.SetScale(1)
        w2i.SetInputBufferTypeToRGB()
        w2i.ReadFrontBufferOff()
        w2i.Update()
        writer = vtk.vtkPNGWriter()
        writer.SetWriteToMemory(True)
        writer.SetInputConnection(w2i.GetOutputPort())
        writer.Write()
        result = writer.GetResult()
        if result and result.GetNumberOfTuples() > 0:
            return vtk_to_numpy(result).tobytes()
        return None
    except Exception:
        return None


def _add_caption_to_png(png_bytes: bytes, caption: str) -> bytes:
    """Add white caption at bottom of image. Returns PNG bytes. Falls back to original if Pillow missing."""
    if not (caption or "").strip():
        return png_bytes
    try:
        import io
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        w, h = img.size
        pad = 14
        font = ImageFont.load_default()
        for path in ("arial.ttf", "Arial.ttf", "C:/Windows/Fonts/arial.ttf"):
            try:
                font = ImageFont.truetype(path, 20)
                break
            except Exception:
                continue
        lines = (caption or "").strip().replace("\r", "").split("\n")[:10]
        line_h = 26
        cap_h = min(len(lines) * line_h + pad * 2, 280)
        out = Image.new("RGB", (w, h + cap_h), (0, 0, 0))
        out.paste(img, (0, 0))
        draw = ImageDraw.Draw(out)
        y = h + pad
        for line in lines[:8]:
            if line.strip():
                draw.text((pad, y), line.strip()[:120], fill=(255, 255, 255), font=font)
            y += line_h
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return png_bytes


def register_bookmark_callbacks(ctrl, state, _refs):
    """Register all bookmark_* and _bookmark_* methods on ctrl. Uses state and _refs."""

    def _bookmark_dataset_id():
        return getattr(state, "bookmark_dataset_id", None) or "default"

    def _bookmark_merge_channels(restored, all_channels_list):
        """Merge snapshot channels with all dataset channels so user can add new channels in bookmark view."""
        from bioset.ui.state import get_channel_color
        if not all_channels_list:
            return restored
        by_id = {ch.get("id"): dict(ch) for ch in restored if ch.get("id") is not None}
        for ch in all_channels_list:
            ch = dict(ch) if isinstance(ch, dict) else {}
            cid = ch.get("id")
            if cid is not None and cid not in by_id:
                by_id[cid] = {
                    "id": cid,
                    "name": ch.get("name") or f"Channel {cid}",
                    "color": ch.get("color") or get_channel_color(cid),
                    "range": ch.get("range") if ch.get("range") and len(ch.get("range", [])) >= 2 else [0, 100],
                }
        return sorted(by_id.values(), key=lambda c: (0 if c.get("id") is not None else 1, c.get("id") or 0))

    def _normalize_channels(channels_list):
        """Build list of channel dicts with id, name, color, range from view/snapshot channels."""
        out = []
        for c in channels_list or []:
            r = c.get("range") or [0, 100]
            if not isinstance(r, (list, tuple)) or len(r) < 2:
                r = [0, 100]
            out.append({
                "id": c.get("id"),
                "name": c.get("name") or f"Channel {c.get('id')}",
                "color": c.get("color", "#FFFFFF"),
                "range": [float(r[0]), float(r[1])],
            })
        return out

    def _apply_camera(streamer, c):
        """Apply camera dict (position, focalPoint, viewUp) to streamer and refresh view."""
        if not streamer or not getattr(streamer, "renderer", None) or not streamer.renderer or not c:
            return
        cam = streamer.renderer.GetActiveCamera()
        if c.get("position") and len(c.get("position", [])) >= 3:
            cam.SetPosition(c["position"][:3])
        if c.get("focalPoint") and len(c.get("focalPoint", [])) >= 3:
            cam.SetFocalPoint(c["focalPoint"][:3])
        if c.get("viewUp") and len(c.get("viewUp", [])) >= 3:
            cam.SetViewUp(c["viewUp"][:3])
        streamer.renderer.ResetCameraClippingRange()
        if _refs.get("view"):
            _refs["view"].update()

    def bookmark_camera_animation_tick():
        """Run each frame: deferred re-apply of bookmark camera, or smooth animation (20 steps) from current to JSON camera."""
        import time
        streamer = _refs.get("streamer")
        if not streamer or not streamer.renderer:
            return
        once = _refs.get("bookmark_camera_apply_once")
        if once and (time.time() - once["set_at"]) > 0.4:
            _apply_camera(streamer, once["camera"])
            _refs.pop("bookmark_camera_apply_once", None)
            if _refs.get("view"):
                _refs["view"].update()
            return
        anim = _refs.get("bookmark_camera_animate")
        if not anim:
            return
        elapsed = time.time() - anim["start_time"]
        duration = max(anim.get("duration", 20.0), 0.01)
        if elapsed >= duration:
            _apply_camera(streamer, anim["end"])
            _refs.pop("bookmark_camera_animate", None)
            if _refs.get("view"):
                _refs["view"].update()
            if getattr(streamer, "on_interaction_end", None):
                try:
                    streamer.on_interaction_end()
                except Exception:
                    pass
            import time as _time
            _refs["bookmark_camera_apply_once"] = {"camera": anim["end"], "set_at": _time.time()}
            return
        t = min(1.0, elapsed / duration)
        start = anim["start"]
        end = anim["end"]
        pos = [start["position"][i] + t * (end["position"][i] - start["position"][i]) for i in range(3)]
        focal = [start["focalPoint"][i] + t * (end["focalPoint"][i] - start["focalPoint"][i]) for i in range(3)]
        viewup = [start["viewUp"][i] + t * (end["viewUp"][i] - start["viewUp"][i]) for i in range(3)]
        _apply_camera(streamer, {"position": pos, "focalPoint": focal, "viewUp": viewup})

    def _apply_nov_view(streamer, nov_data):
        """Apply NOV view: set lens, sync volumes, apply camera to nov_renderer, open popup."""
        if not streamer or not getattr(streamer, "nov_renderer", None) or not nov_data:
            return
        lens_center = nov_data.get("lens_center") or nov_data.get("box_center")
        lens_length = nov_data.get("lens_length", nov_data.get("box_length", 0))
        lens_width = nov_data.get("lens_width", nov_data.get("box_width", 0))
        lens_depth = nov_data.get("lens_depth", nov_data.get("box_depth", 0))
        if not lens_center or len(lens_center) < 3 or lens_length <= 0 or lens_width <= 0 or lens_depth <= 0:
            return
        state.nov_lens_center = list(lens_center)
        state.nov_lens_length = float(lens_length)
        state.nov_lens_width = float(lens_width)
        state.nov_lens_depth = float(lens_depth)
        state.nov_panel_visible = True
        state.nov_popup_open = True
        streamer.set_nov_lens_clip(lens_center, lens_length, lens_width, lens_depth)
        if getattr(streamer, "sync_nov_volumes", None):
            streamer.sync_nov_volumes()
        c = nov_data.get("camera") or {}
        ren = streamer.nov_renderer
        cam = ren.GetActiveCamera()
        if c.get("position") and len(c.get("position", [])) >= 3:
            cam.SetPosition(c["position"][:3])
        if c.get("focalPoint") and len(c.get("focalPoint", [])) >= 3:
            cam.SetFocalPoint(c["focalPoint"][:3])
        if c.get("viewUp") and len(c.get("viewUp", [])) >= 3:
            cam.SetViewUp(c["viewUp"][:3])
        ren.ResetCameraClippingRange()
        if getattr(streamer, "nov_render_window", None):
            streamer.nov_render_window.Render()
        if _refs.get("view"):
            _refs["view"].update()
        if _refs.get("nov_view"):
            _refs["nov_view"].update()
        if hasattr(ctrl, "nov_refresh_lens_display"):
            ctrl.nov_refresh_lens_display()

    def _apply_channel_tfs_to_streamer(streamer):
        """Update streamer TFs from state.channels for active_channels."""
        if not streamer:
            return
        for ch in (state.channels or []):
            ch_id = ch.get("id")
            if ch_id is None or ch_id not in state.active_channels:
                continue
            if ch.get("color"):
                streamer._channel_colors[ch_id] = streamer._hex_to_rgb(ch["color"])
            rng = ch.get("range")
            if rng and len(rng) >= 2 and ch_id in getattr(streamer, "_channel_data_range", {}):
                data_range = streamer._channel_data_range[ch_id]
                tint = streamer._channel_colors.get(ch_id, (1, 1, 1))
                pct_bounds = getattr(streamer, "_channel_percentile_bounds", {}).get(
                    ch_id, (data_range[0], data_range[1], data_range[1])
                )
                intensity_range_pct = (float(rng[0]), float(rng[1]))
                color_tf, opacity_tf = build_tf_with_range(
                    data_range, pct_bounds, intensity_range_pct, tint
                )
                streamer._channel_tfs[ch_id] = (color_tf, opacity_tf)
                if ch_id in streamer.volumes:
                    prop = streamer.volumes[ch_id].GetProperty()
                    prop.SetColor(color_tf)
                    prop.SetScalarOpacity(opacity_tf)

    def bookmark_refresh_names():
        """Load snapshot names for current dataset into dropdown."""
        dataset_id = _bookmark_dataset_id()
        names = snapshot_names(dataset_id)
        state.bookmark_snapshot_names = names

    def bookmark_refresh_categories():
        """Load unique categories for current dataset into dropdown."""
        dataset_id = _bookmark_dataset_id()
        state.bookmark_categories = snapshot_categories(dataset_id)
        if not getattr(state, "bookmark_selected_category", None) or state.bookmark_selected_category not in state.bookmark_categories:
            state.bookmark_selected_category = (state.bookmark_categories or ["Uncategorized"])[0]

    def bookmark_refresh_list():
        """Load bookmarks for selected category into bookmark_list_items (name, category, description, thumbnail)."""
        import base64
        dataset_id = _bookmark_dataset_id()
        category = getattr(state, "bookmark_selected_category", None) or "Uncategorized"
        snapshots = load_snapshots_by_category(dataset_id, category)
        items = []
        for s in snapshots:
            title = (s.get("title") or s.get("id") or "").strip() or "Unnamed"
            cat = (s.get("category") or s.get("_folder") or "").strip() or "Uncategorized"
            views = s.get("views") or []
            notes = (views[0].get("notes") or s.get("notes") or "") if views else (s.get("notes") or "")
            if not isinstance(notes, str):
                notes = str(notes or "")[:200]
            else:
                notes = (notes or "")[:200]
            thumb_path = thumbnail_path_or_fallback(cat, title, dataset_id)
            thumbnail = ""
            if thumb_path:
                try:
                    raw = thumb_path.read_bytes()
                    thumbnail = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
                except Exception:
                    pass
            items.append({"name": title, "category": cat, "description": notes, "thumbnail": thumbnail})
        state.bookmark_list_items = items

    def bookmark_open_snapshot(name=None):
        """Open selected snapshot (or by name if given): restore camera, channels, colors, LOD, TF; show description/comment."""
        name = name or getattr(state, "bookmark_selected_name", None) or "Name"
        if not name or not str(name).strip():
            print("[callbacks] Bookmark: no name selected")
            return
        dataset_id = _bookmark_dataset_id()
        snap = load_snapshot_by_name(name, dataset_id)
        if not snap:
            print(f"[callbacks] Bookmark: snapshot not found: {name}")
            return
        streamer = _refs.get("streamer")
        all_channels_before = list(state.channels or [])
        views = snap.get("views")
        if not views or not isinstance(views, list):
            views = [{
                "camera": snap.get("camera") or {},
                "notes": snap.get("notes") or snap.get("description") or "",
                "comments": snap.get("comments") or (snap.get("meta") or {}).get("comments", []),
                "channels": snap.get("channels") or [],
                "active_channels": snap.get("active_channels") or [],
                "background": snap.get("background") or getattr(state, "bg_color", "#000000"),
                "viewport": snap.get("viewport") or {},
                "optional_LOD": snap.get("optional_LOD"),
            }]
        state.bookmark_current_view_index = 0
        v0 = views[0]
        # 1) Load channels and colors first (state.channels, active_channels, visible)
        restored = _normalize_channels(v0.get("channels"))
        state.channels = _bookmark_merge_channels(restored, all_channels_before)
        state.active_channels = list(v0.get("active_channels") or [])
        visible = list(getattr(state, "visible_channel_ids", []) or [])
        for ch_id in state.active_channels:
            if ch_id not in visible:
                visible.append(ch_id)
        state.visible_channel_ids = visible
        if v0.get("background"):
            state.bg_color = v0["background"]
            if hasattr(ctrl, "update_background_color"):
                ctrl.update_background_color(v0["background"])
        # 2) Load LOD (same comp/distance) and channel TF ranges (filter range) from bookmark
        lod = v0.get("optional_LOD") or snap.get("optional_LOD") or {}
        comp_target = lod.get("component")
        roi = lod.get("roi")
        has_lod = comp_target is not None and isinstance(roi, dict)
        if streamer and has_lod and state.active_channels:
            max_comp = getattr(streamer.cfg, "max_component", 6)
            min_comp = getattr(streamer.cfg, "min_component", 0)
            comp_target = max(min_comp, min(max_comp, int(comp_target)))
            for ch_id in list(streamer.get_active_channels()):
                streamer.deactivate_channel(ch_id)
            for ch_id in state.active_channels:
                ch_id = int(ch_id)
                color_hex = "#FFFFFF"
                for ch in state.channels:
                    if ch.get("id") == ch_id:
                        color_hex = ch.get("color") or color_hex
                        break
                streamer.load_channel_at_lod(ch_id, color_hex, comp_target, roi, reset_camera=False)
            _apply_channel_tfs_to_streamer(streamer)
        else:
            if hasattr(ctrl, "update_active_channels"):
                ctrl.update_active_channels(state.active_channels)
            _apply_channel_tfs_to_streamer(streamer)
        # 3) Go to camera position: animate from current (start) to JSON camera (end) in 20 steps, smooth
        camera_data = v0.get("camera") or snap.get("camera") or {}
        if v0.get("nov_view"):
            _apply_nov_view(streamer, v0["nov_view"])
        else:
            if camera_data and (camera_data.get("position") or camera_data.get("focalPoint")):
                import time as _time
                cam = streamer.renderer.GetActiveCamera()
                start_cam = {
                    "position": list(cam.GetPosition()),
                    "focalPoint": list(cam.GetFocalPoint()),
                    "viewUp": list(cam.GetViewUp()),
                }
                end_cam = {
                    "position": [float(x) for x in (camera_data.get("position") or start_cam["position"])[:3]],
                    "focalPoint": [float(x) for x in (camera_data.get("focalPoint") or start_cam["focalPoint"])[:3]],
                    "viewUp": [float(x) for x in (camera_data.get("viewUp") or start_cam["viewUp"])[:3]],
                }
                _refs["bookmark_camera_animate"] = {
                    "start": start_cam,
                    "end": end_cam,
                    "start_time": _time.time(),
                    "duration": 20.0,
                    "num_steps": 20,
                }
        state.bookmark_edit_title = snap.get("title") or ""
        state.bookmark_edit_category = (snap.get("category") or "").strip() or ""
        state.bookmark_edit_description = v0.get("notes") or ""
        state.bookmark_edit_comment = ""
        state.bookmark_form_dialog = False
        state.bookmark_form_minimized = False
        disp = {
            "title": snap.get("title"),
            "category": (snap.get("category") or "").strip() or "",
            "description": v0.get("notes") or "",
            "comments": v0.get("comments", []),
            "created": snap.get("created"),
            "updated": snap.get("updated"),
            "views": views,
        }
        state.bookmark_display_snapshot = None
        state.bookmark_display_snapshot = disp
        try:
            state.flush()
        except Exception:
            pass
        if _refs.get("view"):
            _refs["view"].update()

    def _bookmark_apply_view(v):
        """Apply view to scene: camera, channels, active_channels, background, TF. Updates state and streamer. If nov_view, apply to NOV popup."""
        streamer = _refs.get("streamer")
        if v.get("nov_view"):
            _apply_nov_view(streamer, v["nov_view"])
        else:
            _apply_camera(streamer, v.get("camera") or {})
        if v.get("channels") is not None and v.get("active_channels") is not None:
            restored = _normalize_channels(v.get("channels"))
            state.channels = _bookmark_merge_channels(restored, list(state.channels or []))
            state.active_channels = list(v.get("active_channels") or [])
            visible = list(getattr(state, "visible_channel_ids", []) or [])
            for ch_id in state.active_channels:
                if ch_id not in visible:
                    visible.append(ch_id)
            state.visible_channel_ids = visible
            if hasattr(ctrl, "update_active_channels"):
                ctrl.update_active_channels(state.active_channels)
            _apply_channel_tfs_to_streamer(streamer)
        if v.get("background") and hasattr(ctrl, "update_background_color"):
            state.bg_color = v["background"]
            ctrl.update_background_color(v["background"])
        if _refs.get("view"):
            _refs["view"].update()

    def _bookmark_switch_view(new_idx):
        """Switch to view at new_idx: apply that view to scene and update form."""
        disp = getattr(state, "bookmark_display_snapshot", None)
        views = (disp.get("views") or []) if disp else []
        if new_idx < 0 or new_idx >= len(views) or not views:
            return
        v = views[new_idx]
        _bookmark_apply_view(v)
        state.bookmark_current_view_index = new_idx
        state.bookmark_edit_description = v.get("notes") or ""
        state.bookmark_edit_comment = ""
        state.bookmark_display_snapshot = {
            **disp,
            "description": v.get("notes") or "",
            "comments": v.get("comments", []),
        }

    # === OV bookmark model (Optimal View-only bookmarks) ===

    def ov_bookmark_refresh_categories():
        """Load OV category list into dropdown."""
        dataset_id = _bookmark_dataset_id()
        state.ov_bookmark_categories = ov_snapshot_categories(dataset_id) or ["Uncategorized"]
        if not getattr(state, "ov_bookmark_selected_category", None) or state.ov_bookmark_selected_category not in (state.ov_bookmark_categories or []):
            state.ov_bookmark_selected_category = (state.ov_bookmark_categories or ["Uncategorized"])[0]

    def ov_bookmark_refresh_names():
        """Load OV snapshot names for selected category into OV dropdown."""
        dataset_id = _bookmark_dataset_id()
        category = getattr(state, "ov_bookmark_selected_category", None) or "Uncategorized"
        names = ov_snapshot_names(dataset_id, category=category)
        state.ov_bookmark_snapshot_names = names or []

    def _ov_bookmark_capture_view():
        """Capture NOV popup camera and lens only (Optimal View)."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "nov_renderer", None):
            return {}
        cam = streamer.nov_renderer.GetActiveCamera()
        camera = {
            "position": list(cam.GetPosition()),
            "focalPoint": list(cam.GetFocalPoint()),
            "viewUp": list(cam.GetViewUp()),
        }
        nov_view = None
        if getattr(state, "nov_lens_center", None) and len(state.nov_lens_center or []) >= 3:
            nov_view = {
                "camera": dict(camera),
                "lens_center": list(state.nov_lens_center),
                "lens_length": float(getattr(state, "nov_lens_length", 0)),
                "lens_width": float(getattr(state, "nov_lens_width", 0)),
                "lens_depth": float(getattr(state, "nov_lens_depth", 0)),
            }
        return {
            "camera": camera,
            "nov_view": nov_view,
        }

    def ov_bookmark_save_current():
        """Save current NOV popup view as OV snapshot in selected category folder."""
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cap = _ov_bookmark_capture_view()
        if not cap or not cap.get("camera"):
            print("[ov_bookmark] No NOV camera to save")
            return
        raw = getattr(state, "ov_bookmark_selected_name", "") or ""
        title = str(raw).strip() or "OV_view"
        category = (getattr(state, "ov_bookmark_selected_category", None) or "").strip() or "Uncategorized"
        snapshot = {
            "id": f"ov_{uuid.uuid4()}",
            "title": title,
            "category": category,
            "created": now,
            "updated": now,
            "notes": "",
            "camera": cap["camera"],
            "views": [],
        }
        if cap.get("nov_view"):
            snapshot["nov_view"] = cap["nov_view"]
            snapshot["views"] = [
                {
                    "camera": cap["camera"],
                    "nov_view": cap["nov_view"],
                    "notes": "",
                    "comments": [],
                }
            ]
        dataset_id = _bookmark_dataset_id()
        ov_save_snapshot(snapshot, dataset_id)
        ov_bookmark_refresh_categories()
        ov_bookmark_refresh_names()
        state.ov_bookmark_selected_name = title

    def ov_bookmark_open_selected():
        """Open selected OV snapshot: apply NOV camera and lens to popup only."""
        name = (getattr(state, "ov_bookmark_selected_name", "") or "").strip()
        if not name:
            print("[ov_bookmark] No OV name selected")
            return
        dataset_id = _bookmark_dataset_id()
        snap = ov_load_snapshot_by_name(name, dataset_id)
        if not snap:
            print(f"[ov_bookmark] OV snapshot not found: {name}")
            return
        streamer = _refs.get("streamer")
        nov_data = snap.get("nov_view")
        if not nov_data:
            # Try to take from first view if present
            views = snap.get("views") or []
            if views and isinstance(views[0], dict):
                nov_data = views[0].get("nov_view")
        if not nov_data:
            print(f"[ov_bookmark] OV snapshot has no nov_view: {name}")
            return
        _apply_nov_view(streamer, nov_data)

    def ov_bookmark_hide_flags():
        """Remove OV flag actors from NOV renderer and unregister picker."""
        streamer = _refs.get("streamer")
        actors_data = _refs.get("ov_bookmark_flag_actors") or []
        for actor, _title, _popup_data in actors_data:
            if streamer and getattr(streamer, "nov_renderer", None) and streamer.nov_renderer.HasViewProp(actor):
                streamer.nov_renderer.RemoveActor(actor)
        _refs["ov_bookmark_flag_actors"] = []
        nov_win = getattr(streamer, "nov_render_window", None) if streamer else None
        if nov_win:
            interactor = nov_win.GetInteractor()
            if interactor is not None:
                obs_tag = _refs.get("_ov_bookmark_flag_picker_tag")
                if obs_tag is not None:
                    try:
                        interactor.RemoveObserver(obs_tag)
                    except Exception:
                        pass
                    _refs["_ov_bookmark_flag_picker_tag"] = None
        state.ov_bookmark_flags_visible = False
        state.ov_bookmark_flags_data = []

    def ov_bookmark_show_category_flags():
        """Show OV bookmarks in selected category as flags on NOV view; click=popup, double-click=open."""
        category = getattr(state, "ov_bookmark_selected_category", None) or "Uncategorized"
        dataset_id = _bookmark_dataset_id()
        snapshots = ov_load_snapshots_by_category(dataset_id, category)
        if not snapshots:
            state.ov_bookmark_flags_visible = False
            state.ov_bookmark_flags_data = []
            return
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "nov_renderer", None):
            return
        nov_win = getattr(streamer, "nov_render_window", None)
        if not nov_win:
            return
        interactor = nov_win.GetInteractor()
        if not interactor:
            return
        ov_bookmark_hide_flags()
        try:
            from vtkmodules.vtkFiltersSources import vtkConeSource
            from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper
        except ImportError:
            return
        renderer = streamer.nov_renderer
        flag_height = 24.0
        flag_radius = 12.0
        actors_data = []
        flags_data = []
        for snap in snapshots:
            nov_data = snap.get("nov_view")
            if not nov_data:
                views = snap.get("views") or []
                if views and isinstance(views[0], dict):
                    nov_data = views[0].get("nov_view")
            if not nov_data:
                continue
            fp = nov_data.get("lens_center") or nov_data.get("box_center") or (nov_data.get("camera") or {}).get("focalPoint")
            if not fp or len(fp) < 3:
                continue
            title = snap.get("title") or snap.get("id") or "Unnamed"
            cat = (snap.get("category") or "").strip() or "Uncategorized"
            notes = snap.get("notes") or ""
            popup_data = {"name": title, "category": cat, "channels_active": "—", "description": notes or "—"}
            cone = vtkConeSource()
            cone.SetCenter(fp[0], fp[1], fp[2])
            cone.SetDirection(0.0, 0.0, -1.0)
            cone.SetHeight(flag_height)
            cone.SetRadius(flag_radius)
            cone.SetResolution(16)
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(cone.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(221 / 255.0, 28 / 255.0, 119 / 255.0)
            actor.SetPickable(True)
            renderer.AddActor(actor)
            actors_data.append((actor, title, popup_data))
            flags_data.append(popup_data)
        _refs["ov_bookmark_flag_actors"] = actors_data
        state.ov_bookmark_flags_data = flags_data
        state.ov_bookmark_flags_visible = True

        _ov_last_pick = [None, 0.0]

        def _on_ov_left_click(obj, event):
            from vtkmodules.vtkRenderingCore import vtkPropPicker
            click_pos = obj.GetEventPosition()
            ad = _refs.get("ov_bookmark_flag_actors") or []
            picker = vtkPropPicker()
            picker.PickFromListOn()
            for a, _t, _p in ad:
                picker.AddPickList(a)
            picker.Pick(click_pos[0], click_pos[1], 0, renderer)
            picked = picker.GetActor()
            if not picked:
                return
            for actor, name, popup_data in ad:
                if actor == picked:
                    import time
                    now = time.time()
                    if _ov_last_pick[0] == name and (now - _ov_last_pick[1]) < 0.4:
                        ov_bookmark_hide_flags()
                        state.ov_bookmark_selected_name = name
                        ov_bookmark_open_selected()
                        return
                    _ov_last_pick[0] = name
                    _ov_last_pick[1] = now
                    _esc = lambda x: (x or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    state.bookmark_flag_popup = True
                    state.bookmark_flag_popup_html = (
                        f"<b>Category:</b> {_esc(str(popup_data.get('category') or '—'))}<br>"
                        f"<b>Name:</b> {_esc(str(popup_data.get('name') or '—'))}<br>"
                        f"<b>Channels active:</b> {_esc(str(popup_data.get('channels_active') or '—'))}<br>"
                        f"<b>Description:</b> {_esc(str(popup_data.get('description') or '—'))}"
                    )
                    state.bookmark_flag_popup_left = click_pos[0] + 10
                    state.bookmark_flag_popup_top = click_pos[1] - 8
                    try:
                        state.flush()
                    except Exception:
                        pass
                    break

        tag = interactor.AddObserver("LeftButtonPressEvent", _on_ov_left_click, 1.0)
        _refs["_ov_bookmark_flag_picker_tag"] = tag
        if nov_win:
            nov_win.Render()

    def bookmark_apply_current_view():
        """Apply current view (camera, channels, TF, background) to the scene."""
        disp = getattr(state, "bookmark_display_snapshot", None)
        views = (disp.get("views") or []) if disp else []
        idx = getattr(state, "bookmark_current_view_index", 0)
        if not views or idx < 0 or idx >= len(views):
            return
        _bookmark_apply_view(views[idx])

    def bookmark_close_display():
        state.bookmark_display_snapshot = None
        state.bookmark_form_minimized = False

    def bookmark_close_flag_popup():
        state.bookmark_flag_popup = None
        state.bookmark_flag_popup_html = ""
        state.bookmark_flag_popup_screen = ""
        state.bookmark_flag_popup_left = 0
        state.bookmark_flag_popup_top = 0

    _bookmark_last_pick = [None, 0.0]  # [actor, time] for double-click detection

    def bookmark_hide_flags():
        """Remove bookmark flag actors from the scene and unregister picker."""
        streamer = _refs.get("streamer")
        actors_data = _refs.get("bookmark_flag_actors") or []
        if streamer and streamer.renderer and actors_data:
            for actor, _name, _data in actors_data:
                if actor and streamer.renderer.HasViewProp(actor):
                    streamer.renderer.RemoveActor(actor)
        _refs["bookmark_flag_actors"] = []
        obs_tag = _refs.get("_bookmark_flag_picker_tag")
        interactor = _refs.get("interactor")
        if interactor is not None and obs_tag is not None:
            try:
                interactor.RemoveObserver(obs_tag)
            except Exception:
                pass
        _refs["_bookmark_flag_picker_tag"] = None
        state.bookmark_flags_visible = False
        state.bookmark_flags_data = []
        bookmark_close_flag_popup()
        if _refs.get("view"):
            _refs["view"].update()

    def bookmark_show_category_flags():
        """Show all bookmarks in the selected category as red flags (spheres) on the image; click=popup, double-click=open."""
        category = getattr(state, "bookmark_selected_category", None) or "Uncategorized"
        dataset_id = _bookmark_dataset_id()
        snapshots = load_snapshots_by_category(dataset_id, category)
        if not snapshots:
            state.bookmark_flags_visible = False
            state.bookmark_flags_data = []
            if _refs.get("view"):
                _refs["view"].update()
            return
        streamer = _refs.get("streamer")
        interactor = _refs.get("interactor")
        if not streamer or not streamer.renderer or not interactor:
            return
        bookmark_hide_flags()
        try:
            from vtkmodules.vtkFiltersSources import vtkConeSource
            from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper
        except ImportError:
            return
        renderer = streamer.renderer
        # Cone marker (pin-like), 2x size so clearly visible
        flag_height = 24.0
        flag_radius = 12.0
        actors_data = []
        flags_data = []
        for snap in snapshots:
            views = snap.get("views") or []
            if not views:
                cam = snap.get("camera") or {}
                fp = cam.get("focalPoint")
                if not fp or len(fp) < 3:
                    continue
                view0 = {"camera": cam, "notes": snap.get("notes") or "", "channels": snap.get("channels") or [], "active_channels": snap.get("active_channels") or []}
            else:
                view0 = views[0] if isinstance(views[0], dict) else {}
                cam = view0.get("camera") or {}
                fp = cam.get("focalPoint")
                if not fp or len(fp) < 3:
                    continue
            title = snap.get("title") or snap.get("id") or "Unnamed"
            cat = (snap.get("category") or "").strip() or "Uncategorized"
            notes = view0.get("notes") or snap.get("notes") or ""
            active = view0.get("active_channels") or snap.get("active_channels") or []
            ch_names = []
            for ch in (view0.get("channels") or snap.get("channels") or []):
                if ch.get("id") in active:
                    ch_names.append(ch.get("name") or str(ch.get("id")))
            channels_str = ", ".join(ch_names) if ch_names else "—"
            cone = vtkConeSource()
            cone.SetCenter(fp[0], fp[1], fp[2])
            cone.SetDirection(0.0, 0.0, -1.0)
            cone.SetHeight(flag_height)
            cone.SetRadius(flag_radius)
            cone.SetResolution(16)
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(cone.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(221 / 255.0, 28 / 255.0, 119 / 255.0)  # #dd1c77
            actor.SetPickable(True)
            renderer.AddActor(actor)
            popup_data = {"name": title, "category": cat, "channels_active": channels_str, "description": notes or "—"}
            actors_data.append((actor, title, popup_data))
            flags_data.append(popup_data)
        _refs["bookmark_flag_actors"] = actors_data
        state.bookmark_flags_data = flags_data
        state.bookmark_flags_visible = True

        def _on_left_click(obj, event):
            from vtkmodules.vtkRenderingCore import vtkPropPicker
            click_pos = obj.GetEventPosition()
            ad = _refs.get("bookmark_flag_actors") or []
            if not ad:
                return
            picker = vtkPropPicker()
            picker.PickFromListOn()
            for a, _n, _d in ad:
                picker.AddPickList(a)
            picker.Pick(click_pos[0], click_pos[1], 0, renderer)
            picked_actor = picker.GetActor()
            for actor, name, popup_data in ad:
                if picked_actor is actor:
                    import time
                    now = time.time()
                    last_actor, last_time = _bookmark_last_pick[0], _bookmark_last_pick[1]
                    if last_actor is actor and (now - last_time) < 0.4:
                        _bookmark_last_pick[0] = None
                        _bookmark_last_pick[1] = 0.0
                        bookmark_close_flag_popup()
                        state.bookmark_selected_name = name
                        bookmark_open_snapshot(name)
                        bookmark_hide_flags()
                        if _refs.get("view"):
                            _refs["view"].update()
                    else:
                        _bookmark_last_pick[0] = actor
                        _bookmark_last_pick[1] = now
                        state.bookmark_flag_popup = popup_data
                        import html as html_module
                        _esc = html_module.escape
                        _cat = _esc(str(popup_data.get("category") or "—"))
                        _name = _esc(str(popup_data.get("name") or "—"))
                        _ch = _esc(str(popup_data.get("channels_active") or "—"))
                        _desc = _esc(str(popup_data.get("description") or "—"))
                        state.bookmark_flag_popup_html = (
                            f"<b>Category:</b> {_cat}<br>"
                            f"<b>Name:</b> {_name}<br>"
                            f"<b>Channels active:</b> {_ch}<br>"
                            f"<b>Description:</b> {_desc}"
                        )
                        state.bookmark_flag_popup_screen = f"{click_pos[0]},{click_pos[1]}"
                        state.bookmark_flag_popup_left = click_pos[0] + 10
                        state.bookmark_flag_popup_top = click_pos[1] - 8
                        try:
                            state.flush()
                        except Exception:
                            pass
                    return
            bookmark_close_flag_popup()

        tag = interactor.AddObserver("LeftButtonPressEvent", _on_left_click, 1.0)
        _refs["_bookmark_flag_picker_tag"] = tag
        if _refs.get("view"):
            _refs["view"].update()

    def bookmark_view_prev():
        idx = getattr(state, "bookmark_current_view_index", 0)
        _bookmark_switch_view(idx - 1)

    def bookmark_view_next():
        _bookmark_switch_view(getattr(state, "bookmark_current_view_index", 0) + 1)

    def bookmark_add_view():
        """Add a new view with current camera, channels, TF, background; save to JSON and switch to it."""
        disp = getattr(state, "bookmark_display_snapshot", None)
        if not disp or not disp.get("title"):
            return
        cap = _bookmark_capture_view()
        views = list(disp.get("views") or [])
        new_view = {
            "camera": cap["camera"],
            "notes": "",
            "comments": [],
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap.get("viewport") or {},
            "optional_LOD": cap.get("optional_lod"),
        }
        views.append(new_view)
        state.bookmark_current_view_index = len(views) - 1
        state.bookmark_edit_description = ""
        state.bookmark_edit_comment = ""
        state.bookmark_display_snapshot = {
            **disp,
            "views": views,
            "description": "",
            "comments": [],
        }
        dataset_id = _bookmark_dataset_id()
        snap = load_snapshot_by_name(disp["title"], dataset_id)
        if snap:
            snap["views"] = views
            snap["updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            save_snapshot(snap, dataset_id)
        if _refs.get("view"):
            _refs["view"].update()

    def bookmark_open_new_form():
        """Open the new-snapshot form (bottom-left). Category first, then name, description, comment."""
        state.bookmark_capture_from_nov = False
        bookmark_refresh_categories()
        state.bookmark_form_category = getattr(state, "bookmark_selected_category", "Uncategorized") or "Uncategorized"
        state.bookmark_form_name = getattr(state, "bookmark_selected_name", "Name") or "Name"
        state.bookmark_form_description = ""
        state.bookmark_form_new_comment = ""
        state.bookmark_form_dialog = True

    def bookmark_open_new_form_from_nov():
        """Open the new-snapshot form for saving the current NOV popup view as a bookmark."""
        state.bookmark_capture_from_nov = True
        bookmark_refresh_categories()
        state.bookmark_form_category = getattr(state, "bookmark_selected_category", "Uncategorized") or "Uncategorized"
        state.bookmark_form_name = getattr(state, "bookmark_selected_name", "Name") or "Name"
        state.bookmark_form_description = ""
        state.bookmark_form_new_comment = ""
        state.bookmark_form_dialog = True

    def _bookmark_capture_view():
        """Capture camera, LOD, channels (active only), viewport, background. From main scene or NOV popup if bookmark_capture_from_nov."""
        streamer = _refs.get("streamer")
        capture_nov = getattr(state, "bookmark_capture_from_nov", False)
        use_nov = capture_nov and streamer and getattr(streamer, "nov_renderer", None)
        camera = {}
        if use_nov:
            cam = streamer.nov_renderer.GetActiveCamera()
            camera["position"] = list(cam.GetPosition())
            camera["focalPoint"] = list(cam.GetFocalPoint())
            camera["viewUp"] = list(cam.GetViewUp())
        elif streamer and hasattr(streamer, "renderer") and streamer.renderer:
            cam = streamer.renderer.GetActiveCamera()
            camera["position"] = list(cam.GetPosition())
            camera["focalPoint"] = list(cam.GetFocalPoint())
            camera["viewUp"] = list(cam.GetViewUp())
        optional_lod = None
        if streamer and getattr(streamer, "state", None):
            for ch_id in state.active_channels:
                if ch_id in streamer.state:
                    st = streamer.state[ch_id]
                    optional_lod = {
                        "component": st.component,
                        "roi": {"x0": st.roi.x0, "x1": st.roi.x1, "y0": st.roi.y0, "y1": st.roi.y1},
                    }
                    break
        active = list(state.active_channels) if state.active_channels else []
        channels_data = _normalize_channels(
            [c for c in (state.channels or []) if isinstance(c, dict) and c.get("id") in active]
        )
        viewport = {}
        if streamer and hasattr(streamer, "renderer") and streamer.renderer:
            rw = streamer.renderer.GetRenderWindow()
            if rw:
                w, h = rw.GetSize()
                viewport = {"width": w, "height": h}
        bg = getattr(state, "bg_color", "#000000") or "#000000"
        out = {"camera": camera, "optional_lod": optional_lod, "channels": channels_data, "active_channels": active, "viewport": viewport, "background": bg}
        if use_nov and getattr(state, "nov_lens_center", None) and len(state.nov_lens_center) >= 3:
            out["nov_view"] = {
                "camera": dict(camera),
                "lens_center": list(state.nov_lens_center),
                "lens_length": float(getattr(state, "nov_lens_length", 0)),
                "lens_width": float(getattr(state, "nov_lens_width", 0)),
                "lens_depth": float(getattr(state, "nov_lens_depth", 0)),
            }
        return out

    def bookmark_save_snapshot(form_category=None, form_name=None, form_description=None, form_new_comment=None):
        """Capture current view + form fields; save to bookmark JSON in folder for given category. Form values from client ensure correct category folder."""
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cap = _bookmark_capture_view()
        state.bookmark_capture_from_nov = False
        form_name = (form_name if form_name is not None else getattr(state, "bookmark_form_name", None) or getattr(state, "bookmark_selected_name", None) or "")
        if not isinstance(form_name, str):
            form_name = str(form_name or "")
        title = form_name.strip() or "Unnamed"
        category = (form_category if form_category is not None else getattr(state, "bookmark_form_category", None) or "")
        if not isinstance(category, str):
            category = str(category or "")
        category = category.strip() or "Uncategorized"
        comments = []
        new_comment = form_new_comment if form_new_comment is not None else getattr(state, "bookmark_form_new_comment", "")
        if isinstance(new_comment, str) and new_comment.strip():
            comments.append({"date": now, "text": new_comment.strip()})
        notes = form_description if (form_description is not None and isinstance(form_description, str)) else (getattr(state, "bookmark_form_description", "") or "")
        view0 = {
            "camera": cap["camera"],
            "notes": notes,
            "comments": comments,
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap.get("viewport") or {},
            "optional_LOD": cap.get("optional_lod"),
        }
        if cap.get("nov_view"):
            view0["nov_view"] = cap["nov_view"]
        snapshot = {
            "id": str(uuid.uuid4()),
            "title": title,
            "category": category,
            "created": now,
            "updated": now,
            "notes": notes,
            "camera": cap["camera"],
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap["viewport"],
            "comments": comments,
            "views": [view0],
        }
        if cap["optional_lod"]:
            snapshot["optional_LOD"] = cap["optional_lod"]
        if cap.get("nov_view"):
            snapshot["nov_view"] = cap["nov_view"]
        dataset_id = _bookmark_dataset_id()
        save_snapshot(snapshot, dataset_id)
        # Capture thumbnail from current view and save in same category folder with same name
        streamer = _refs.get("streamer")
        if streamer:
            png_bytes = capture_screenshot_png_bytes(streamer)
            if png_bytes:
                save_thumbnail(png_bytes, category, title, dataset_id)
        bookmark_refresh_names()
        bookmark_refresh_categories()
        bookmark_refresh_list()
        state.bookmark_selected_name = title
        state.bookmark_selected_category = category
        state.bookmark_form_dialog = False
        state.bookmark_edit_title = title
        state.bookmark_edit_description = notes
        state.bookmark_edit_comment = ""
        state.bookmark_current_view_index = 0
        state.bookmark_display_snapshot = {
            "title": title,
            "category": category or "",
            "description": notes,
            "comments": comments,
            "created": now,
            "updated": now,
            "views": [view0],
        }
        if _refs.get("view"):
            _refs["view"].update()

    def bookmark_update_snapshot():
        """Save current view into views list (use state's views so Add-view entries persist), then write JSON."""
        disp = getattr(state, "bookmark_display_snapshot", None)
        if not disp or not disp.get("title"):
            print("[callbacks] Bookmark: no snapshot displayed to update")
            return
        old_title = disp["title"]
        dataset_id = _bookmark_dataset_id()
        snap = load_snapshot_by_name(old_title, dataset_id)
        if not snap:
            print(f"[callbacks] Bookmark: snapshot not found: {old_title}")
            return
        old_category = (snap.get("category") or disp.get("category") or "").strip() or "Uncategorized"
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cap = _bookmark_capture_view()
        snap["updated"] = now
        snap["title"] = (getattr(state, "bookmark_edit_title", "") or "").strip() or old_title
        snap["category"] = (getattr(state, "bookmark_edit_category", "") or "").strip() or "Uncategorized"
        views = list(disp.get("views") or [])
        if not views and snap.get("views"):
            views = list(snap.get("views") or [])
        if not views or not isinstance(views, list):
            views = [{
                "camera": snap.get("camera") or {},
                "notes": snap.get("notes") or "",
                "comments": snap.get("comments") or [],
                "channels": snap.get("channels") or [],
                "active_channels": snap.get("active_channels") or [],
                "background": snap.get("background") or "",
                "viewport": snap.get("viewport") or {},
                "optional_LOD": snap.get("optional_LOD"),
            }]
        idx = getattr(state, "bookmark_current_view_index", 0)
        idx = max(0, min(idx, len(views) - 1))
        notes = getattr(state, "bookmark_edit_description", "") or ""
        new_comment = (getattr(state, "bookmark_edit_comment", "") or "").strip()
        view_comments = list(views[idx].get("comments") or []) if idx < len(views) else []
        if new_comment:
            view_comments.append({"date": now, "text": new_comment})
        view_payload = {
            "camera": cap["camera"],
            "notes": notes,
            "comments": view_comments,
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap.get("viewport") or {},
            "optional_LOD": cap.get("optional_lod"),
        }
        if idx < len(views):
            views[idx] = view_payload
        else:
            views.append(view_payload)
        snap["views"] = views
        current_view = views[idx]
        snap["channels"] = current_view.get("channels") or []
        snap["active_channels"] = current_view.get("active_channels") or []
        snap["background"] = current_view.get("background") or ""
        snap["viewport"] = current_view.get("viewport") or {}
        if current_view.get("optional_LOD"):
            snap["optional_LOD"] = current_view["optional_LOD"]
        else:
            snap.pop("optional_LOD", None)
        snap["camera"] = current_view.get("camera") or {}
        snap["notes"] = current_view.get("notes") or ""
        snap["description"] = snap["notes"]
        snap["comments"] = current_view.get("comments") or []
        new_title = snap["title"]
        new_category = (snap.get("category") or "").strip() or "Uncategorized"
        if (old_title, old_category) != (new_title, new_category):
            delete_snapshot_in_category(old_title, dataset_id, old_category)
        save_snapshot(snap, dataset_id)
        # Update thumbnail from current view
        streamer = _refs.get("streamer")
        if streamer:
            png_bytes = capture_screenshot_png_bytes(streamer)
            if png_bytes:
                save_thumbnail(png_bytes, new_category, new_title, dataset_id)
        bookmark_refresh_names()
        bookmark_refresh_list()
        state.bookmark_selected_name = snap["title"]
        state.bookmark_display_snapshot = {
            "title": snap["title"],
            "category": (snap.get("category") or "").strip() or "",
            "description": notes,
            "comments": view_comments,
            "created": snap.get("created"),
            "updated": snap.get("updated"),
            "views": views,
        }
        state.bookmark_edit_title = snap["title"]
        state.bookmark_edit_category = (snap.get("category") or "").strip() or ""
        state.bookmark_edit_comment = ""

    def bookmark_comment():
        """Append current comment to the current view and save."""
        disp = getattr(state, "bookmark_display_snapshot", None)
        if not disp or not disp.get("title"):
            return
        new_comment = (getattr(state, "bookmark_edit_comment", "") or "").strip()
        if not new_comment:
            return
        title = disp["title"]
        dataset_id = _bookmark_dataset_id()
        snap = load_snapshot_by_name(title, dataset_id)
        if not snap:
            return
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        snap["updated"] = now
        views = snap.get("views") or [{"camera": snap.get("camera"), "notes": snap.get("notes") or "", "comments": snap.get("comments") or []}]
        idx = max(0, min(getattr(state, "bookmark_current_view_index", 0), len(views) - 1))
        views[idx].setdefault("comments", []).append({"date": now, "text": new_comment})
        snap["views"] = views
        snap["comments"] = views[0].get("comments", [])
        save_snapshot(snap, dataset_id)
        state.bookmark_display_snapshot = {**disp, "comments": views[idx].get("comments", []), "updated": now, "views": views}
        state.bookmark_edit_comment = ""

    def _capture_screenshot_png_bytes():
        streamer = _refs.get("streamer")
        return capture_screenshot_png_bytes(streamer)

    def bookmark_open_export_screenshot():
        """Open export screenshot popup: empty name and caption boxes."""
        state.bookmark_export_screenshot_name = ""
        state.bookmark_export_screenshot_caption = ""
        state.bookmark_export_screenshot_dialog = True

    def bookmark_export_screenshot_save():
        """Save screenshot to dataset Screenshot folder with chosen name; add description as caption; close dialog."""
        name = (getattr(state, "bookmark_export_screenshot_name", "") or "").strip()
        if not name:
            return
        png_bytes = _capture_screenshot_png_bytes()
        if not png_bytes:
            return
        caption = getattr(state, "bookmark_export_screenshot_caption", "") or ""
        png_bytes = _add_caption_to_png(png_bytes, caption)
        dataset_id = _bookmark_dataset_id()
        save_screenshot(png_bytes, name, dataset_id)
        state.bookmark_export_screenshot_dialog = False
        state.bookmark_export_screenshot_name = ""
        state.bookmark_export_screenshot_caption = ""
        if _refs.get("view"):
            _refs["view"].update()

    # Attach to ctrl
    ctrl.bookmark_refresh_names = bookmark_refresh_names
    ctrl.bookmark_refresh_categories = bookmark_refresh_categories
    ctrl.bookmark_refresh_list = bookmark_refresh_list
    ctrl.bookmark_open_snapshot = bookmark_open_snapshot
    ctrl.bookmark_open_new_form = bookmark_open_new_form
    ctrl.bookmark_open_new_form_from_nov = bookmark_open_new_form_from_nov
    ctrl.bookmark_save_snapshot = bookmark_save_snapshot
    ctrl.bookmark_view_prev = bookmark_view_prev
    ctrl.bookmark_view_next = bookmark_view_next
    ctrl.bookmark_add_view = bookmark_add_view
    ctrl.bookmark_close_display = bookmark_close_display
    ctrl.bookmark_close_flag_popup = bookmark_close_flag_popup
    ctrl.bookmark_show_category_flags = bookmark_show_category_flags
    ctrl.bookmark_hide_flags = bookmark_hide_flags
    ctrl.bookmark_camera_animation_tick = bookmark_camera_animation_tick
    ctrl.bookmark_apply_current_view = bookmark_apply_current_view
    ctrl.bookmark_open_export_screenshot = bookmark_open_export_screenshot
    ctrl.bookmark_export_screenshot_save = bookmark_export_screenshot_save
    ctrl.bookmark_comment = bookmark_comment
    ctrl.bookmark_update_snapshot = bookmark_update_snapshot
    # OV-specific controls (Optimal View-only bookmarks)
    ctrl.ov_bookmark_refresh_categories = ov_bookmark_refresh_categories
    ctrl.ov_bookmark_refresh_names = ov_bookmark_refresh_names
    ctrl.ov_bookmark_show_category_flags = ov_bookmark_show_category_flags
    ctrl.ov_bookmark_hide_flags = ov_bookmark_hide_flags
    ctrl.ov_bookmark_save_current = ov_bookmark_save_current
    ctrl.ov_bookmark_open_selected = ov_bookmark_open_selected
