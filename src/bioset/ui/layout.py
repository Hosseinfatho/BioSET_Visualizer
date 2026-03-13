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


def build_ui(server, render_window, streamer=None):
    ctrl = server.controller
    state = server.state
    
    init_state(state)

    with VAppLayout(server) as layout:
        register_styles(client)

        # UI components
        left_drawer(state, ctrl)
        right_drawer(state, ctrl)

        floating_chatbot_section(state, ctrl)

        # VTK RENDERER
        with layout.root:
            view = viewer(ctrl, render_window)
            register_scripts(client)

    register_callbacks(ctrl, state, view, streamer)
    register_state_change_handlers(state, ctrl)
        
    return ctrl, view