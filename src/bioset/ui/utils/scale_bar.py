from __future__ import annotations

import math
from typing import Iterable, Optional, Tuple


def _voxel_um_xy_for_lod(base_spacing_xy: float, component: int) -> float:
    """Voxel size in µm for X/Y at given LOD: base * 2^comp. comp=0 → base; comp=1 → 2×; comp=6 → 64×."""
    return float(base_spacing_xy) * (2 ** int(component))


def compute_scale_bar_for_camera(
    *,
    camera,
    render_window,
    target_px: int = 90,
    max_width_px: int = 400,
    nice_values: Optional[Iterable[float]] = (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500),
    unit: str = "µm",
    component: Optional[int] = None,
    base_spacing_xy: Optional[float] = None,
) -> Tuple[str, int]:
    """
    Compute a "nice" scale bar length and its pixel width for a VTK camera.

    Returns:
      (label, width_px)

    If component and base_spacing_xy are provided, the scale is quantized to multiples
    of the current LOD voxel size (base_spacing_xy * 2^component) so the bar is accurate
    to the real image voxel size at that level of detail.
    """
    if camera is None or render_window is None:
        return ("", 0)

    try:
        _w, h = render_window.GetSize()
    except Exception:
        return ("", 0)

    if h is None or h < 10:
        return ("", 0)

    try:
        pos = camera.GetPosition()
        fp = camera.GetFocalPoint()
    except Exception:
        return ("", 0)

    dist = math.sqrt(
        (pos[0] - fp[0]) ** 2 + (pos[1] - fp[1]) ** 2 + (pos[2] - fp[2]) ** 2
    )
    if dist < 1e-9:
        return ("", 0)

    try:
        view_angle_deg = camera.GetViewAngle()
    except Exception:
        return ("", 0)

    view_angle_rad = math.radians(float(view_angle_deg))
    world_height = 2.0 * dist * math.tan(view_angle_rad / 2.0)
    units_per_pixel = world_height / float(h)
    if units_per_pixel <= 0:
        return ("", 0)

    raw_units = float(target_px) * units_per_pixel

    # LOD-aware: quantize to multiples of voxel size at current component
    voxel_um: Optional[float] = None
    if component is not None and base_spacing_xy is not None and base_spacing_xy > 0:
        voxel_um = _voxel_um_xy_for_lod(base_spacing_xy, component)

    if voxel_um is not None and voxel_um > 0:
        # Nice multiples of one voxel: 1, 2, 5, 10, 20, 50, 100, 200, 500 voxels
        nice_multipliers = (1, 2, 5, 10, 20, 50, 100, 200, 500)
        nice = tuple(voxel_um * n for n in nice_multipliers)
        raw_voxels = raw_units / voxel_um
        # Choose nearest nice multiple of voxel_um
        scale_units = min(nice, key=lambda x: abs(x - raw_units))
        if raw_units < 0.25 * voxel_um:
            scale_units = voxel_um
        elif raw_units > 500 * voxel_um:
            scale_units = 500.0 * voxel_um
    else:
        nice = tuple(float(x) for x in nice_values) if nice_values else (1.0,)
        if not nice:
            nice = (1.0,)
        if raw_units < 0.25:
            scale_units = 0.5
        elif raw_units > 400:
            scale_units = 500.0
        else:
            scale_units = min(nice, key=lambda x: abs(x - raw_units))

    width_px = int(round(min(scale_units / units_per_pixel, float(max_width_px))))
    if scale_units >= 1:
        label = f"{scale_units:.0f} {unit}"
    else:
        label = f"{scale_units} {unit}"
    return (label, width_px)


def compute_scale_bar(
    *,
    renderer,
    render_window,
    target_px: int = 90,
    max_width_px: int = 400,
    nice_values: Optional[Iterable[float]] = (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500),
    unit: str = "µm",
    component: Optional[int] = None,
    base_spacing_xy: Optional[float] = None,
) -> Tuple[str, int]:
    """Convenience wrapper that fetches the active camera from a renderer. Uses LOD (component + base_spacing_xy) when provided."""
    if renderer is None:
        return ("", 0)
    try:
        cam = renderer.GetActiveCamera()
    except Exception:
        cam = None
    return compute_scale_bar_for_camera(
        camera=cam,
        render_window=render_window,
        target_px=target_px,
        max_width_px=max_width_px,
        nice_values=nice_values,
        unit=unit,
        component=component,
        base_spacing_xy=base_spacing_xy,
    )

