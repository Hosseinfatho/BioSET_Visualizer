# nov_popup.py
"""NOV popup: 3D view only; channels overlay (transparent); single Set/Reset button. Smaller window."""

from __future__ import annotations

from trame.widgets import html, vtk, vuetify

_TRANS = "transparent"
# Smaller window
_STYLE_FULL = (
    "position: fixed; left: 50%; transform: translateX(-50%); bottom: 1%; "
    "width: 26vw; min-width: 340px; max-width: 640px; "
    "height: 22vh; min-height: 220px; max-height: 360px; "
    "z-index: 300; border-radius: 8px 8px 0 0; overflow: hidden; "
    "box-shadow: 0 4px 20px rgba(0,0,0,0.2); background: transparent; border: 1px solid rgba(255,255,255,0.2); "
    "display: flex; flex-direction: column; "
)
_STYLE_MINIMIZED = (
    "position: fixed; left: 50%; transform: translateX(-50%); bottom: 0; "
    "width: 26vw; min-width: 340px; max-width: 640px; "
    "height: auto; min-height: 36px; max-height: 36px; "
    "z-index: 300; border-radius: 8px 8px 0 0; overflow: hidden; "
    "box-shadow: 0 -2px 12px rgba(0,0,0,0.2); background: transparent; border: 1px solid rgba(255,255,255,0.2); border-bottom: none; "
    "display: flex; flex-direction: column;"
)

_BTN_STYLE = "background: transparent !important; color: rgba(255,255,255,0.95);"


def nov_popup_panel(state, ctrl, nov_render_window=None):
    """Floating NOV panel: 3D view full; channels as overlay with transparent bg; single Set/Reset in header."""
    state.setdefault("nov_popup_style_full", _STYLE_FULL)
    state.setdefault("nov_popup_style_minimized", _STYLE_MINIMIZED)
    with html.Div(
        v_show=("nov_panel_visible", False),
        style=("nov_popup_minimized ? nov_popup_style_minimized : nov_popup_style_full", _STYLE_FULL),
    ):
        # Header: nav + Set or Reset (single button) + bookmark + minimize + close
        with html.Div(
            style="display: flex; align-items: center; justify-content: space-between; padding: 4px 8px; background: transparent; border-bottom: 1px solid rgba(255,255,255,0.2); flex: 0 0 auto; min-height: 32px; max-height: 40px; color: rgba(255,255,255,0.95);",
        ):
            with html.Div(style="display: flex; align-items: center; gap: 4px; min-width: 0; color: rgba(255,255,255,0.95);"):
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