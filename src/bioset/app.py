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
    cfg = cfg.__class__(**{**cfg.__dict__,
        "source": "zarr_s3",
        "zarr_url": "https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/0",
        # "source": "tiff",
        "zarr_component": 4,
        "channels": (13,0,1,3),
        "base_sx": 0.14,
        "base_sy": 0.14,
        "base_sz": 0.28,
    })
    
    scene = build_scene(cfg)

    server = get_server(client_type="vue2")
    build_ui(server, scene.render_window)

    server.start()


if __name__ == "__main__":
    main()
