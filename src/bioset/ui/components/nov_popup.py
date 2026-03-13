# nov_popup.py
"""NOV popup: 3D view; channels overlay; Set (run best view) / Reset (clear results, keep popup open). Resizable."""

from __future__ import annotations

from trame.widgets import html, vtk, vuetify

# Base style: position from nov_popup_pos when set; else default. Sizes 1.5x.
_POS_DEFAULT = "left: 50%; transform: translateX(-50%); bottom: 1%; "
_POS_DEFAULT_MIN = "left: 50%; transform: translateX(-50%); bottom: 0; "
_STYLE_BASE_FULL = (
    "position: fixed; "
    "min-width: 630px; max-width: 1620px; min-height: 450px; max-height: 945px; "
    "z-index: 300; border-radius: 8px 8px 0 0; overflow: hidden; resize: both; "
    "box-shadow: 0 4px 20px rgba(0,0,0,0.2); background: transparent; border: 1px solid rgba(255,255,255,0.2); "
    "display: flex; flex-direction: column; "
)
_STYLE_BASE_MIN = (
    "position: fixed; "
    "width: 900px; height: auto; min-height: 36px; max-height: 36px; "
    "z-index: 300; border-radius: 8px 8px 0 0; overflow: hidden; "
    "box-shadow: 0 -2px 12px rgba(0,0,0,0.2); background: transparent; border: 1px solid rgba(255,255,255,0.2); border-bottom: none; "
    "display: flex; flex-direction: column;"
)

_BTN_STYLE = "background: transparent !important; color: rgba(255,255,255,0.95);"


def nov_popup_panel(state, ctrl, nov_render_window=None):
    """Floating NOV panel. Size from state (initial from box); user can resize."""
    state.setdefault("nov_popup_style_full", _STYLE_BASE_FULL)
    state.setdefault("nov_popup_style_minimized", _STYLE_BASE_MIN)
    state.setdefault("nov_popup_pos_default", _POS_DEFAULT)
    state.setdefault("nov_popup_pos_default_min", _POS_DEFAULT_MIN)
    # Safe: never access [0]/[1] when pos is undefined or invalid (avoids "reading '0' of undefined")
    _pos_css = (
        "((function(){ var s = nov_popup_pos; if (s == null || typeof s !== 'string') return (nov_popup_minimized ? nov_popup_pos_default_min : nov_popup_pos_default); "
        "var p = s.split(','); if (p.length < 2) return (nov_popup_minimized ? nov_popup_pos_default_min : nov_popup_pos_default); "
        "var a = (p && p.length >= 2) ? p[0] : null; var b = (p && p.length >= 2) ? p[1] : null; if (a == null || b == null) return (nov_popup_minimized ? nov_popup_pos_default_min : nov_popup_pos_default); return nov_popup_minimized ? ('left: ' + a + 'px; bottom: 0; transform: none; ') : ('left: ' + a + 'px; bottom: ' + b + 'px; transform: none; '); })())"
    )
    _full_style = (
        "'position: fixed; ' + " + _pos_css + " + "
        "(nov_popup_minimized ? nov_popup_style_minimized : ('width: ' + nov_popup_width_px + 'px; height: ' + nov_popup_height_px + 'px; ' + nov_popup_style_full))"
    )

    # Drag is handled on the panel root (currentTarget), but only when the pointer
    # starts in the top header band. This avoids fragile DOM queries across iframes/wrappers.
    _panel_mousedown = (
        "if ($event.target && ($event.target.closest('button') || $event.target.closest('.v-btn') || $event.target.closest('a'))) return; "
        "var el = $event.currentTarget; if (!el || !el.getBoundingClientRect) return; "
        "var r = el.getBoundingClientRect(); var y = $event.clientY - r.top; "
        "if (y > 44) return; "
        "$event.preventDefault(); $event.stopPropagation(); "
        "if (window.novDragStart) window.novDragStart($event);"
    )
    with html.Div(
            v_show=("nov_panel_visible", False),
            style=(_full_style, "position: fixed; " + _POS_DEFAULT + "width: 900px; height: 675px; " + _STYLE_BASE_FULL),
            class_="nov-popup-panel",
            attrs={"id": "nov-popup-drag-root", "data-nov-panel": "1"},
            mousedown=_panel_mousedown,
        ):
            html.Input(
                type="text",
                v_model=("nov_popup_size_str", ""),
                attrs={"id": "nov-popup-size-input", "aria-hidden": "true", "tabindex": "-1"},
                style="position: absolute; opacity: 0; width: 0; height: 0; pointer-events: none;",
            )
            html.Input(
                type="text",
                v_model=("nov_popup_pos", ""),
                attrs={"id": "nov-popup-pos-input", "data-nov-pos-input": "1", "aria-hidden": "true", "tabindex": "-1"},
                style="position: absolute; opacity: 0; width: 0; height: 0; pointer-events: none;",
            )
            with html.Div(
                class_="nov-popup-header",
                style="display: flex; align-items: center; justify-content: space-between; padding: 4px 8px; background: transparent; border-bottom: 1px solid rgba(255,255,255,0.2); flex: 0 0 auto; min-height: 32px; max-height: 40px; color: rgba(255,255,255,0.95); cursor: move; -webkit-user-drag: none; user-select: none;",
                attrs={"draggable": "false"},
            ):
                with html.Div(style="display: flex; align-items: center; gap: 4px; min-width: 0; color: rgba(255,255,255,0.95);"):
                    with vuetify.VBtn(v_show=("!nov_auto_play", True), icon=True, x_small=True, dense=True, click=ctrl.nov_play_pause, style=_BTN_STYLE):
                        vuetify.VIcon("mdi-play", small=True)
                    with vuetify.VBtn(v_show=("nov_auto_play", False), icon=True, x_small=True, dense=True, click=ctrl.nov_play_pause, style=_BTN_STYLE):
                        vuetify.VIcon("mdi-pause", small=True)
                    with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.nov_prev, style=_BTN_STYLE):
                        vuetify.VIcon("mdi-chevron-left", small=True)
                    html.Span("{{ nov_view_index_display }}", style="min-width: 3ch; font-size: 0.7rem; color: rgba(255,255,255,0.95);")
                    with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.nov_next, style=_BTN_STYLE):
                        vuetify.VIcon("mdi-chevron-right", small=True)
                    with html.Div(
                        v_show=("nov_sphere_svg", False),
                        style="display: inline-flex; align-items: center; margin-left: 2px; flex-shrink: 0; background: transparent; border-radius: 50%; padding: 1px;",
                    ):
                        html.Div(
                            v_html=("nov_sphere_svg", ""),
                            style="width: 24px; height: 24px; display: inline-block; line-height: 0;",
                        )
                    # Single button: Set (when no results) or Reset (when has results)
                    with vuetify.VBtn(v_show=("!nov_has_results", False), small=True, dense=True, click=ctrl.nov_set, style=_BTN_STYLE):
                        html.Span("Set")
                    with vuetify.VBtn(v_show=("nov_has_results", False), small=True, dense=True, click=ctrl.nov_reset, style=_BTN_STYLE):
                        html.Span("Reset")
                with html.Div(style="display: flex; align-items: center; flex-shrink: 0; gap: 2px;"):
                    with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.bookmark_open_new_form_from_nov, style=_BTN_STYLE):
                        vuetify.VIcon("mdi-bookmark", small=True)
                    with vuetify.VBtn(v_show=("!nov_popup_minimized", True), icon=True, x_small=True, dense=True, click="nov_popup_minimized = true", style=_BTN_STYLE):
                        vuetify.VIcon("mdi-window-minimize", small=True)
                    with vuetify.VBtn(v_show=("nov_popup_minimized", False), icon=True, x_small=True, dense=True, click="nov_popup_minimized = false", style=_BTN_STYLE):
                        vuetify.VIcon("mdi-window-restore", small=True)
                    with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.nov_toggle, style=_BTN_STYLE):
                        vuetify.VIcon("mdi-close", small=True)

            # Body: only 3D view (full area); channel list as overlay with transparent background, higher z
            with html.Div(
                v_show=("!nov_popup_minimized", True),
                style="flex: 1 1 0; min-height: 0; width: 100%; position: relative; overflow: hidden; background: transparent;",
            ):
                # 3D view full size
                with html.Div(
                    style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; min-height: 200px;",
                ):
                    if nov_render_window is not None:
                        nov_view = vtk.VtkRemoteView(
                            nov_render_window,
                            interactive_ratio=1.0,
                            style="width: 100%; height: 100%; min-width: 100%; min-height: 100%; display: block;",
                        )
                        if hasattr(ctrl, "set_nov_view"):
                            ctrl.set_nov_view(nov_view)
                    else:
                        html.Div(
                            "NOV view",
                            style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; color: rgba(255,255,255,0.5);",
                        )
                # Channel list overlay: no scrollbar; fits ~6 channels (26px each)
                with html.Div(
                    style="position: absolute; top: 4px; left: 4px; z-index: 10; max-height: 180px; overflow: hidden; "
                    "background: rgba(0,0,0,0.35); border-radius: 6px; padding: 4px 4px 4px 3px;",
                ):
                    with html.Template(v_for="(ch, idx) in nov_active_channel_items", key="ch.id"):
                        with vuetify.VListItem(dense=True, style="min-height: 26px; padding: 0 2px; background: transparent; display: flex; align-items: center; gap: 2px;"):
                            vuetify.VCheckbox(
                                v_model=("nov_selected_channels", []),
                                value=("ch.id",),
                                hide_details=True,
                                dense=True,
                                color=("ch.color", "#888"),
                                style="margin: 0; flex-shrink: 0;",
                                dark=True,
                            )
                            html.Span("{{ ch.name }}", style="font-size: 0.7rem; color: rgba(255,255,255,0.95); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0;")
                # Scale bar: right-bottom, small; two perpendicular ticks at start/end; adaptive with zoom (spacing 0.14,0.14,0.28 µm)
                with html.Div(
                    v_show=("nov_scale_bar_width_px > 0", False),
                    style="position: absolute; right: 6px; bottom: 6px; left: auto; z-index: 10; "
                    "display: flex; flex-direction: column; align-items: flex-end; gap: 1px; "
                    "background: rgba(0,0,0,0.35); border-radius: 3px; padding: 2px 4px; "
                    "color: rgba(255,255,255,0.92); font-size: 0.55rem;",
                ):
                    with html.Div(style="display: flex; align-items: center; height: 5px;"):
                        html.Div(style="width: 1px; height: 5px; min-width: 1px; background: rgba(255,255,255,0.95); border-radius: 0;")
                        html.Div(
                            style=(
                                "'width: ' + nov_scale_bar_width_px + 'px; height: 1.5px; min-width: 2px; background: rgba(255,255,255,0.95); border-radius: 0;'",
                                "width: 50px; height: 1.5px; background: rgba(255,255,255,0.95);",
                            ),
                        )
                        html.Div(style="width: 1px; height: 5px; min-width: 1px; background: rgba(255,255,255,0.95); border-radius: 0;")
                    html.Span("{{ nov_scale_bar_label }}", style="font-size: 0.55rem; line-height: 1;")