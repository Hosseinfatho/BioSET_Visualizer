# app.py
from __future__ import annotations

from trame.app import get_server
from trame.ui.vuetify import SinglePageLayout
from trame.widgets import vtk, vuetify

from .config import default_config
from .vtk_scene import build_scene


def build_ui(server, render_window, streamer=None):
    ctrl = server.controller
    state = server.state

    with SinglePageLayout(server) as layout:
        layout.title.set_text("BioSET")

        with layout.content:
            with vuetify.VContainer(fluid=True, classes="pa-0 fill-height"):
                view = vtk.VtkRemoteView(render_window)
                
                def _on_ready(**_):
                    render_window.Render()
                    view.update()

                ctrl.on_server_ready.add(_on_ready)
    
    if streamer is not None:
        import asyncio
        
        async def _check_loaded_data_loop():
            """Periodically check if background loading has finished and apply to VTK"""
            while True:
                await asyncio.sleep(0.1)  
                try:
                    if streamer.check_and_apply_loaded_data():
                        view.update()
                except Exception as e:
                    print(f"[error] check_loaded_data: {e}")
        
        @ctrl.add("on_server_ready")
        def _start_check_loop(**_):
            asyncio.create_task(_check_loaded_data_loop())

    return ctrl, view


def main():
    cfg = default_config()
    cfg = cfg.__class__(**{**cfg.__dict__,
        "source": "zarr_s3",
        "zarr_url": "https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/0",
        "channels": (0, 3, 13),
        "base_sx": 0.14,
        "base_sy": 0.14,
        "base_sz": 0.28,
    })
    
    scene = build_scene(cfg)

    server = get_server(client_type="vue2")
    ctrl, view = build_ui(server, scene.render_window, streamer=scene.streamer)
    
    if scene.streamer is not None:
        scene.streamer.set_render_callback(view.update)

    server.start()


if __name__ == "__main__":
    main()