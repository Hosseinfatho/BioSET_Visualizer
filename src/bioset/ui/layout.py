# gui.py
from __future__ import annotations

from trame.ui.vuetify import VAppLayout
from trame.widgets import client

from .callbacks import register_callbacks
from .components import left_drawer, right_drawer, viewer
from .components.floating_chatbot import floating_chatbot_section
from .scripts import register_scripts
from .state import init_state, register_state_change_handlers
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
            view = viewer(ctrl, render_window)
            ctrl.set_view(view)
            bookmark_form_panel(state, ctrl)
            nov_popup_panel(state, ctrl, nov_render_window)
            register_scripts(client)

    register_state_change_handlers(state, ctrl)
    return ctrl, view