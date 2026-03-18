from __future__ import annotations

import math
from typing import Iterable, Optional, Tuple


def compute_scale_bar_for_camera(
    *,
    camera,
    render_window,
    target_px: int = 90,
    max_width_px: int = 400,
    nice_values: Iterable[float] = (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500),
    unit: str = "µm",
) -> Tuple[str, int]:
    """
    Compute a "nice" scale bar length and its pixel width for a VTK camera.

    Returns:
      (label, width_px)

    Notes:
    - This mirrors the NOV scale bar approach (camera distance + view angle),
      but is kept generic so it can be used by the main view without touching NOV code.
    - The unit label is purely cosmetic; this function does not apply voxel spacing.
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
    nice = tuple(float(x) for x in nice_values) if nice_values is not None else (1.0,)
    if not nice:
        nice = (1.0,)

    # Clamp extremes (same behavior as NOV implementation)
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
    nice_values: Iterable[float] = (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500),
    unit: str = "µm",
) -> Tuple[str, int]:
    """Convenience wrapper that fetches the active camera from a renderer."""
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
    )

