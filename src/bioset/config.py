from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence, Optional, Tuple

@dataclass(frozen=True)
class VolumeConfig:
    # Source mode
    source: Literal["tiff", "zarr_s3"] = "tiff"
    
    # Local TIFF settings
    project_root: Path | None = None
    data_dir: Path | None = None
    tiff_pattern: str = "ch{ch}_comp5.tiff"
    
    # S3/Zarr settings
    zarr_url: Optional[str] = None  # e.g. ".../Dataset.../0"
    zarr_component: int = 5
    zarr_time_index: int = 0

    # Which channels to load
    channels: Sequence[int] = (0,)  
    
    # Channel colors
    channel_colors: Sequence[str] = ("Cyan", "Magenta", "Yellow", "Red", "Green", "Blue")

    # Volume spacing and zarr comp
    level: int = 5
    base_sx: float = 0.14
    base_sy: float = 0.14
    base_sz: float = 0.28

    # Rendering Defaults
    background: str = "Black"
    shade: bool = True
    linear_interpolation: bool = True

    @property
    def xy_scale(self) -> float:
        return float(2 ** self.level)

    def tiff_path_for_channel(self, ch: int) -> Path:
        if self.data_dir is None:
            raise ValueError("data_dir is None (TIFF mode needs data_dir)")
        return self.data_dir / self.tiff_pattern.format(ch=ch)



def default_config() -> VolumeConfig:
    project_root = Path(__file__).resolve().parents[2]
    data_dir = project_root / "data"
    return VolumeConfig(
        source="tiff",
        # source="zarr_s3",
        # zarr_url="https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/0",
        zarr_component=5,
        project_root=project_root,
        data_dir=data_dir,
        channels=(0,),
        level=5,
        base_sx=0.14,
        base_sy=0.14,
        base_sz=0.28,
    )
