# nov_popup.py
"""NOV popup: left = active channels (select/deselect) + Set/Reset, right = 3D view. Transparent background."""

from __future__ import annotations

from trame.widgets import html, vtk, vuetify

_TRANS = "transparent"
_STYLE_FULL = (
    "position: fixed; left: 50%; transform: translateX(-50%); bottom: 1%; "
    "width: 40vw; min-width: 640px; max-width: 1400px; "
    "height: 36vh; min-height: 400px; max-height: 720px; "
    "z-index: 300; border-radius: 8px 8px 0 0; overflow: hidden; "
    "box-shadow: 0 4px 20px rgba(0,0,0,0.2); background: transparent; border: 1px solid rgba(255,255,255,0.2); "
    "display: flex; flex-direction: column; "
)
_STYLE_MINIMIZED = (
    "position: fixed; left: 50%; transform: translateX(-50%); bottom: 0; "
    "width: 40vw; min-width: 640px; max-width: 1400px; "
    "height: auto; min-height: 44px; max-height: 44px; "
    "z-index: 300; border-radius: 8px 8px 0 0; overflow: hidden; "
    "box-shadow: 0 -2px 12px rgba(0,0,0,0.2); background: transparent; border: 1px solid rgba(255,255,255,0.2); border-bottom: none; "
    "display: flex; flex-direction: column;"
)

_BTN_STYLE = "background: transparent !important; color: rgba(255,255,255,0.95);"


def nov_popup_panel(state, ctrl, nov_render_window=None):
    """Floating NOV panel: left = active channels (checkboxes) + Set/Reset, right = 3D view. All transparent."""
    state.setdefault("nov_popup_style_full", _STYLE_FULL)
    state.setdefault("nov_popup_style_minimized", _STYLE_MINIMIZED)
    with html.Div(
        v_show=("nov_panel_visible", False),
        style=("nov_popup_minimized ? nov_popup_style_minimized : nov_popup_style_full", _STYLE_FULL),
    ):
        # Header: nav + minimize + close (transparent)
        with html.Div(
            style="display: flex; align-items: center; justify-content: space-between; padding: 6px 8px; background: transparent; border-bottom: 1px solid rgba(255,255,255,0.2); flex: 0 0 auto; min-height: 36px; max-height: 48px; color: rgba(255,255,255,0.95);",
        ):
            with html.Div(style="display: flex; align-items: center; gap: 6px; min-width: 0; color: rgba(255,255,255,0.95);"):
                with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.nov_prev, style=_BTN_STYLE):
                    vuetify.VIcon("mdi-chevron-left", small=True)
                html.Span("{{ nov_view_index_display }}", style="min-width: 3ch; font-size: 0.75rem; color: rgba(255,255,255,0.95);")
                with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.nov_next, style=_BTN_STYLE):
                    vuetify.VIcon("mdi-chevron-right", small=True)
                with html.Div(
                    v_show=("nov_sphere_svg", False),
                    style="display: inline-flex; align-items: center; margin-left: 4px; flex-shrink: 0; background: transparent; border-radius: 50%; padding: 2px;",
                ):
                    html.Div(
                        v_html=("nov_sphere_svg", ""),
                        style="width: 28px; height: 28px; display: inline-block; line-height: 0;",
                    )
            with html.Div(style="display: flex; align-items: center; flex-shrink: 0; gap: 2px;"):
                with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.bookmark_open_new_form_from_nov, style=_BTN_STYLE):
                    vuetify.VIcon("mdi-bookmark", small=True)
                with vuetify.VBtn(v_show=("!nov_popup_minimized", True), icon=True, x_small=True, dense=True, click="nov_popup_minimized = true", style=_BTN_STYLE):
                    vuetify.VIcon("mdi-window-minimize", small=True)
                with vuetify.VBtn(v_show=("nov_popup_minimized", False), icon=True, x_small=True, dense=True, click="nov_popup_minimized = false", style=_BTN_STYLE):
                    vuetify.VIcon("mdi-window-restore", small=True)
                with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.nov_toggle, style=_BTN_STYLE):
                    vuetify.VIcon("mdi-close", small=True)

        # Body: left sidebar (active channels + Set/Reset) | right (3D view)
        with html.Div(
            v_show=("!nov_popup_minimized", True),
            style="flex: 1 1 0; min-height: 0; display: flex; flex-direction: row; width: 100%; overflow: hidden; background: transparent;",
        ):
            # Left: same channels as active in main scene — list with checkboxes to select/unselect + Set / Reset
            with html.Div(
                style="width: 200px; min-width: 180px; max-width: 240px; border-right: 1px solid rgba(255,255,255,0.2); padding: 8px; display: flex; flex-direction: column; gap: 8px; overflow-y: auto; background: transparent;",
            ):
                html.Span("Channels (uncheck to hide in window)", style="font-weight: 600; font-size: 0.8rem; color: rgba(255,255,255,0.95);")
                with vuetify.VList(dense=True, style="background: transparent; flex: 1 1 0; min-height: 0; overflow-y: auto;"):
                    with html.Template(v_for="(ch, idx) in nov_active_channel_items", key="ch.id"):
                        with vuetify.VListItem(dense=True, style="min-height: 36px; background: transparent;"):
                            vuetify.VCheckbox(
                                v_model=("nov_selected_channels", []),
                                value=("ch.id",),
                                label=("ch.name",),
                                hide_details=True,
                                dense=True,
                                style="color: rgba(255,255,255,0.95); margin: 0;",
                                dark=True,
                            )
                with html.Div(style="display: flex; gap: 8px;"):
                    with vuetify.VBtn(small=True, click=ctrl.nov_set, style=_BTN_STYLE):
                        html.Span("Set")
                    with vuetify.VBtn(small=True, click=ctrl.nov_reset, style=_BTN_STYLE):
                        html.Span("Reset")

            # Right: 3D view
            with html.Div(
                style="flex: 1 1 0; min-width: 0; min-height: 280px; position: relative; display: flex; flex-direction: column; background: transparent;",
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
