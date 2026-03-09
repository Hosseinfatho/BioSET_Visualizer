# vtk_scene.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow, vtkRenderWindowInteractor
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleSwitch  # noqa

import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
import vtkmodules.vtkRenderingVolumeOpenGL2  # noqa: F401

from ..config import VolumeConfig
from .volumes import SpacingConfig, make_volume_from_tiff, make_volume_from_zarr_s3, color_name_to_rgb
from ..streaming import VolumeStreamer
from ..streaming.heatmap_lod import HeatmapLOD
from .heatmap import HeatmapRenderer
from .meshes import MeshManager

@dataclass
class VtkScene:
    renderer: vtkRenderer
    render_window: vtkRenderWindow
    interactor: vtkRenderWindowInteractor
    streamer: Optional[VolumeStreamer] = None
    heatmap: Optional[HeatmapRenderer] = None
    mesh_manager: Optional[MeshManager] = None
    heatmap_lod: Optional[HeatmapLOD] = None


def build_scene(cfg: VolumeConfig) -> VtkScene:
    colors = vtkNamedColors()

    renderer = vtkRenderer()
    render_window = vtkRenderWindow()
    render_window.AddRenderer(renderer)
    render_window.SetOffScreenRendering(1)
    render_window.SetShowWindow(False)

    interactor = vtkRenderWindowInteractor()
    interactor.SetRenderWindow(render_window)
    interactor.Initialize()
    style = vtkInteractorStyleSwitch()
    style.SetCurrentStyleToTrackballCamera()
    interactor.SetInteractorStyle(style)

    renderer.SetBackground(colors.GetColor3d(cfg.background))

    streamer: Optional[VolumeStreamer] = None
    heatmap_lod: Optional[HeatmapLOD] = None

    heatmap = HeatmapRenderer(renderer)

    # cube_actor = create_red_cube(center=(1000.0, 400.0, 25.0),size=50,opacity=0.5)  
    # cube_actor1 = create_red_cube(center=(900.0, 400.0, 0.0), size=50, opacity=1.0)  

    # renderer.AddActor(cube_actor)
    # renderer.AddActor(cube_actor1)

    if cfg.source == "zarr_s3":
        if not cfg.zarr_url:
            raise ValueError("cfg.zarr_url must be set for source='zarr_s3'")

        streamer = VolumeStreamer(
            cfg=cfg, renderer=renderer, render_window=render_window)

        heatmap_lod = HeatmapLOD()

        def _on_end_interaction(obj, evt):
            desired_comp = streamer.on_interaction_end()
            if desired_comp is not None:
                heatmap_lod.on_camera_moved(desired_comp)

        interactor.AddObserver("EndInteractionEvent", _on_end_interaction)

    else:
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

    mesh_manager: Optional[MeshManager] = None
    if cfg.mesh_dir:
        mesh_manager = MeshManager(
            mesh_dir=cfg.mesh_dir,
            renderer=renderer,
            base_spacing=(cfg.base_sx, cfg.base_sy, cfg.base_sz),
        )

    return VtkScene(
        renderer=renderer,
        render_window=render_window,
        interactor=interactor,
        streamer=streamer,
        heatmap=heatmap,
        mesh_manager=mesh_manager,
        heatmap_lod=heatmap_lod,
    )
