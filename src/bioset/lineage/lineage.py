# lineage.py
"""Lineage UI callbacks: view snapshots, per-dataset recordings, export screenshot.
   Register with register_lineage_callbacks(ctrl, state, _refs).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from .snapshot_io import (
    load_snapshot_by_name,
    save_snapshot,
    snapshot_names,
    delete_snapshot_by_name,
    save_screenshot,
)
from bioset.ui.state import get_channel_color


def capture_screenshot_png_bytes(streamer):
    """Capture VTK view as PNG bytes (scene only, no UI). Returns bytes or None.
    Standalone for use by callbacks.capture_screenshot and lineage export."""
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


def register_lineage_callbacks(ctrl, state, _refs):
    """Register all lineage_* and _lineage_* methods on ctrl. Uses state and _refs."""

    def _lineage_dataset_id():
        return getattr(state, "lineage_dataset_id", None) or "default"

    def _lineage_merge_channels(restored, all_channels_list):
        """Merge snapshot channels with all dataset channels so user can add new channels in lineage view."""
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

    def lineage_refresh_names():
        """Load snapshot names for current dataset into dropdown."""
        dataset_id = _lineage_dataset_id()
        names = snapshot_names(dataset_id)
        state.lineage_snapshot_names = names

    def lineage_open_snapshot():
        """Open selected snapshot: restore camera, channels, colors, LOD, TF; show description/comment."""
        name = getattr(state, "lineage_selected_name", None) or "Name"
        if not name or not str(name).strip():
            print("[callbacks] Lineage: no name selected")
            return
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(name, dataset_id)
        if not snap:
            print(f"[callbacks] Lineage: snapshot not found: {name}")
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
        state.lineage_current_view_index = 0
        v0 = views[0]
        restored = []
        for c in v0.get("channels") or []:
            r = c.get("range") or [0, 100]
            if not isinstance(r, (list, tuple)) or len(r) < 2:
                r = [0, 100]
            restored.append({
                "id": c.get("id"),
                "name": c.get("name") or f"Channel {c.get('id')}",
                "color": c.get("color", "#FFFFFF"),
                "range": [float(r[0]), float(r[1])],
            })
        state.channels = _lineage_merge_channels(restored, all_channels_before)
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
        lod = snap.get("optional_LOD") or {}
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
            from bioset.scene.volumes import build_tf_with_range
            for ch in state.channels:
                ch_id = ch.get("id")
                if ch_id is None or ch_id not in state.active_channels:
                    continue
                rng = ch.get("range")
                if rng is not None and len(rng) >= 2 and ch_id in getattr(streamer, "_channel_data_range", {}):
                    data_range = streamer._channel_data_range[ch_id]
                    tint = streamer._channel_colors.get(ch_id, (1, 1, 1))
                    color_tf, opacity_tf = build_tf_with_range(data_range, (float(rng[0]), float(rng[1])), tint)
                    streamer._channel_tfs[ch_id] = (color_tf, opacity_tf)
                    if ch_id in streamer.volumes:
                        prop = streamer.volumes[ch_id].GetProperty()
                        prop.SetColor(color_tf)
                        prop.SetScalarOpacity(opacity_tf)
        else:
            if hasattr(ctrl, "update_active_channels"):
                ctrl.update_active_channels(state.active_channels)
            if streamer:
                for ch in state.channels:
                    ch_id = ch.get("id")
                    if ch_id is None:
                        continue
                    if ch_id in state.active_channels and ch.get("color"):
                        streamer._channel_colors[ch_id] = streamer._hex_to_rgb(ch["color"])
                    if ch_id in state.active_channels and ch.get("range") is not None and len(ch.get("range", [])) >= 2 and ch_id in getattr(streamer, "_channel_data_range", {}):
                        from bioset.scene.volumes import build_tf_with_range
                        rng = (float(ch["range"][0]), float(ch["range"][1]))
                        color_tf, opacity_tf = build_tf_with_range(
                            streamer._channel_data_range[ch_id], rng, streamer._channel_colors.get(ch_id, (1, 1, 1))
                        )
                        streamer._channel_tfs[ch_id] = (color_tf, opacity_tf)
                        if ch_id in streamer.volumes:
                            prop = streamer.volumes[ch_id].GetProperty()
                            prop.SetColor(color_tf)
                            prop.SetScalarOpacity(opacity_tf)
        c = v0.get("camera") or {}
        if streamer and hasattr(streamer, "renderer") and streamer.renderer and c:
            cam = streamer.renderer.GetActiveCamera()
            if c and "position" in c and len(c.get("position", [])) >= 3:
                cam.SetPosition(c["position"][:3])
            if c and "focalPoint" in c and len(c.get("focalPoint", [])) >= 3:
                cam.SetFocalPoint(c["focalPoint"][:3])
            if c and "viewUp" in c and len(c.get("viewUp", [])) >= 3:
                cam.SetViewUp(c["viewUp"][:3])
            streamer.renderer.ResetCameraClippingRange()
        if _refs.get("view"):
            _refs["view"].update()
        state.lineage_edit_title = snap.get("title") or ""
        state.lineage_edit_description = v0.get("notes") or ""
        state.lineage_edit_comment = ""
        state.lineage_form_minimized = False
        state.lineage_display_snapshot = {
            "title": snap.get("title"),
            "description": v0.get("notes") or "",
            "comments": v0.get("comments", []),
            "created": snap.get("created"),
            "updated": snap.get("updated"),
            "agreements": v0.get("agreements", snap.get("agreements", 0)),
            "disagreements": v0.get("disagreements", snap.get("disagreements", 0)),
            "views": views,
        }

    def _lineage_apply_view(v):
        """Apply view to scene: camera, channels, active_channels, background, TF. Updates state and streamer."""
        streamer = _refs.get("streamer")
        c = v.get("camera") or {}
        if streamer and hasattr(streamer, "renderer") and streamer.renderer and c:
            cam = streamer.renderer.GetActiveCamera()
            if c.get("position") and len(c["position"]) >= 3:
                cam.SetPosition(c["position"][:3])
            if c.get("focalPoint") and len(c["focalPoint"]) >= 3:
                cam.SetFocalPoint(c["focalPoint"][:3])
            if c.get("viewUp") and len(c["viewUp"]) >= 3:
                cam.SetViewUp(c["viewUp"][:3])
            streamer.renderer.ResetCameraClippingRange()
        if v.get("channels") is not None and v.get("active_channels") is not None:
            restored = []
            for ch in v.get("channels") or []:
                r = ch.get("range") or [0, 100]
                if not isinstance(r, (list, tuple)) or len(r) < 2:
                    r = [0, 100]
                restored.append({
                    "id": ch.get("id"),
                    "name": ch.get("name") or f"Channel {ch.get('id')}",
                    "color": ch.get("color", "#FFFFFF"),
                    "range": [float(r[0]), float(r[1])],
                })
            state.channels = _lineage_merge_channels(restored, list(state.channels or []))
            state.active_channels = list(v.get("active_channels") or [])
            visible = list(getattr(state, "visible_channel_ids", []) or [])
            for ch_id in state.active_channels:
                if ch_id not in visible:
                    visible.append(ch_id)
            state.visible_channel_ids = visible
            if hasattr(ctrl, "update_active_channels"):
                ctrl.update_active_channels(state.active_channels)
            if streamer:
                from bioset.scene.volumes import build_tf_with_range
                for ch in state.channels:
                    ch_id = ch.get("id")
                    if ch_id is None or ch_id not in state.active_channels:
                        continue
                    if ch.get("color"):
                        streamer._channel_colors[ch_id] = streamer._hex_to_rgb(ch["color"])
                    rng = ch.get("range")
                    if rng and len(rng) >= 2 and ch_id in getattr(streamer, "_channel_data_range", {}):
                        color_tf, opacity_tf = build_tf_with_range(
                            streamer._channel_data_range[ch_id], (float(rng[0]), float(rng[1])),
                            streamer._channel_colors.get(ch_id, (1, 1, 1)))
                        streamer._channel_tfs[ch_id] = (color_tf, opacity_tf)
                        if ch_id in streamer.volumes:
                            prop = streamer.volumes[ch_id].GetProperty()
                            prop.SetColor(color_tf)
                            prop.SetScalarOpacity(opacity_tf)
        if v.get("background") and hasattr(ctrl, "update_background_color"):
            state.bg_color = v["background"]
            ctrl.update_background_color(v["background"])
        if _refs.get("view"):
            _refs["view"].update()

    def _lineage_switch_view(new_idx):
        """Switch to view at new_idx: apply that view to scene and update form."""
        disp = getattr(state, "lineage_display_snapshot", None)
        views = (disp.get("views") or []) if disp else []
        if new_idx < 0 or new_idx >= len(views) or not views:
            return
        v = views[new_idx]
        _lineage_apply_view(v)
        state.lineage_current_view_index = new_idx
        state.lineage_edit_description = v.get("notes") or ""
        state.lineage_edit_comment = ""
        state.lineage_display_snapshot = {
            **disp,
            "description": v.get("notes") or "",
            "comments": v.get("comments", []),
            "agreements": v.get("agreements", 0),
            "disagreements": v.get("disagreements", 0),
        }

    def lineage_apply_current_view():
        """Apply current view (camera, channels, TF, background) to the scene."""
        disp = getattr(state, "lineage_display_snapshot", None)
        views = (disp.get("views") or []) if disp else []
        idx = getattr(state, "lineage_current_view_index", 0)
        if not views or idx < 0 or idx >= len(views):
            return
        _lineage_apply_view(views[idx])

    def lineage_close_display():
        state.lineage_display_snapshot = None
        state.lineage_form_minimized = False

    def lineage_view_prev():
        idx = getattr(state, "lineage_current_view_index", 0)
        _lineage_switch_view(idx - 1)

    def lineage_view_next():
        _lineage_switch_view(getattr(state, "lineage_current_view_index", 0) + 1)

    def lineage_add_view():
        """Add a new view with current camera, channels, TF, background; save to JSON and switch to it."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            return
        cap = _lineage_capture_view()
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
            "agreements": 0,
            "disagreements": 0,
        }
        views.append(new_view)
        state.lineage_current_view_index = len(views) - 1
        state.lineage_edit_description = ""
        state.lineage_edit_comment = ""
        state.lineage_display_snapshot = {
            **disp,
            "views": views,
            "description": "",
            "comments": [],
            "agreements": 0,
            "disagreements": 0,
        }
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(disp["title"], dataset_id)
        if snap:
            snap["views"] = views
            snap["updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            save_snapshot(snap, dataset_id)
        if _refs.get("view"):
            _refs["view"].update()

    def lineage_open_new_form():
        """Open the new-snapshot form (bottom-left). Optionally refresh names."""
        lineage_refresh_names()
        state.lineage_form_name = getattr(state, "lineage_selected_name", "Name") or "Name"
        state.lineage_form_description = ""
        state.lineage_form_new_comment = ""
        state.lineage_form_dialog = True

    def _lineage_capture_view():
        """Capture camera, LOD, channels (active only), viewport, background from current view."""
        streamer = _refs.get("streamer")
        camera = {}
        if streamer and hasattr(streamer, "renderer") and streamer.renderer:
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
        channels_data = []
        for ch in (state.channels or []):
            c = dict(ch) if isinstance(ch, dict) else {}
            if c.get("id") not in active:
                continue
            r = c.get("range") or [0, 100]
            if not isinstance(r, (list, tuple)) or len(r) < 2:
                r = [0, 100]
            channels_data.append({
                "id": c.get("id"),
                "name": c.get("name") or f"Channel {c.get('id', '')}",
                "color": c.get("color", "#FFFFFF"),
                "range": [float(r[0]), float(r[1])],
            })
        viewport = {}
        if streamer and hasattr(streamer, "renderer") and streamer.renderer:
            rw = streamer.renderer.GetRenderWindow()
            if rw:
                w, h = rw.GetSize()
                viewport = {"width": w, "height": h}
        bg = getattr(state, "bg_color", "#000000") or "#000000"
        return {"camera": camera, "optional_lod": optional_lod, "channels": channels_data, "active_channels": active, "viewport": viewport, "background": bg}

    def lineage_save_snapshot():
        """Capture current view + form fields; save to lineage JSON; close form."""
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cap = _lineage_capture_view()
        form_name = (getattr(state, "lineage_form_name", None) or getattr(state, "lineage_selected_name", None) or "").strip()
        title = form_name or "Unnamed"
        comments = []
        if getattr(state, "lineage_form_new_comment", "").strip():
            comments.append({"date": now, "text": state.lineage_form_new_comment})
        notes = getattr(state, "lineage_form_description", "") or ""
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
        snapshot = {
            "id": str(uuid.uuid4()),
            "title": title,
            "created": now,
            "updated": now,
            "notes": notes,
            "camera": cap["camera"],
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap["viewport"],
            "agreements": 0,
            "disagreements": 0,
            "comments": comments,
            "views": [view0],
        }
        if cap["optional_lod"]:
            snapshot["optional_LOD"] = cap["optional_lod"]
        dataset_id = _lineage_dataset_id()
        save_snapshot(snapshot, dataset_id)
        lineage_refresh_names()
        state.lineage_selected_name = title
        state.lineage_form_dialog = False
        state.lineage_edit_title = title
        state.lineage_edit_description = notes
        state.lineage_edit_comment = ""
        state.lineage_current_view_index = 0
        state.lineage_display_snapshot = {
            "title": title,
            "description": notes,
            "comments": comments,
            "created": now,
            "updated": now,
            "agreements": 0,
            "disagreements": 0,
            "views": [view0],
        }
        if _refs.get("view"):
            _refs["view"].update()

    def lineage_update_snapshot():
        """Save current view into views list (use state's views so Add-view entries persist), then write JSON."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            print("[callbacks] Lineage: no snapshot displayed to update")
            return
        old_title = disp["title"]
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(old_title, dataset_id)
        if not snap:
            print(f"[callbacks] Lineage: snapshot not found: {old_title}")
            return
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cap = _lineage_capture_view()
        snap["updated"] = now
        snap["title"] = (getattr(state, "lineage_edit_title", "") or "").strip() or old_title
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
                "agreements": snap.get("agreements", 0),
                "disagreements": snap.get("disagreements", 0),
            }]
        idx = getattr(state, "lineage_current_view_index", 0)
        idx = max(0, min(idx, len(views) - 1))
        notes = getattr(state, "lineage_edit_description", "") or ""
        new_comment = (getattr(state, "lineage_edit_comment", "") or "").strip()
        view_comments = list(views[idx].get("comments") or []) if idx < len(views) else []
        if new_comment:
            view_comments.append({"date": now, "text": new_comment})
        current_view_data = views[idx] if idx < len(views) else {}
        def _view_agreements():
            if "agreements" in current_view_data:
                return current_view_data["agreements"]
            return snap.get("agreements", 0) if idx == 0 else 0
        def _view_disagreements():
            if "disagreements" in current_view_data:
                return current_view_data["disagreements"]
            return snap.get("disagreements", 0) if idx == 0 else 0
        view_payload = {
            "camera": cap["camera"],
            "notes": notes,
            "comments": view_comments,
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap.get("viewport") or {},
            "optional_LOD": cap.get("optional_lod"),
            "agreements": _view_agreements(),
            "disagreements": _view_disagreements(),
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
        if snap["title"] != old_title:
            delete_snapshot_by_name(old_title, dataset_id)
        save_snapshot(snap, dataset_id)
        lineage_refresh_names()
        state.lineage_selected_name = snap["title"]
        state.lineage_display_snapshot = {
            "title": snap["title"],
            "description": notes,
            "comments": view_comments,
            "created": snap.get("created"),
            "updated": snap["updated"],
            "agreements": current_view.get("agreements", 0),
            "disagreements": current_view.get("disagreements", 0),
            "views": views,
        }
        state.lineage_edit_title = snap["title"]
        state.lineage_edit_comment = ""

    def lineage_agree():
        """Increment agreements for the current view and save."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            print("[callbacks] Lineage: no snapshot displayed to agree")
            return
        idx = max(0, min(getattr(state, "lineage_current_view_index", 0), len(disp.get("views") or []) - 1))
        title = disp["title"]
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(title, dataset_id)
        if not snap:
            print(f"[callbacks] Lineage: snapshot not found: {title}")
            return
        views = list(snap.get("views") or [])
        if idx < len(views):
            views[idx]["agreements"] = views[idx].get("agreements", 0) + 1
            snap["views"] = views
            save_snapshot(snap, dataset_id)
            state.lineage_display_snapshot = {
                **disp,
                "views": views,
                "agreements": views[idx]["agreements"],
            }

    def lineage_disagree():
        """Increment disagreements for the current view and save."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            return
        idx = max(0, min(getattr(state, "lineage_current_view_index", 0), len(disp.get("views") or []) - 1))
        title = disp["title"]
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(title, dataset_id)
        if not snap:
            return
        views = list(snap.get("views") or [])
        if idx < len(views):
            views[idx]["disagreements"] = views[idx].get("disagreements", 0) + 1
            snap["views"] = views
            save_snapshot(snap, dataset_id)
            state.lineage_display_snapshot = {
                **disp,
                "views": views,
                "disagreements": views[idx]["disagreements"],
            }

    def lineage_comment():
        """Append current comment to the current view and save."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            return
        new_comment = (getattr(state, "lineage_edit_comment", "") or "").strip()
        if not new_comment:
            return
        title = disp["title"]
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(title, dataset_id)
        if not snap:
            return
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        snap["updated"] = now
        views = snap.get("views") or [{"camera": snap.get("camera"), "notes": snap.get("notes") or "", "comments": snap.get("comments") or []}]
        idx = max(0, min(getattr(state, "lineage_current_view_index", 0), len(views) - 1))
        views[idx].setdefault("comments", []).append({"date": now, "text": new_comment})
        snap["views"] = views
        snap["comments"] = views[0].get("comments", [])
        save_snapshot(snap, dataset_id)
        state.lineage_display_snapshot = {**disp, "comments": views[idx].get("comments", []), "updated": now, "views": views}
        state.lineage_edit_comment = ""

    def _capture_screenshot_png_bytes():
        streamer = _refs.get("streamer")
        return capture_screenshot_png_bytes(streamer)

    def lineage_open_export_screenshot():
        """Open export screenshot popup: empty name and caption boxes."""
        state.lineage_export_screenshot_name = ""
        state.lineage_export_screenshot_caption = ""
        state.lineage_export_screenshot_dialog = True

    def lineage_export_screenshot_save():
        """Save screenshot to dataset Screenshot folder with chosen name; add description as caption; close dialog."""
        name = (getattr(state, "lineage_export_screenshot_name", "") or "").strip()
        if not name:
            return
        png_bytes = _capture_screenshot_png_bytes()
        if not png_bytes:
            return
        caption = getattr(state, "lineage_export_screenshot_caption", "") or ""
        png_bytes = _add_caption_to_png(png_bytes, caption)
        dataset_id = _lineage_dataset_id()
        save_screenshot(png_bytes, name, dataset_id)
        state.lineage_export_screenshot_dialog = False
        state.lineage_export_screenshot_name = ""
        state.lineage_export_screenshot_caption = ""
        if _refs.get("view"):
            _refs["view"].update()

    # Attach to ctrl
    ctrl.lineage_refresh_names = lineage_refresh_names
    ctrl.lineage_open_snapshot = lineage_open_snapshot
    ctrl.lineage_open_new_form = lineage_open_new_form
    ctrl.lineage_save_snapshot = lineage_save_snapshot
    ctrl.lineage_view_prev = lineage_view_prev
    ctrl.lineage_view_next = lineage_view_next
    ctrl.lineage_add_view = lineage_add_view
    ctrl.lineage_close_display = lineage_close_display
    ctrl.lineage_apply_current_view = lineage_apply_current_view
    ctrl.lineage_open_export_screenshot = lineage_open_export_screenshot
    ctrl.lineage_export_screenshot_save = lineage_export_screenshot_save
    ctrl.lineage_agree = lineage_agree
    ctrl.lineage_disagree = lineage_disagree
    ctrl.lineage_comment = lineage_comment
    ctrl.lineage_update_snapshot = lineage_update_snapshot
