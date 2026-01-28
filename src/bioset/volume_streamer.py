# volume_streamer.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import dask.array as da

from .camera import (
    camera_distance_to_focal,
    choose_component,
    compute_visible_xy_roi_vox,
    ROI,
)
from .zarr import ZarrMultiscaleSource
from .volume import SpacingConfig, make_volume_from_dask_zyx, color_name_to_rgb

@dataclass
class ChannelState:
    component: int
    roi: ROI

class VolumeStreamer:
    def __init__(self, *, cfg, renderer, render_window):
        self.cfg = cfg
        self.renderer = renderer
        self.render_window = render_window

        self.render_callback = None  # optionally set to trame view.update

        max_bytes = int(cfg.cache_size_gb * (1024**3))
        self.zsrc = ZarrMultiscaleSource(
            url=cfg.zarr_url,
            cache_enabled=cfg.cache_enabled,
            cache_dir=cfg.cache_dir,
            cache_size_bytes=max_bytes,
        )

        self.volumes = {}      # ch -> vtkVolume
        self.state: Dict[int, ChannelState] = {}
        
        self._last_component = None

        self._init_low_res_full()

    def set_render_callback(self, fn):
        self.render_callback = fn

    def _spacing_for_component(self, component: int) -> SpacingConfig:
        scale = float(2 ** component)
        return SpacingConfig(
            sx=self.cfg.base_sx * scale,
            sy=self.cfg.base_sy * scale,
            sz=self.cfg.base_sz,  # assuming Z is not downsampled for your dataset
        )

    def _dims_for_component(self, component: int) -> Tuple[int, int, int]:
        # darr shape is (t,c,z,y,x)
        shape = self.zsrc.shape_tczyx(component)
        _, _, z, y, x = shape
        return (z, y, x)

    def _volume_bounds_world(self, component: int):
        spacing = self._spacing_for_component(component)
        z, y, x = self._dims_for_component(component)
        xmin, ymin, zmin = 0.0, 0.0, 0.0
        xmax = x * spacing.sx
        ymax = y * spacing.sy
        zmax = z * spacing.sz
        return (xmin, xmax, ymin, ymax, zmin, zmax)

    def _init_low_res_full(self):
        comp = self.cfg.start_component
        spacing = self._spacing_for_component(comp)

        print(f"[stream] init: loading FULL volumes at component={comp}")

        darr = self.zsrc.array(comp)  # (t,c,z,y,x)
        _, _, z, y, x = darr.shape

        for i, ch in enumerate(self.cfg.channels):
            color_name = self.cfg.channel_colors[i % len(self.cfg.channel_colors)]
            tint = color_name_to_rgb(color_name)

            vol_zyx = darr[self.cfg.zarr_time_index, int(ch), :, :, :]  # full
            vtkvol = make_volume_from_dask_zyx(
                vol_zyx,
                spacing=spacing,
                origin_xyz=(0.0, 0.0, 0.0),
                tint_rgb=tint,
                shade=self.cfg.shade,
                linear_interpolation=self.cfg.linear_interpolation,
            )
            self.renderer.AddVolume(vtkvol)
            self.volumes[int(ch)] = vtkvol

            self.state[int(ch)] = ChannelState(
                component=comp,
                roi=ROI(0, int(x), 0, int(y)),
            )
            print(f"[stream] added ch={ch} color={color_name} dims=(z={z},y={y},x={x})")

        self.renderer.ResetCameraClippingRange()
        self.renderer.ResetCamera()
        self._render()

    def _render(self):
        self.render_window.Render()
        if self.render_callback is not None:
            self.render_callback()

    def on_interaction_end(self):
        cam = self.renderer.GetActiveCamera()
        dist = camera_distance_to_focal(cam)
        print(f"[camera] EndInteraction distance={dist:.3f}")

        desired_comp = choose_component(
            dist,
            self.cfg.distance_rules,
            min_component=self.cfg.min_component,
            max_component=self.cfg.max_component,
        )
        
        if desired_comp != self._last_component:
            print(f"[stream] switch component {self._last_component} -> {desired_comp}")
            self._last_component = desired_comp

        spacing = self._spacing_for_component(desired_comp)
        zdim, ydim, xdim = self._dims_for_component(desired_comp)
        bounds = self._volume_bounds_world(desired_comp)

        roi = compute_visible_xy_roi_vox(
            self.renderer,
            bounds_world=bounds,
            sx=spacing.sx,
            sy=spacing.sy,
            x_dim=xdim,
            y_dim=ydim,
            margin_vox=self.cfg.roi_margin_vox,
        )

        print(f"[stream] interaction_end: cam_dist={dist:.2f} -> comp={desired_comp} roi=({roi.x0}:{roi.x1}, {roi.y0}:{roi.y1})")

        darr = self.zsrc.array(desired_comp)  # (t,c,z,y,x)

        # reload each channel if component or ROI changed
        updated_any = False
        for ch in self.cfg.channels:
            ch = int(ch)
            prev = self.state.get(ch)
            need = (prev is None) or (prev.component != desired_comp) or (prev.roi != roi)
            if not need:
                continue

            if prev and prev.component != desired_comp:
                print(f"[stream] LEVEL SWITCH ch={ch}: {prev.component} -> {desired_comp}")

            vol_zyx = darr[self.cfg.zarr_time_index, ch, :, roi.y0:roi.y1, roi.x0:roi.x1]
            origin_xyz = (roi.x0 * spacing.sx, roi.y0 * spacing.sy, 0.0)

            # rebuild vtkImageData and swap mapper input
            vtkvol = self.volumes[ch]
            color_name = self.cfg.channel_colors[list(self.cfg.channels).index(ch) % len(self.cfg.channel_colors)]
            tint = color_name_to_rgb(color_name)

            new_vtkvol = make_volume_from_dask_zyx(
                vol_zyx,
                spacing=spacing,
                origin_xyz=origin_xyz,
                tint_rgb=tint,
                shade=self.cfg.shade,
                linear_interpolation=self.cfg.linear_interpolation,
            )

            self.renderer.RemoveVolume(vtkvol)
            self.renderer.AddVolume(new_vtkvol)
            self.volumes[ch] = new_vtkvol
            self.state[ch] = ChannelState(component=desired_comp, roi=roi)

            updated_any = True

        if updated_any:
            self.renderer.ResetCameraClippingRange()
            self._render()
