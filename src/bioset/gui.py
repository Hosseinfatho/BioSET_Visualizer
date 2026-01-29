# gui.py
from __future__ import annotations

from trame.ui.vuetify import VAppLayout
from trame.widgets import vtk, vuetify


def build_ui(server, render_window, streamer=None):
    ctrl = server.controller
    state = server.state

    state.is_left_drawer_open = False  # left navigation drawer open or closed

    with VAppLayout(server) as layout:
        left_navigation_drawer()

        # Main content
        with layout.root:
            with vuetify.VContainer(
                fluid=True,
                classes="pa-0 fill-height",
            ):
                view = vtk.VtkRemoteView(render_window, interactive_ratio=1.0)
                
                def _on_ready(**_):
                    render_window.Render()
                    view.update()
                ctrl.on_server_ready.add(_on_ready)

    return ctrl, view

def left_navigation_drawer():
    with vuetify.VNavigationDrawer(
        app=True,
        permanent=True,  # Always visible
        mini_variant=("is_left_drawer_open",),  # state
        mini_variant_width=56,  
        width=256,  
        dark=True,  
        style="background-color: rgba(0, 0, 0, 0.8);",  
    ):
        # Title
        with vuetify.VListItem(
            click="is_left_drawer_open = !is_left_drawer_open",
            classes="px-2",
        ):
            with vuetify.VListItemAvatar():
                # vuetify.VIcon("mdi-cube-scan", color="primary")
                vuetify.VImg(
                    src="assets/icon.svg",
                    contain=True,
                    width="32",)
            
            with vuetify.VListItemContent():
                vuetify.VListItemTitle("BioSET", classes="text-h6")
                # vuetify.VListItemSubtitle("Volume Renderer")
        
        vuetify.VDivider()
        
        # Navigation items
        with vuetify.VList(dense=True, nav=True):
            with vuetify.VListItem(link=True):
                with vuetify.VListItemIcon():
                    vuetify.VIcon("mdi-layers")
                with vuetify.VListItemContent():
                    vuetify.VListItemTitle("Channels")
            
            with vuetify.VListItem(link=True):
                with vuetify.VListItemIcon():
                    vuetify.VIcon("mdi-tune")
                with vuetify.VListItemContent():
                    vuetify.VListItemTitle("Rendering")
            
            with vuetify.VListItem(link=True):
                with vuetify.VListItemIcon():
                    vuetify.VIcon("mdi-chart-bar")
                with vuetify.VListItemContent():
                    vuetify.VListItemTitle("Analysis")
            
            with vuetify.VListItem(link=True):
                with vuetify.VListItemIcon():
                    vuetify.VIcon("mdi-cog")
                with vuetify.VListItemContent():
                    vuetify.VListItemTitle("Settings")