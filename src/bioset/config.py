from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class VolumeConfig:
    # Data / paths
    project_root: Path
    data_dir: Path
    tiff_pattern: str = "ch{ch}_comp5.tiff"

    # Which channels to load
    channels: Sequence[int] = (0,)  # e.g. (0, 1, 2, 3, 13, 14)

    # Pyramid level spacing behavior
    level: int = 5
    base_sx: float = 1.0
    base_sy: float = 1.0
    base_sz: float = 1.0

    # Rendering defaults
    background: str = "White"
    shade: bool = True
    linear_interpolation: bool = True

    @property
    def xy_scale(self) -> float:
        return float(2 ** self.level)

    def tiff_path_for_channel(self, ch: int) -> Path:
        return self.data_dir / self.tiff_pattern.format(ch=ch)


def default_config() -> VolumeConfig:
    # app is in src/bioset; project root is 3 levels up: src/bioset/config.py -> project
    project_root = Path(__file__).resolve().parents[2]
    data_dir = project_root / "data"
    return VolumeConfig(project_root=project_root, data_dir=data_dir)
