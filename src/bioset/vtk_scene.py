from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple

from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow, vtkRenderWindowInteractor

# Ensures OpenGL2 backend is registered
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401

from .config import VolumeConfig
from .volume import SpacingConfig, make_volume_from_tiff


@dataclass
class VtkScene:
    renderer: vtkRenderer
    render_window: vtkRenderWindow
    interactor: vtkRenderWindowInteractor


def build_scene(cfg: VolumeConfig) -> VtkScene:
    colors = vtkNamedColors()

    renderer = vtkRenderer()
    render_window = vtkRenderWindow()
    render_window.AddRenderer(renderer)

    interactor = vtkRenderWindowInteractor()
    interactor.SetRenderWindow(render_window)
    # Trackball camera
    interactor.GetInteractorStyle().SetCurrentStyleToTrackballCamera()

    spacing = SpacingConfig(
        sx=cfg.base_sx * cfg.xy_scale,
        sy=cfg.base_sy * cfg.xy_scale,
        sz=cfg.base_sz,  # z unchanged
    )

    for ch in cfg.channels:
        path = cfg.tiff_path_for_channel(int(ch))
        vol = make_volume_from_tiff(
            path,
            spacing,
            shade=cfg.shade,
            linear_interpolation=cfg.linear_interpolation,
        )
        renderer.AddVolume(vol)

    renderer.SetBackground(colors.GetColor3d(cfg.background))
    renderer.ResetCameraClippingRange()
    renderer.ResetCamera()

    return VtkScene(renderer=renderer, render_window=render_window, interactor=interactor)
