# vtk_scene.py
from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from typing import Optional

from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter
from vtkmodules.vtkFiltersSources import vtkArrowSource
from vtkmodules.vtkInteractionStyle import (  # noqa
    vtkInteractorStyleSwitch,
    vtkInteractorStyleTrackballCamera,
)
from vtkmodules.vtkRenderingCore import (
    vtkRenderer,
    vtkRenderWindow,
    vtkRenderWindowInteractor,
    vtkActor,
    vtkPolyDataMapper,
)

try:
    from vtkmodules.vtkCommonTransforms import vtkTransform
except ImportError:
    from vtkmodules.vtkCommonTransform import vtkTransform

import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
import vtkmodules.vtkRenderingVolumeOpenGL2  # noqa: F401

from ..config import VolumeConfig
from .volumes import SpacingConfig, make_volume_from_tiff, color_name_to_rgb
from ..streaming import VolumeStreamer
from ..streaming.heatmap_lod import HeatmapLOD
from ..streaming.viewport_plots import ViewportPlotComputer
from ..streaming.lod import camera_distance_to_focal
from .heatmap import HeatmapRenderer
from .meshes import MeshManager

# Axis length (smaller = smaller arrows) and camera distance (larger = more margin, no clipping when rotating).
NOV_AXIS_LENGTH = 0.5
NOV_AXIS_CAMERA_DIST = 2.5


class _SmoothZoomStyle(vtkInteractorStyleTrackballCamera):
    """Trackball camera style whose mouse-wheel zoom is handed to the streamer's
    eased-zoom animation (glide fast -> slow -> stop) instead of an instant
    per-notch dolly. Rotate / pan / right-drag behaviour is inherited unchanged.
    Falls back to the default instant dolly when no streamer is attached."""

    def SetStreamer(self, streamer):
        self._streamer = streamer

    def _sz(self):
        return getattr(self, "_streamer", None)

    def OnMouseWheelForward(self):
        s = self._sz()
        if s is not None and s.queue_zoom(+1):
            return
        super().OnMouseWheelForward()

    def OnMouseWheelBackward(self):
        s = self._sz()
        if s is not None and s.queue_zoom(-1):
            return
        super().OnMouseWheelBackward()


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
            axis_cam.SetPosition(dx / n * NOV_AXIS_CAMERA_DIST, dy / n * NOV_AXIS_CAMERA_DIST,
                                 dz / n * NOV_AXIS_CAMERA_DIST)
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
    viewport_plots: Optional[ViewportPlotComputer] = None
    nov_renderer: Optional[vtkRenderer] = None
    nov_render_window: Optional[vtkRenderWindow] = None
    mesh_manager: Optional[MeshManager] = None


def build_scene(cfg: VolumeConfig) -> VtkScene:
    colors = vtkNamedColors()

    renderer = vtkRenderer()
    render_window = vtkRenderWindow()
    # Layering:
    # - layer 0: heatmap fill (behind the volume)
    # - layer 1: main volume renderer (image)
    # - layer 2: heatmap outline (in front of the volume)
    render_window.SetNumberOfLayers(3)
    try:
        render_window.SetAlphaBitPlanes(1)
    except Exception:
        pass
    renderer.SetLayer(1)
    render_window.AddRenderer(renderer)
    render_window.SetOffScreenRendering(1)
    render_window.SetShowWindow(False)

    # Heatmap fill renderer (behind). As the layer-0 renderer it is the ONLY one
    # that clears the window's color buffer (layers >0 are transparent overlays),
    # so it owns the visible background. It must therefore carry the configured
    # background color, opaque — otherwise the window always reads back black and
    # background-color changes never show. Heatmap fill actors draw over it.
    heatmap_fill_renderer = vtkRenderer()
    heatmap_fill_renderer.SetLayer(0)
    heatmap_fill_renderer.SetBackground(colors.GetColor3d(cfg.background))
    heatmap_fill_renderer.SetBackgroundAlpha(1.0)
    heatmap_fill_renderer.SetActiveCamera(renderer.GetActiveCamera())  # share camera
    render_window.AddRenderer(heatmap_fill_renderer)

    # Heatmap outline renderer (in front)
    heatmap_outline_renderer = vtkRenderer()
    heatmap_outline_renderer.SetLayer(2)
    heatmap_outline_renderer.SetBackground(0.0, 0.0, 0.0)
    heatmap_outline_renderer.SetBackgroundAlpha(0.0)
    heatmap_outline_renderer.SetActiveCamera(renderer.GetActiveCamera())  # share camera
    render_window.AddRenderer(heatmap_outline_renderer)

    interactor = vtkRenderWindowInteractor()
    interactor.SetRenderWindow(render_window)
    interactor.Initialize()
    # Trackball camera with eased mouse-wheel zoom (streamer attached below).
    style = _SmoothZoomStyle()
    interactor.SetInteractorStyle(style)

    renderer.SetBackground(colors.GetColor3d(cfg.background))

    streamer: Optional[VolumeStreamer] = None
    nov_renderer: Optional[vtkRenderer] = None
    nov_render_window: Optional[vtkRenderWindow] = None
    _heatmap_lod_ref: list = [None]  # mutable so _on_end_interaction closure can access it
    _viewport_plots_ref: list = [None]

    heatmap = HeatmapRenderer(heatmap_fill_renderer, outline_renderer=heatmap_outline_renderer)

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
            # Route mouse-wheel zoom through the streamer's eased-zoom animation.
            style.SetStreamer(streamer)

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
                # Keep volume streamer + heatmap LOD + viewport plots in sync with camera zoom.
                streamer.on_interaction_end()
                from bioset.streaming.lod import camera_distance_to_focal, compute_visible_xy_roi_vox
                dist = camera_distance_to_focal(renderer.GetActiveCamera())
                if _heatmap_lod_ref[0] is not None:
                    _heatmap_lod_ref[0].on_camera_moved(dist)
                vp = _viewport_plots_ref[0]
                if vp is not None and vp._enabled:
                    try:
                        # Compute ROI at base resolution (component 0) for tile mapping
                        bounds = streamer._volume_bounds_world(0)
                        sp = streamer._spacing_for_component(0)
                        _, ydim, xdim = streamer._dims_for_component(0)
                        roi = compute_visible_xy_roi_vox(
                            renderer, bounds_world=bounds, sx=sp.sx, sy=sp.sy,
                            x_dim=xdim, y_dim=ydim, margin_vox=0,
                        )
                        # Convert ROI voxel coords to tally-block indices (128 voxels)
                        from bioset.analysis.constants import BLOCK_VOX
                        gx0 = roi.x0 // BLOCK_VOX
                        gx1 = (roi.x1 + BLOCK_VOX - 1) // BLOCK_VOX
                        gy0 = roi.y0 // BLOCK_VOX
                        gy1 = (roi.y1 + BLOCK_VOX - 1) // BLOCK_VOX
                        vp.on_camera_moved((gx0, gx1), (gy0, gy1))
                    except Exception as e:
                        print(f"[viewport_plots] ROI computation error: {e}")

            def _on_nov_end_interaction(obj, evt):
                nov_render_window.Render()
                if getattr(streamer, "nov_render_callback", None):
                    try:
                        streamer.nov_render_callback()
                    except Exception:
                        pass

            # Keep NOV scale bar responsive during zoom/pan by updating on interaction,
            # but throttle to avoid flooding the websocket/UI with updates.
            _nov_scale_bar_last_update = 0.0

            def _on_nov_interaction(obj, evt):
                nonlocal _nov_scale_bar_last_update
                now = time.time()
                if now - _nov_scale_bar_last_update < 0.10:  # 10 FPS max
                    return
                _nov_scale_bar_last_update = now
                if getattr(streamer, "nov_render_callback", None):
                    try:
                        streamer.nov_render_callback()
                    except Exception:
                        pass

            def _on_start_interaction(obj, evt):
                # Keep each channel's sharp texture while interacting as long as
                # the viewport stays inside the loaded ROI; the per-move handler
                # drops to the coarse base only when a zoom-out/large pan pushes
                # past it. The sharp ROI for the settled viewport streams in via
                # on_interaction_end -> async stream -> apply.
                try:
                    streamer.on_interaction_start()
                except Exception:
                    pass

            def _on_interaction(obj, evt):
                # Throttled inside the streamer; re-evaluates sharp<->base per
                # channel as the camera moves.
                try:
                    streamer.on_interaction_move()
                except Exception:
                    pass

            interactor.AddObserver("StartInteractionEvent", _on_start_interaction)
            interactor.AddObserver("InteractionEvent", _on_interaction)
            interactor.AddObserver("EndInteractionEvent", _on_end_interaction)
            nov_interactor.AddObserver("EndInteractionEvent", _on_nov_end_interaction)
            nov_interactor.AddObserver("InteractionEvent", _on_nov_interaction)
        except Exception as e:
            streamer = None
            nov_renderer = None
            nov_render_window = None
            err = f"{type(e).__name__}: {e}"
            print(f"[bioset] Could not connect to zarr URL (SSL/network?). App will start without data. Error: {err}",
                  file=sys.stderr)

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
            nov_renderer=nov_renderer,
        )

    heatmap_lod: Optional[HeatmapLOD] = HeatmapLOD(distance_rules=cfg.heatmap_distance_rules) if streamer is not None else None
    _heatmap_lod_ref[0] = heatmap_lod

    viewport_plots: Optional[ViewportPlotComputer] = ViewportPlotComputer() if streamer is not None else None
    _viewport_plots_ref[0] = viewport_plots

    return VtkScene(
        renderer=renderer,
        render_window=render_window,
        interactor=interactor,
        streamer=streamer,
        heatmap=heatmap,
        heatmap_lod=heatmap_lod,
        viewport_plots=viewport_plots,
        nov_renderer=nov_renderer,
        nov_render_window=nov_render_window,
        mesh_manager=mesh_manager,
    )
