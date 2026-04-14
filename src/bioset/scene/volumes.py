from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import dask.array as da
import numpy as np
from ome_zarr.io import parse_url
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkCommonDataModel import vtkImageData
from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction
from vtkmodules.vtkIOImage import vtkTIFFReader
from vtkmodules.vtkImagingCore import vtkImageChangeInformation
from vtkmodules.vtkRenderingCore import vtkColorTransferFunction, vtkVolume, vtkVolumeProperty
from vtkmodules.vtkRenderingVolume import vtkGPUVolumeRayCastMapper

# Default tint if none provided
DEFAULT_TINT_RGB = (0.2, 0.8, 1.0)


def color_name_to_rgb(color_name: str) -> tuple[float, float, float]:
    """Convert a named color (e.g., 'Cyan') to an RGB tuple (0-1 range)."""
    colors = vtkNamedColors()
    rgb = colors.GetColor3d(color_name)
    return (rgb.GetRed(), rgb.GetGreen(), rgb.GetBlue())


@dataclass(frozen=True)
class SpacingConfig:
    sx: float
    sy: float
    sz: float


def vtk_image_from_numpy_zyx_u16(
    np_vol_zyx: np.ndarray,
    *,
    spacing: SpacingConfig,
    origin_xyz: tuple[float, float, float],
) -> vtkImageData:
    np_vol_zyx = np.ascontiguousarray(np_vol_zyx).astype(np.uint16, copy=False)
    z, y, x = np_vol_zyx.shape

    vtk_arr = numpy_to_vtk(np_vol_zyx.ravel(order="C"), deep=True)
    vtk_arr.SetName("scalars")

    img = vtkImageData()
    img.SetDimensions(x, y, z)
    img.SetExtent(0, x - 1, 0, y - 1, 0, z - 1)
    img.SetOrigin(*origin_xyz)
    img.SetSpacing(spacing.sx, spacing.sy, spacing.sz)
    img.GetPointData().SetScalars(vtk_arr)
    img.Modified()
    return img


def _make_volume_from_vtk_image(
    image: vtkImageData,
    *,
    tint_rgb: tuple[float, float, float] = DEFAULT_TINT_RGB,
    shade: bool = True,
    linear_interpolation: bool = True,
) -> vtkVolume:
    color_tf, opacity_tf = build_histogram_tf(image, tint_rgb=tint_rgb)

    prop = vtkVolumeProperty()
    prop.SetColor(color_tf)
    prop.SetScalarOpacity(opacity_tf)
    prop.SetInterpolationTypeToLinear(
    ) if linear_interpolation else prop.SetInterpolationTypeToNearest()
    apply_volume_properties(prop, image, shade=shade)

    # mapper = vtkFixedPointVolumeRayCastMapper()
    mapper = vtkGPUVolumeRayCastMapper()
    mapper.SetInputData(image)

    vol = vtkVolume()
    vol.SetMapper(mapper)
    vol.SetProperty(prop)
    vol.SetPickable(False)
    
    return vol


def make_volume_from_dask_zyx(
    vol_zyx: da.Array,
    *,
    spacing: SpacingConfig,
    origin_xyz: tuple[float, float, float],
    tint_rgb: tuple[float, float, float] = DEFAULT_TINT_RGB,
    shade: bool = True,
    linear_interpolation: bool = True,
) -> vtkVolume:
    np_vol = vol_zyx.compute()
    img = vtk_image_from_numpy_zyx_u16(
        np_vol, spacing=spacing, origin_xyz=origin_xyz)
    return _make_volume_from_vtk_image(img, tint_rgb=tint_rgb, shade=shade, linear_interpolation=linear_interpolation)


def _make_volume_from_vtk_image(
    image: vtkImageData,
    *,
    tint_rgb: tuple[float, float, float] = DEFAULT_TINT_RGB,
    shade: bool = True,
    linear_interpolation: bool = True,
) -> vtkVolume:
    color_tf, opacity_tf = build_histogram_tf(image, tint_rgb=tint_rgb)

    prop = vtkVolumeProperty()
    prop.SetColor(color_tf)
    prop.SetScalarOpacity(opacity_tf)
    prop.SetInterpolationTypeToLinear(
    ) if linear_interpolation else prop.SetInterpolationTypeToNearest()
    apply_volume_properties(prop, image, shade=shade)

    # mapper = vtkFixedPointVolumeRayCastMapper()
    mapper = vtkGPUVolumeRayCastMapper()
    mapper.SetInputData(image)

    vol = vtkVolume()
    vol.SetMapper(mapper)
    vol.SetProperty(prop)
    vol.SetPickable(False)
    
    return vol


def make_volume_from_tiff(
    tiff_path: Path,
    spacing: SpacingConfig,
    *,
    tint_rgb: tuple[float, float, float] = DEFAULT_TINT_RGB,
    shade: bool = True,
    linear_interpolation: bool = True,
) -> vtkVolume:
    """
    Create a vtkVolume from a TIFF using:
    - vtkTIFFReader -> vtkImageChangeInformation (spacing) -> vtkFixedPointVolumeRayCastMapper
    - Simple default opacity/color transfer functions based on scalar range.
    """
    if not tiff_path.exists():
        raise FileNotFoundError(f"TIFF not found: {tiff_path}")

    reader = vtkTIFFReader()
    reader.SetFileName(str(tiff_path))

    change = vtkImageChangeInformation()
    change.SetInputConnection(reader.GetOutputPort())
    change.SetOutputSpacing(spacing.sx, spacing.sy, spacing.sz)
    change.Update()

    return _make_volume_from_vtk_image(
        change.GetOutput(),
        tint_rgb=tint_rgb,
        shade=shade,
        linear_interpolation=linear_interpolation,
    )


def make_volume_from_zarr_s3(
    zarr_url: str,
    *,
    component: int,
    channel: int,
    t_index: int,
    spacing: SpacingConfig,
    tint_rgb: tuple[float, float, float] = DEFAULT_TINT_RGB,
    shade: bool = True,
    linear_interpolation: bool = True,
    max_bytes: int = 2_000_000_000,
) -> vtkVolume:
    root = parse_url(zarr_url, mode="r")
    store = root.store

    darr = da.from_zarr(store, component=str(component))
    vol = darr[t_index, channel, :, :, :]

    est_bytes = int(np.prod(vol.shape)) * np.dtype(vol.dtype).itemsize
    if est_bytes > max_bytes:
        raise MemoryError(f"Volume ~{est_bytes/1e9:.2f} GB, too big.")

    np_vol = vol.compute()
    np_vol = np_vol.astype(np.uint16, copy=False)
    np_vol = np.ascontiguousarray(np_vol)
    if np_vol.ndim != 3:
        raise ValueError(f"Expected (z,y,x), got {np_vol.shape}")

    z, y, x = np_vol.shape

    vtk_arr = numpy_to_vtk(np_vol.ravel(order="C"), deep=True)
    vtk_arr.SetName("scalars")

    img = vtkImageData()
    img.SetDimensions(x, y, z)
    img.SetExtent(0, x - 1, 0, y - 1, 0, z - 1)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(spacing.sx, spacing.sy, spacing.sz)
    img.GetPointData().SetScalars(vtk_arr)
    img.Modified()

    return _make_volume_from_vtk_image(img, tint_rgb=tint_rgb, shade=shade, linear_interpolation=linear_interpolation)


# transfer function and property helpers
def _percentiles_from_vtk_image(image, sample_max=2_000_000):

    from vtkmodules.util.numpy_support import vtk_to_numpy

    scalars = image.GetPointData().GetScalars()
    if scalars is None:
        r0, r1 = image.GetScalarRange()
        return r0, r1, r0, r1, r0, r1

    arr = vtk_to_numpy(scalars)
    if arr.size == 0:
        r0, r1 = image.GetScalarRange()
        return r0, r1, r0, r1, r0, r1

    if arr.size > sample_max:
        idx = np.random.choice(arr.size, size=sample_max, replace=False)
        arr = arr[idx]

    arr = arr.astype(np.float32, copy=False)

    p01, p10, p50, p90, p99, p995 = np.percentile(
        arr, [1, 10, 50, 90, 99, 99.5])
    r0, r1 = float(np.min(arr)), float(np.max(arr))
    return r0, r1, float(p01), float(p10), float(p99), float(p995)


def build_histogram_tf(image, *, tint_rgb=(0.2, 0.8, 1.0)):
    r0, r1, p01, p10, p99, p995 = _percentiles_from_vtk_image(image)

    if r1 <= r0 + 1e-6:
        opacity = vtkPiecewiseFunction()
        opacity.AddPoint(r0, 0.0)
        opacity.AddPoint(r1, 0.0)
        color = vtkColorTransferFunction()
        color.AddRGBPoint(r0, *tint_rgb)
        color.AddRGBPoint(r1, *tint_rgb)
        return color, opacity, (r0, r1, r1)

    lo = max(p10, r0)
    hi = max(p99, lo + 1.0)

    opacity = vtkPiecewiseFunction()
    opacity.AddPoint(r0, 0.0)
    opacity.AddPoint(lo, 0.0)
    opacity.AddPoint(lo + 0.25 * (hi - lo), 0.015)
    opacity.AddPoint(lo + 0.60 * (hi - lo), 0.06)
    opacity.AddPoint(hi, 0.12)
    opacity.AddPoint(max(p995, hi), 0.12)

    color = vtkColorTransferFunction()
    color.AddRGBPoint(r0, 0.0, 0.0, 0.0)
    color.AddRGBPoint(lo, 0.0, 0.0, 0.0)
    color.AddRGBPoint(hi, *tint_rgb)
    color.AddRGBPoint(r1, *tint_rgb)

    return color, opacity, (p10, p99, p995)


def apply_volume_properties(prop: vtkVolumeProperty, image, *, shade=True):
    if shade:
        prop.ShadeOn()
        prop.SetAmbient(0.8)
        prop.SetDiffuse(1.0)
        prop.SetSpecular(0.5)
        prop.SetSpecularPower(32.0)
    else:
        prop.ShadeOff()

    prop.SetInterpolationTypeToLinear()

    sx, sy, sz = image.GetSpacing()
    prop.SetScalarOpacityUnitDistance(max(1e-6, 3.0 * min(sx, sy, sz)))

def build_tf_with_range(
    data_range: Tuple[float, float],
    percentile_range: Tuple[float, float, float],
    intensity_range_pct: Tuple[float, float],  
    tint_rgb: Tuple[float, float, float],
) -> Tuple[vtkColorTransferFunction, vtkPiecewiseFunction]:
    """
    Build transfer functions with user-specified intensity range.
    
    Args:
        data_range: (min, max) scalar values from the data
        percentile_range: (p10, p99, p995) percentile ranges 
        intensity_range_pct: [low%, high%] from slider (0-100)
        tint_rgb: Channel color as RGB tuple (0-1 range)
    """
    r0, r1 = data_range
    p10, p99, p995 = percentile_range

    pct_lo, pct_hi = intensity_range_pct

    bounded_lo = max(p10, r0)
    bounded_hi = max(p99, bounded_lo + 1.0)

    lo = bounded_lo + (pct_lo / 100.0) * (bounded_hi - bounded_lo)
    hi = bounded_lo + (pct_hi / 100.0) * (bounded_hi - bounded_lo)

    opacity = vtkPiecewiseFunction()
    color = vtkColorTransferFunction()

    # When both ends are the same, make channel fully transparent
    if hi <= lo:
        opacity.AddPoint(r0, 0.0)
        opacity.AddPoint(r1, 0.0)
        color.AddRGBPoint(r0, 0.0, 0.0, 0.0)
        color.AddRGBPoint(r1, 0.0, 0.0, 0.0)
        return color, opacity

    opacity.AddPoint(r0, 0.0)
    opacity.AddPoint(lo, 0.0)
    opacity.AddPoint(lo + 0.25 * (hi - lo), 0.015)
    opacity.AddPoint(lo + 0.60 * (hi - lo), 0.06)
    opacity.AddPoint(hi, 0.12)
    opacity.AddPoint(max(p995, hi), 0.12)

    color.AddRGBPoint(r0, 0.0, 0.0, 0.0)
    color.AddRGBPoint(lo, 0.0, 0.0, 0.0)
    color.AddRGBPoint(hi, *tint_rgb)
    color.AddRGBPoint(r1, *tint_rgb)

    return color, opacity