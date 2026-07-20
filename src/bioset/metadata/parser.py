# parser.py
"""OME-XML metadata parser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import requests
import ome_types


@dataclass
class ChannelInfo:
    """Information about a single channel."""
    id: int
    name: str
    

@dataclass
class VolumeMetadata:
    """Parsed metadata for a volume."""
    channels: List[ChannelInfo]
    physical_size_x: float
    physical_size_y: float
    physical_size_z: float
    size_unit: Optional[str] = None
    

_UNIT_ALIASES = {
    "micrometer": "µm",
    "micron": "µm",
    "um": "µm",
    "nanometer": "nm",
    "millimeter": "mm",
    "meter": "m",
}


def parse_zarr_attrs_metadata(
    attrs: Dict[str, Any],
    channel_count: Optional[int] = None,
) -> Optional[VolumeMetadata]:
    """
    Parse metadata baked into an OME-Zarr store's root attributes.

    Reads the ``multiscales`` block for axes/units and the level-0
    ``coordinateTransformations`` scale, and the ``omero`` block for channel
    names. Both live on the root group, not on the level arrays.

    Args:
        attrs: root group attributes (``ZarrMultiscaleSource.root_attrs()``)
        channel_count: fallback channel count when there is no ``omero`` block

    Returns:
        VolumeMetadata, or None when the attrs carry no usable multiscales block
    """
    multiscales = attrs.get("multiscales")
    if not multiscales:
        return None

    ms = multiscales[0]
    axes = ms.get("axes") or []
    axis_names = [str(a.get("name", "")).lower() for a in axes]

    datasets = ms.get("datasets") or []
    if not datasets:
        return None
    # Level 0 is the finest level; datasets are ordered coarsest-last by spec,
    # but sort by path to be safe against writers that don't order them.
    try:
        level0 = min(datasets, key=lambda d: int(str(d.get("path", "0"))))
    except (TypeError, ValueError):
        level0 = datasets[0]

    scale = None
    for tf in level0.get("coordinateTransformations") or []:
        if tf.get("type") == "scale" and tf.get("scale"):
            scale = tf["scale"]
            break
    if scale is None:
        return None

    def _axis_scale(name: str, default: float) -> float:
        if name in axis_names:
            idx = axis_names.index(name)
            if idx < len(scale):
                return float(scale[idx])
        return default

    physical_size_x = _axis_scale("x", 0.14)
    physical_size_y = _axis_scale("y", 0.14)
    physical_size_z = _axis_scale("z", 0.28)

    # Unit is per-axis in NGFF; the spatial axes share one in practice.
    raw_unit = None
    for axis in axes:
        if str(axis.get("name", "")).lower() in ("x", "y", "z") and axis.get("unit"):
            raw_unit = str(axis["unit"])
            break
    size_unit = _UNIT_ALIASES.get((raw_unit or "").lower(), raw_unit or "µm")

    omero_channels = (attrs.get("omero") or {}).get("channels") or []
    if omero_channels:
        channels = [
            ChannelInfo(id=idx, name=ch.get("label") or ch.get("name") or f"Channel {idx}")
            for idx, ch in enumerate(omero_channels)
        ]
    elif channel_count:
        channels = [ChannelInfo(id=i, name=f"Channel {i}") for i in range(channel_count)]
    else:
        return None

    print(f"[metadata] Using metadata baked into the zarr store")
    print(f"[metadata] Found {len(channels)} channels: {[c.name for c in channels]}")
    print(f"[metadata] Physical size: ({physical_size_x}, {physical_size_y}, {physical_size_z}) {size_unit}")

    return VolumeMetadata(
        channels=channels,
        physical_size_x=physical_size_x,
        physical_size_y=physical_size_y,
        physical_size_z=physical_size_z,
        size_unit=size_unit,
    )


def parse_ome_metadata(metadata_url: str) -> VolumeMetadata:
    """
    Fetch and parse OME-XML metadata from URL.
    
    Args:
        metadata_url: URL to the OME-XML metadata file
        
    Returns:
        VolumeMetadata with channel info and physical dimensions
    """    
    print(f"[metadata] Fetching metadata from {metadata_url}")
    response = requests.get(metadata_url, timeout=30)
    response.raise_for_status()
    
    xml_text = response.text.replace("Â", "")
    ome_xml = ome_types.from_xml(xml_text)
    
    pixels = ome_xml.images[0].pixels
    channels = [
        ChannelInfo(id=idx, name=ch.name or f"Channel {idx}")
        for idx, ch in enumerate(pixels.channels)
    ]
    
    physical_size_x = float(pixels.physical_size_x) if pixels.physical_size_x else 0.14
    physical_size_y = float(pixels.physical_size_y) if pixels.physical_size_y else 0.14
    physical_size_z = float(pixels.physical_size_z) if pixels.physical_size_z else 0.28
    
    size_unit = str(pixels.physical_size_x_unit) if pixels.physical_size_x_unit else "µm"
    
    print(f"[metadata] Found {len(channels)} channels")
    print(f"[metadata] Physical size: ({physical_size_x}, {physical_size_y}, {physical_size_z}) {size_unit}")
    
    return VolumeMetadata(
        channels=channels,
        physical_size_x=physical_size_x,
        physical_size_y=physical_size_y,
        physical_size_z=physical_size_z,
        size_unit=size_unit,
    )