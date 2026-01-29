# app.py
from __future__ import annotations

from pathlib import Path

from trame.app import get_server

import asyncio
import sys
# Python 3.10+ compatibility: ensure event loop exists before trame imports
# In Python 3.10+, asyncio.get_event_loop() raises RuntimeError if no loop exists
if sys.version_info >= (3, 10):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

# Path to assets directory
ASSETS_DIR = Path(__file__).parent / "assets"

from .config import default_config
from .vtk_scene import build_scene
from .gui import build_ui

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
    server.enable_module({"serve": {"assets": str(ASSETS_DIR)}})
    ctrl, view = build_ui(server, scene.render_window, streamer=scene.streamer)

    if scene.streamer is not None:
        import asyncio

        async def _check_loaded_data_loop():
            """Periodically check if background loading has finished and apply to VTK"""
            while True:
                await asyncio.sleep(0.1)
                try:
                    if scene.streamer.check_and_apply_loaded_data():
                        view.update()
                except Exception as e:
                    print(f"[error] check_loaded_data: {e}")

        @ctrl.add("on_server_ready")
        def _start_check_loop(**_):
            asyncio.create_task(_check_loaded_data_loop())

    if scene.streamer is not None:
        scene.streamer.set_render_callback(view.update)

    server.start()


if __name__ == "__main__":
    main()
