# nov_popup.py
"""NOV popup: bottom-center window (10% screen), close/minimize, nav + score + SVG, and clipped 3D view."""

from __future__ import annotations

from trame.widgets import html, vtk, vuetify


_STYLE_FULL = (
    "position: fixed; left: 50%; transform: translateX(-50%); bottom: 1%; "
    "width: 30vw; min-width: 560px; max-width: 1280px; "
    "height: 30vh; min-height: 360px; max-height: 720px; "
    "z-index: 300; border-radius: 8px 8px 0 0; overflow: hidden; "
    "box-shadow: 0 4px 20px rgba(0,0,0,0.35); background: #ffffff; border: 1px solid rgba(0,0,0,0.12); "
    "display: flex; flex-direction: column;"
)
_STYLE_MINIMIZED = (
    "position: fixed; left: 50%; transform: translateX(-50%); bottom: 0; "
    "width: 30vw; min-width: 560px; max-width: 1280px; "
    "height: auto; min-height: 44px; max-height: 44px; "
    "z-index: 300; border-radius: 8px 8px 0 0; overflow: hidden; "
    "box-shadow: 0 -2px 12px rgba(0,0,0,0.25); background: #ffffff; border: 1px solid rgba(0,0,0,0.12); border-bottom: none; "
    "display: flex; flex-direction: column;"
)


def nov_popup_panel(state, ctrl, nov_render_window=None):
    """Floating NOV panel at bottom center; when minimized, collapses to a bar at bottom of screen."""
    state.setdefault("nov_popup_style_full", _STYLE_FULL)
    state.setdefault("nov_popup_style_minimized", _STYLE_MINIMIZED)
    with html.Div(
        v_show=("nov_panel_visible && nov_popup_open", False),
        style=("nov_popup_minimized ? nov_popup_style_minimized : nov_popup_style_full", _STYLE_FULL),
    ):
        # Header: white background, dark text so NOV next/prev, score, SVG are visible
        with html.Div(
            style="display: flex; align-items: center; justify-content: space-between; padding: 6px 8px; background: #ffffff; border-bottom: 1px solid rgba(0,0,0,0.12); flex-shrink: 0; color: #1a1a1a;",
        ):
            with html.Div(style="display: flex; align-items: center; gap: 6px; min-width: 0; color: #1a1a1a;"):
                with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.nov_prev):
                    vuetify.VIcon("mdi-chevron-left", small=True)
                html.Span("{{ nov_view_index_display }}", style="min-width: 3ch; font-size: 0.75rem; color: #1a1a1a;")
                with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.nov_next):
                    vuetify.VIcon("mdi-chevron-right", small=True)
                html.Span("{{ nov_view_side }}", style="font-size: 0.7rem; font-weight: 600; margin-left: 2px; color: #1a1a1a;")
                with html.Div(
                    v_show=("nov_sphere_svg", False),
                    style="display: inline-flex; align-items: center; margin-left: 4px; flex-shrink: 0; background: #333; border-radius: 50%; padding: 2px;",
                ):
                    html.Div(
                        v_html=("nov_sphere_svg", ""),
                        style="width: 28px; height: 28px; display: inline-block; line-height: 0;",
                    )
            with html.Div(style="display: flex; align-items: center; flex-shrink: 0; gap: 2px;"):
                with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.bookmark_open_new_form_from_nov):
                    vuetify.VIcon("mdi-bookmark", small=True)
                with vuetify.VBtn(v_show=("!nov_popup_minimized", True), icon=True, x_small=True, dense=True, click="nov_popup_minimized = true"):
                    vuetify.VIcon("mdi-window-minimize", small=True)
                with vuetify.VBtn(v_show=("nov_popup_minimized", False), icon=True, x_small=True, dense=True, click="nov_popup_minimized = false"):
                    vuetify.VIcon("mdi-window-restore", small=True)
                with vuetify.VBtn(icon=True, x_small=True, dense=True, click=ctrl.nov_toggle):
                    vuetify.VIcon("mdi-close", small=True)

        # Body: 3D view (only when not minimized)
        with html.Div(
            v_show=("!nov_popup_minimized", True),
            style="flex: 1; min-height: 0; position: relative;",
        ):
            if nov_render_window is not None:
                nov_view = vtk.VtkRemoteView(
                    nov_render_window,
                    interactive_ratio=1.0,
                    style="width: 100%; height: 100%; min-height: 60px;",
                )
                if hasattr(ctrl, "set_nov_view"):
                    ctrl.set_nov_view(nov_view)
            else:
                html.Div(
                    "NOV view",
                    style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; color: rgba(255,255,255,0.5);",
                )
