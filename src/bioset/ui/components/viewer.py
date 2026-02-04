# viewer.py
"""VTK viewer component."""

from __future__ import annotations

from trame.widgets import vtk, vuetify, html


def viewer(ctrl, render_window):
    """Create the VTK viewer container. Returns the view object."""
    
    with vuetify.VContainer(
        fluid=True,
        classes="pa-0 fill-height",
        style="position: relative;",
    ):
        view = vtk.VtkRemoteView(render_window, interactive_ratio=1.0)
        ctrl.view_update = view.update
        
        with vuetify.VBtn(
            #v_if="analysis_loaded && !right_drawer_open",
            fab=True,
            large=True,
            dark=True,
            outlined=True,
            style="position: absolute; top: 16px; right: 16px; z-index: 100;",
            click="right_drawer_open = true",
        ):
            vuetify.VIcon("mdi-chart-box-outline")
        
        def _on_ready(**_):
            render_window.Render()
            view.update()
        ctrl.on_server_ready.add(_on_ready)
    
    return view