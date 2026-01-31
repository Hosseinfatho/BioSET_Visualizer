# parser.py
"""OME-XML metadata parser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
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