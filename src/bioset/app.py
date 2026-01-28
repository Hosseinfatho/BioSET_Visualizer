from __future__ import annotations

from trame.app import get_server
from trame.ui.vuetify import SinglePageLayout
from trame.widgets import vtk, vuetify

from .config import default_config
from .vtk_scene import build_scene


def build_ui(server, render_window):
    ctrl = server.controller

    with SinglePageLayout(server) as layout:
        layout.title.set_text("BioSET")

        with layout.content:
            with vuetify.VContainer(fluid=True, classes="pa-0 fill-height"):
                view = vtk.VtkLocalView(render_window)
                # view = vtk.VtkRemoteView(render_window)
                ctrl.on_server_ready.add(view.update)

    return ctrl


def main():
    cfg = default_config()
    scene = build_scene(cfg)

    server = get_server(client_type="vue2")
    build_ui(server, scene.render_window)

    server.start()


if __name__ == "__main__":
    main()
