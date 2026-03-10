# vtk_scene.py
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

import math

from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkRenderingCore import (
    vtkRenderer,
    vtkRenderWindow,
    vtkRenderWindowInteractor,
    vtkActor,
    vtkPolyDataMapper,
)
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleSwitch  # noqa
from vtkmodules.vtkFiltersSources import vtkArrowSource
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter
try:
    from vtkmodules.vtkCommonTransforms import vtkTransform
except ImportError:
    from vtkmodules.vtkCommonTransform import vtkTransform

import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
import vtkmodules.vtkRenderingVolumeOpenGL2  # noqa: F401

from ..config import VolumeConfig
from .volumes import SpacingConfig, make_volume_from_tiff, make_volume_from_zarr_s3, color_name_to_rgb
from ..streaming import VolumeStreamer
from ..streaming.heatmap_lod import HeatmapLOD
from ..streaming.lod import camera_distance_to_focal
from .heatmap import HeatmapRenderer
from .meshes import MeshManager


# Axis length (smaller = smaller arrows) and camera distance (larger = more margin, no clipping when rotating).
NOV_AXIS_LENGTH = 0.5
NOV_AXIS_CAMERA_DIST = 2.5


def _make_axis_arrow(scale, rotate_axis, rotate_deg, r, g, b):
    """Arrow actor along +X, scaled and rotated. rotate_axis: 'Z' or 'Y', rotate_deg in degrees."""
    arrow = vtkArrowSource()
    arrow.SetTipLength(0.25)
    arrow.SetTipRadius(0.1)
    arrow.SetShaftRadius(0.03)
    tr = vtkTransform()
    tr.Scale(scale, scale, scale)
    if rotate_axis == "Z":
        tr.RotateZ(rotate_deg)
    elif rotate_axis == "Y":
        tr.RotateY(rotate_deg)
    tf = vtkTransformPolyDataFilter()
    tf.SetInputConnection(arrow.GetOutputPort())
    tf.SetTransform(tr)
    mapper = vtkPolyDataMapper()
    mapper.SetInputConnection(tf.GetOutputPort())
    actor = vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(r, g, b)
    return actor


def _make_axis_label(text, pos_x, pos_y, pos_z, r, g, b):
    """3D text label at (pos_x, pos_y, pos_z) with RGB color, at end of axis arrow (font at half size)."""
    try:
        from vtkmodules.vtkRenderingFreeType import vtkVectorText
    except Exception:
        return None
    vec_text = vtkVectorText()
    vec_text.SetText(text)
    tr = vtkTransform()
    tr.Translate(pos_x, pos_y, pos_z)
    tr.Scale(0.06, 0.06, 0.06)  # half of previous 0.12
    tf = vtkTransformPolyDataFilter()
    tf.SetInputConnection(vec_text.GetOutputPort())
    tf.SetTransform(tr)
    mapper = vtkPolyDataMapper()
    mapper.SetInputConnection(tf.GetOutputPort())
    actor = vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(r, g, b)
    return actor


def _create_nov_axis_renderer(nov_renderer, nov_render_window, streamer):
    """Top-right viewport: XYZ as arrows with X,Y,Z labels; transparent so NOV view shows through; synced to main NOV camera."""
    axis_renderer = vtkRenderer()
    axis_renderer.SetViewport(0.72, 0.66, 0.99, 0.99)
    axis_renderer.SetBackground(0.0, 0.0, 0.0)
    axis_renderer.SetBackgroundAlpha(0.0)
    axis_renderer.SetLayer(1)  # draw on top of main NOV view with transparent background
    # Arrows: X=red, Y=green, Z=blue (vtkArrowSource is +X by default; rotate for Y, Z).
    axis_renderer.AddActor(_make_axis_arrow(NOV_AXIS_LENGTH, None, 0, 1.0, 0.0, 0.0))
    axis_renderer.AddActor(_make_axis_arrow(NOV_AXIS_LENGTH, "Z", 90, 0.0, 1.0, 0.0))
    axis_renderer.AddActor(_make_axis_arrow(NOV_AXIS_LENGTH, "Y", 90, 0.0, 0.0, 1.0))
    # Labels at end of each arrow (X, Y, Z).
    label_dist = NOV_AXIS_LENGTH * 1.15
    for actor in (
        _make_axis_label("X", label_dist, 0, 0, 1.0, 0.0, 0.0),
        _make_axis_label("Y", 0, label_dist, 0, 0.0, 1.0, 0.0),
        _make_axis_label("Z", 0, 0, label_dist, 0.0, 0.0, 1.0),
    ):
        if actor is not None:
            axis_renderer.AddActor(actor)
    axis_renderer.ResetCamera()
    nov_render_window.AddRenderer(axis_renderer)
    streamer.nov_axis_renderer = axis_renderer

    def _sync_nov_axis_camera(obj, evt):
        if not getattr(streamer, "nov_axis_renderer", None) or not getattr(streamer, "nov_renderer", None):
            return
        main_cam = streamer.nov_renderer.GetActiveCamera()
        axis_cam = streamer.nov_axis_renderer.GetActiveCamera()
        pos = main_cam.GetPosition()
        fp = main_cam.GetFocalPoint()
        vup = main_cam.GetViewUp()
        dx = pos[0] - fp[0]
        dy = pos[1] - fp[1]
        dz = pos[2] - fp[2]
        n = math.sqrt(dx * dx + dy * dy + dz * dz)
        if n >= 1e-12:
            axis_cam.SetPosition(dx / n * NOV_AXIS_CAMERA_DIST, dy / n * NOV_AXIS_CAMERA_DIST, dz / n * NOV_AXIS_CAMERA_DIST)
        axis_cam.SetFocalPoint(0.0, 0.0, 0.0)
        axis_cam.SetViewUp(vup[0], vup[1], vup[2])
        axis_cam.SetViewAngle(main_cam.GetViewAngle())
        streamer.nov_axis_renderer.ResetCameraClippingRange()

    # Sync axis camera with main NOV camera before each render (manual rotate + animation).
    nov_render_window.AddObserver("RenderEvent", _sync_nov_axis_camera)

@dataclass
class VtkScene:
    renderer: vtkRenderer
    render_window: vtkRenderWindow
    interactor: vtkRenderWindowInteractor
    streamer: Optional[VolumeStreamer] = None
    heatmap: Optional[HeatmapRenderer] = None
    heatmap_lod: Optional[HeatmapLOD] = None
    nov_renderer: Optional[vtkRenderer] = None
    nov_render_window: Optional[vtkRenderWindow] = None
    mesh_manager: Optional[MeshManager] = None  


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
    nov_renderer: Optional[vtkRenderer] = None
    nov_render_window: Optional[vtkRenderWindow] = None

    heatmap = HeatmapRenderer(renderer)

    # cube_actor = create_red_cube(center=(1000.0, 400.0, 25.0),size=50,opacity=0.5)  
    # cube_actor1 = create_red_cube(center=(900.0, 400.0, 0.0), size=50, opacity=1.0)  

    # renderer.AddActor(cube_actor)
    # renderer.AddActor(cube_actor1)

    if cfg.source == "zarr_s3":
        if not cfg.zarr_url:
            raise ValueError("cfg.zarr_url must be set for source='zarr_s3'")

        try:
            streamer = VolumeStreamer(
                cfg=cfg, renderer=renderer, render_window=render_window)

            nov_renderer = vtkRenderer()
            nov_render_window = vtkRenderWindow()
            nov_render_window.SetNumberOfLayers(2)  # layer 1 = axis overlay with transparent background
            try:
                nov_render_window.SetAlphaBitPlanes(1)
            except Exception:
                pass
            nov_render_window.AddRenderer(nov_renderer)
            nov_render_window.SetOffScreenRendering(1)
            nov_render_window.SetShowWindow(False)
            # Large size so the 3D view fills the popup (client scales to container); ~90% of max popup body
            nov_render_window.SetSize(1152, 648)
            nov_renderer.SetBackground(colors.GetColor3d(cfg.background))
            nov_interactor = vtkRenderWindowInteractor()
            nov_interactor.SetRenderWindow(nov_render_window)
            nov_interactor.Initialize()
            nov_style = vtkInteractorStyleSwitch()
            nov_style.SetCurrentStyleToTrackballCamera()
            nov_interactor.SetInteractorStyle(nov_style)
            nov_render_window.SetInteractor(nov_interactor)
            streamer.set_nov_renderer(nov_renderer, nov_render_window)
            _create_nov_axis_renderer(nov_renderer, nov_render_window, streamer)

            def _on_end_interaction(obj, evt):
                streamer.on_interaction_end()

            def _on_nov_end_interaction(obj, evt):
                nov_render_window.Render()
                if getattr(streamer, "nov_render_callback", None):
                    try:
                        streamer.nov_render_callback()
                    except Exception:
                        pass

            interactor.AddObserver("EndInteractionEvent", _on_end_interaction)
            nov_interactor.AddObserver("EndInteractionEvent", _on_nov_end_interaction)
        except Exception as e:
            streamer = None
            nov_renderer = None
            nov_render_window = None
            err = f"{type(e).__name__}: {e}"
            print(f"[bioset] Could not connect to zarr URL (SSL/network?). App will start without data. Error: {err}", file=sys.stderr)

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

    heatmap_lod: Optional[HeatmapLOD] = HeatmapLOD() if streamer is not None else None

    return VtkScene(
        renderer=renderer,
        render_window=render_window,
        interactor=interactor,
        streamer=streamer,
        heatmap=heatmap,
        heatmap_lod=heatmap_lod,
        nov_renderer=nov_renderer,
        nov_render_window=nov_render_window,
        mesh_manager=mesh_manager,
    )
