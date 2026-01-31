# viewer.py
"""VTK viewer component."""

from __future__ import annotations

from trame.widgets import vtk, vuetify


def viewer(ctrl, render_window):
    """Create the VTK viewer container. Returns the view object."""
    
    with vuetify.VContainer(
        fluid=True,
        classes="pa-0 fill-height",
    ):
        view = vtk.VtkRemoteView(render_window, interactive_ratio=1.0)
        ctrl.view_update = view.update
        
        def _on_ready(**_):
            render_window.Render()
            view.update()
        ctrl.on_server_ready.add(_on_ready)
    
    return view