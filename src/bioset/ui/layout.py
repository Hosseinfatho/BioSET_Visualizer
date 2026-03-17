# gui.py
from __future__ import annotations

from trame.ui.vuetify import VAppLayout
from trame.widgets import html, vuetify, client

from .components.floating_chatbot import floating_chatbot_section
from .scripts import register_scripts
from .state import init_state, register_state_change_handlers

from .callbacks import register_callbacks
from .components import bookmark_column
from .styles import register_styles

from .components import left_drawer, right_drawer, viewer, nov_popup_panel
from bioset.bookmark import bookmark_form_panel

def build_ui(server, render_window, streamer=None, nov_render_window=None):
    ctrl = server.controller
    state = server.state

    init_state(state)
    register_callbacks(ctrl, state, None, streamer)

    view = None
    with VAppLayout(server) as layout:
        register_styles(client)
        left_drawer(state, ctrl)
        right_drawer(state, ctrl)
        floating_chatbot_section(state, ctrl)
        # VTK RENDERER
        with layout.root:
            # Container with fill-height so viewer gets height (avoids white screen)
            with vuetify.VContainer(fluid=True, classes="pa-0 fill-height", style="position: relative; min-height: 0;"):
                with html.Div(style="position: relative; width: 100%; height: 100%; min-height: 0;"):
                    with html.Div(
                            style="position: absolute; left: 0; top: 0; right: 0; bottom: 0; min-width: 0; min-height: 0;"):
                        view = viewer(ctrl, render_window)
            # Hidden input: client script updates this with actual drawer width (px)
            html.Input(
                type="text",
                v_model=("left_drawer_width_px", 350),
                attrs={"id": "bioset-left-drawer-width", "aria-hidden": "true", "tabindex": "-1"},
                style="position: fixed; opacity: 0; width: 0; height: 0; pointer-events: none;",
            )
            # Bookmark: fixed overlay to the RIGHT of left drawer (not on top of settings)
            with html.Div(
                    v_show=("bookmark_open", False),
                    class_="bookmark-panel-beside-drawer",
                    style=(
                        "'position: fixed; left: ' + ((left_drawer_width_px || 0) + 8) + 'px; top: 0; "
                        "width: 250px; height: 50vh; z-index: 12; pointer-events: auto;'",
                        "position: fixed; left: 358px; top: 0; width: 250px; height: 50vh; z-index: 12; pointer-events: auto;",
                    ),
            ):
                bookmark_column(state, ctrl)
            ctrl.set_view(view)
            bookmark_form_panel(state, ctrl)
            nov_popup_panel(state, ctrl, nov_render_window)
            register_scripts(client)

    register_state_change_handlers(state, ctrl)
    return ctrl, view