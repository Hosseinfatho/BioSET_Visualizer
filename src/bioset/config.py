from __future__ import annotations

import os
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
    zarr_url: Optional[str] = None
    zarr_component: int = 5
    zarr_time_index: int = 0

    # Which channels to load
    channels: Sequence[int] = (0,)

    # Channel colors
    channel_colors: Sequence[str] = (
        "Cyan", "Magenta", "Yellow", "Red", "Green", "Blue")

    # Volume spacing and zarr comp
    level: int = 5
    base_sx: float = 0.14
    base_sy: float = 0.14
    base_sz: float = 0.28

    # Zarr multiresolution settings
    # starting default component and range of componets to pick from
    start_component: int = 6
    min_component: int = 0
    max_component: int = 6

    # LOD zoom thresholds in world units (camera dist from vol)
    distance_rules: Sequence[Tuple[float, int]] = (
        (2500.0, 6),
        (1900.0, 5),
        (1000.0,  4),
        (600.0,  3),
        (300.0,  2),
        (100.0,  1),
        (-100.0,  0),
    )

    # Heatmap LOD thresholds: (camera_distance, hierarchy_level)
    # Level 3=Overview (coarse), 0=Fine. Same distance axis as distance_rules.
    heatmap_distance_rules: Sequence[Tuple[float, int]] = (
        (1000.0, 3),  # far out    → Overview  (1024-voxel tiles)
        (300.0,  2),  # medium     → Coarse    (512-voxel tiles)
        (150.0,  1),  # close      → Medium    (256-voxel tiles)
        (-100.0, 0),  # very close → Fine      (128-voxel tiles)
    )

    # ROI padding (voxels at the chosen component)
    roi_margin_vox: int = 16

    # caching
    cache_enabled: bool = True
    cache_dir: Path = Path.home() / ".cache" / "bioset_zarr_cache"
    cache_size_gb: float = 8.0

    # Globus HTTPS streaming (for `globus://<path>` or *.gaccess.io URLs).
    # Collection/host differ per collection; token file caches the refresh token.
    globus_client_id: Optional[str] = None
    globus_collection_id: Optional[str] = None
    globus_https_base: Optional[str] = None
    globus_token_file: str = "~/.bioset/globus_token.json"

    # surfaces directory
    mesh_dir: Optional[Path] = None

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
        # source="tiff",
        source="zarr_s3",
        zarr_url="https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/0",
        zarr_component=5,
        project_root=project_root,
        data_dir=data_dir,
        channels=(0,),
        base_sx=0.14,
        base_sy=0.14,
        base_sz=0.28,
        mesh_dir=data_dir / "output_meshes",
        # Globus HTTPS: env-overridable, with the known working collection as default.
        globus_client_id=os.environ.get(
            "BIOSET_GLOBUS_CLIENT_ID", "6be7e29d-6cf6-42a0-bf49-90ccba4bf8a3"),
        globus_collection_id=os.environ.get(
            "BIOSET_GLOBUS_COLLECTION_ID", "31fa4572-cd84-489b-8008-0bf0e52bb4d4"),
        globus_https_base=os.environ.get(
            "BIOSET_GLOBUS_HTTPS_BASE", "https://m-b2c38a.183192.b160.gaccess.io"),
        globus_token_file=os.environ.get(
            "BIOSET_GLOBUS_TOKEN_FILE", "~/.bioset/globus_token.json"),
    )
