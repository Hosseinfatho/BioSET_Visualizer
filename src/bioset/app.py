# app.py
from __future__ import annotations

from pathlib import Path

from trame.app import get_server
from dotenv import load_dotenv

import argparse
import asyncio
import sys
import os

load_dotenv()

# Parse --logs argument early before any other imports
# Default: no logs (quiet mode). Use --logs to enable output.
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--logs", action="store_true", default=False, help="Enable console output")
_args, _ = _parser.parse_known_args()

if not _args.logs:
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")

# Python 3.10+ compatibility
if sys.version_info >= (3, 10):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

# Path to assets directory
ASSETS_DIR = Path(__file__).parent / "ui" / "assets"

from .config import default_config
from .scene import build_scene
from .ui import build_ui

import contextlib

def main():
    cfg = default_config()
    cfg = cfg.__class__(**{**cfg.__dict__,
                           "source": "zarr_s3",
                           #"zarr_url": "https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/0",
                           "channels": (), # loaded after upload from frontend
                           "base_sx": 0.14,
                           "base_sy": 0.14,
                           "base_sz": 0.28,
                           })

    scene = build_scene(cfg)

    server = get_server(client_type="vue2")
    server.enable_module({"serve": {"assets": str(ASSETS_DIR)}})

    ctrl, view = build_ui(
        server,
        scene.render_window,
        streamer=scene.streamer,
        nov_render_window=scene.nov_render_window,
    )

    if scene.streamer is not None:
        ctrl.set_streamer(scene.streamer)

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
    if scene.heatmap is not None:
        ctrl.set_heatmap(scene.heatmap)

    server.start()


if __name__ == "__main__":
    with contextlib.redirect_stdout(None):
        main()
