# gui.py
from __future__ import annotations

from trame.ui.vuetify import VAppLayout
from trame.widgets import html, vtk, vuetify, client

from .styles import register_styles
from .scripts import register_scripts
from .state import init_state, register_state_change_handlers
from .callbacks import register_callbacks
from .components import left_drawer, right_drawer, viewer, lineage_form_panel

def build_ui(server, render_window, streamer=None):
    ctrl = server.controller
    state = server.state
    
    init_state(state)

    with VAppLayout(server) as layout:
        register_styles(client)

        # UI components
        left_drawer(state, ctrl)
        right_drawer(state, ctrl)
        
        # VTK RENDERER
        with layout.root:
            view = viewer(ctrl, render_window)
            lineage_form_panel(state, ctrl)
            register_scripts(client)

    register_callbacks(ctrl, state, view, streamer)
    register_state_change_handlers(state, ctrl)
        
    return ctrl, view