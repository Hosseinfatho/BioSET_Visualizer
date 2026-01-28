from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import numpy as np

from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction
from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkIOImage import vtkTIFFReader
from vtkmodules.vtkImagingCore import vtkImageChangeInformation
from vtkmodules.vtkRenderingCore import vtkColorTransferFunction, vtkVolume, vtkVolumeProperty
from vtkmodules.vtkRenderingVolume import vtkFixedPointVolumeRayCastMapper
from vtkmodules.vtkCommonDataModel import vtkImageData
from vtkmodules.util.numpy_support import numpy_to_vtk

import dask.array as da
from ome_zarr.io import parse_url

@dataclass(frozen=True)
class SpacingConfig:
    sx: float
    sy: float
    sz: float

def _make_volume_from_vtk_image(
    image: vtkImageData,
    *,
    shade: bool = True,
    linear_interpolation: bool = True,
) -> vtkVolume:
    r0, r1 = image.GetScalarRange()
    rmid = 0.5 * (r0 + r1)

    opacity_tf = vtkPiecewiseFunction()
    opacity_tf.AddPoint(r0, 0.0)
    opacity_tf.AddPoint(rmid, 0.2)
    opacity_tf.AddPoint(r1, 0.2)

    color_tf = vtkColorTransferFunction()
    color_tf.AddRGBPoint(r0, 0.0, 0.0, 1.0)
    color_tf.AddRGBPoint(rmid, 1.0, 1.0, 1.0)
    color_tf.AddRGBPoint(r1, 1.0, 0.0, 0.0)

    prop = vtkVolumeProperty()
    prop.SetColor(color_tf)
    prop.SetScalarOpacity(opacity_tf)
    prop.SetInterpolationTypeToLinear() if linear_interpolation else prop.SetInterpolationTypeToNearest()
    prop.ShadeOn() if shade else prop.ShadeOff()

    mapper = vtkFixedPointVolumeRayCastMapper()
    mapper.SetInputData(image)

    vol = vtkVolume()
    vol.SetMapper(mapper)
    vol.SetProperty(prop)
    return vol

def make_volume_from_tiff(
    tiff_path: Path,
    spacing: SpacingConfig,
    *,
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
    shade: bool = True,
    linear_interpolation: bool = True,
    max_bytes: int = 2_000_000_000,  # ~2GB cap
) -> vtkVolume:
    """
    Loads a volume from OME-Zarr on S3 via Dask, then converts to vtkImageData.

    Assumes array is either:
      - (t, c, z, y, x)
      - (c, z, y, x)
      - (z, y, x)
    We pick [t_index, channel] when possible.
    """

    root = parse_url(zarr_url, mode="r")
    store = root.store
    darr = da.from_zarr(store, component=f"{component}")

    vol = darr
    if vol.ndim == 5: # (t,c,z,y,x)
        vol = vol[t_index, channel]
    elif vol.ndim == 4: # (c,z,y,x)
        vol = vol[channel]
    elif vol.ndim == 3:
        pass
    else:
        raise ValueError(f"Unsupported zarr array ndim={vol.ndim}, shape={vol.shape}")

    est_bytes = int(np.prod(vol.shape)) * np.dtype(vol.dtype).itemsize
    if est_bytes > max_bytes:
        raise MemoryError(
            f"Requested volume would be ~{est_bytes/1e9:.2f} GB in RAM. "
            f"Use a lower-res zarr_url (e.g. .../1, .../2), crop, or downsample."
        )

    np_vol = vol.compute()  
    np_vol = np.ascontiguousarray(np_vol)

    z, y, x = np_vol.shape

    vtk_arr = numpy_to_vtk(num_array=np_vol.ravel(order="C"), deep=True)
    vtk_arr.SetName("scalars")

    img = vtkImageData()
    img.SetDimensions(x, y, z)
    img.SetSpacing(spacing.sx, spacing.sy, spacing.sz)
    img.GetPointData().SetScalars(vtk_arr)

    return _make_volume_from_vtk_image(img, shade=shade, linear_interpolation=linear_interpolation)
