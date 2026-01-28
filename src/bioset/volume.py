from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction
from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkIOImage import vtkTIFFReader
from vtkmodules.vtkImagingCore import vtkImageChangeInformation
from vtkmodules.vtkRenderingCore import vtkColorTransferFunction, vtkVolume, vtkVolumeProperty
from vtkmodules.vtkRenderingVolume import vtkFixedPointVolumeRayCastMapper


@dataclass(frozen=True)
class SpacingConfig:
    sx: float
    sy: float
    sz: float


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

    out = change.GetOutput()
    r0, r1 = out.GetScalarRange()
    rmid = 0.5 * (r0 + r1)

    # Opacity TF
    opacity_tf = vtkPiecewiseFunction()
    opacity_tf.AddPoint(r0, 0.0)
    opacity_tf.AddPoint(rmid, 0.2)
    opacity_tf.AddPoint(r1, 0.2)

    # Color TF
    color_tf = vtkColorTransferFunction()
    color_tf.AddRGBPoint(r0, 0.0, 0.0, 1.0)
    color_tf.AddRGBPoint(rmid, 1.0, 1.0, 1.0)
    color_tf.AddRGBPoint(r1, 1.0, 0.0, 0.0)

    prop = vtkVolumeProperty()
    prop.SetColor(color_tf)
    prop.SetScalarOpacity(opacity_tf)

    if shade:
        prop.ShadeOn()
    else:
        prop.ShadeOff()

    if linear_interpolation:
        prop.SetInterpolationTypeToLinear()
    else:
        prop.SetInterpolationTypeToNearest()

    mapper = vtkFixedPointVolumeRayCastMapper()
    mapper.SetInputConnection(change.GetOutputPort())

    vol = vtkVolume()
    vol.SetMapper(mapper)
    vol.SetProperty(prop)
    return vol
