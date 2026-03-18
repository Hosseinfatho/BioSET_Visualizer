# app.py
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from trame.app import get_server

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
    if hasattr(scene, "interactor") and scene.interactor is not None:
        ctrl.set_interactor(scene.interactor)

    if scene.streamer is not None:
        import asyncio

        _scale_bar_tick = [0]  # mutable so inner function can update

        async def _check_loaded_data_loop():
            """Periodically check if background loading has finished and apply to VTK; also apply NOV progressive resolution updates and adaptive scale bar."""
            while True:
                await asyncio.sleep(0.1)
                try:
                    updated = False
                    updated = False
                    if scene.streamer.check_and_apply_loaded_data():
                        updated = True
                    if scene.heatmap_lod is not None:
                        server_state = server.state
                        if scene.heatmap_lod.check_and_apply(scene.heatmap, server_state):
                            updated = True
                    if updated:
                        server.state.flush()  # push state changes (e.g. hierarchy level) before render
                        view.update()
                    if scene.streamer.process_nov_progressive_queue():
                        view.update()
                    _scale_bar_tick[0] += 1
                    if _scale_bar_tick[0] >= 5:
                        _scale_bar_tick[0] = 0
                        if getattr(server.state, "nov_panel_visible", False) and hasattr(ctrl, "update_nov_scale_bar"):
                            try:
                                ctrl.update_nov_scale_bar()
                                view.update()
                            except Exception:
                                pass
                except Exception as e:
                    print(f"[error] check_loaded_data: {e}")

        async def _nov_animation_loop():
            """Drive NOV camera transition and bookmark camera animation: one frame every 40ms."""
            while True:
                await asyncio.sleep(0.04)
                try:
                    if hasattr(ctrl, "nov_animation_tick"):
                        ctrl.nov_animation_tick()
                    if hasattr(ctrl, "bookmark_camera_animation_tick"):
                        ctrl.bookmark_camera_animation_tick()
                except Exception:
                    pass

        @ctrl.add("on_server_ready")
        def _start_check_loop(**_):
            asyncio.create_task(_check_loaded_data_loop())
            asyncio.create_task(_nov_animation_loop())

    if scene.streamer is not None:
        scene.streamer.set_render_callback(view.update)
    if scene.heatmap is not None:
        ctrl.set_heatmap(scene.heatmap)
    # Enable right-click tile picking/drill-down regardless of mesh availability.
    # (Mesh activation remains conditional inside the picker callback.)
    if scene.interactor is not None and hasattr(ctrl, "setup_right_click_picker"):
        ctrl.setup_right_click_picker(scene.interactor)
    if scene.heatmap_lod is not None:
        ctrl.set_heatmap_lod(scene.heatmap_lod)
    if scene.mesh_manager is not None:
        ctrl.set_mesh_manager(scene.mesh_manager)
    if scene.heatmap_lod is not None:
        ctrl.set_heatmap_lod(scene.heatmap_lod)

    ctrl.get_available_llms()

    server.start()


if __name__ == "__main__":
    with contextlib.redirect_stdout(None):
        main()
