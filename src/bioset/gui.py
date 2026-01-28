#gui.pyu
from __future__ import annotations

from trame.ui.vuetify import VAppLayout
from trame.widgets import vtk, vuetify

def build_ui(server, render_window, streamer=None):
    ctrl = server.controller
    state = server.state

    with VAppLayout(server) as layout:
        # layout.title.hide()  
        # layout.footer.hide()
        state = server.state

        # with layout.toolbar:
        #     vuetify.VImg(src="assets/icon.png", max_height=40, max_width=40, contain=True, classes="mr-2")
            # vuetify.VToolbarTitle("BioSET")

        with layout.root:
            with vuetify.VContainer(
                fluid=True, classes="pa-0 fill-height"
            ):
                view = vtk.VtkRemoteView(render_window, interactive_ratio=1.0)

                def _on_ready(**_):
                    render_window.Render()
                    view.update()
                ctrl.on_server_ready.add(_on_ready)

    return ctrl, view
