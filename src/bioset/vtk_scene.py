# vtk_scene.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow, vtkRenderWindowInteractor
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleSwitch  # noqa

import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
import vtkmodules.vtkRenderingVolumeOpenGL2  # noqa: F401

from .config import VolumeConfig
from .volume import SpacingConfig, make_volume_from_tiff, make_volume_from_zarr_s3, color_name_to_rgb
from .volume_streamer import VolumeStreamer


@dataclass
class VtkScene:
    renderer: vtkRenderer
    render_window: vtkRenderWindow
    interactor: vtkRenderWindowInteractor
    streamer: Optional[VolumeStreamer] = None


def build_scene(cfg: VolumeConfig) -> VtkScene:
    colors = vtkNamedColors()

    renderer = vtkRenderer()
    render_window = vtkRenderWindow()
    render_window.AddRenderer(renderer)
    render_window.SetOffScreenRendering(1)
    render_window.SetShowWindow(False)

    interactor = vtkRenderWindowInteractor()
    interactor.Initialize()
    interactor.SetRenderWindow(render_window)
    style = vtkInteractorStyleSwitch()
    style.SetCurrentStyleToTrackballCamera()
    interactor.SetInteractorStyle(style)

    renderer.SetBackground(colors.GetColor3d(cfg.background))

    streamer: Optional[VolumeStreamer] = None

    # --- NEW: streaming mode for zarr_s3 ---
    if cfg.source == "zarr_s3":
        if not cfg.zarr_url:
            raise ValueError("cfg.zarr_url must be set for source='zarr_s3'")

        streamer = VolumeStreamer(cfg=cfg, renderer=renderer, render_window=render_window)

        def _on_end_interaction(obj, evt):
            streamer.on_interaction_end()

        # Only update after user finishes interaction (pan/zoom/rotate)
        interactor.AddObserver("EndInteractionEvent", _on_end_interaction)

    else:
        # --- Old behavior (TIFF) ---
        spacing = SpacingConfig(
            sx=cfg.base_sx * cfg.xy_scale,
            sy=cfg.base_sy * cfg.xy_scale,
            sz=cfg.base_sz,
        )

        for i, ch in enumerate(cfg.channels):
            color_name = cfg.channel_colors[i % len(cfg.channel_colors)]
            tint_rgb = color_name_to_rgb(color_name)

            vol = make_volume_from_tiff(
                cfg.tiff_path_for_channel(int(ch)),
                spacing,
                tint_rgb=tint_rgb,
                shade=cfg.shade,
                linear_interpolation=cfg.linear_interpolation,
            )
            renderer.AddVolume(vol)
            print(f"Added volume for channel {ch} with color {color_name}")

    renderer.ResetCameraClippingRange()
    renderer.ResetCamera()

    return VtkScene(
        renderer=renderer,
        render_window=render_window,
        interactor=interactor,
        streamer=streamer,
    )
