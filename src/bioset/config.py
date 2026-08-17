from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence, Optional, Tuple


@dataclass(frozen=True)
class IntegratedHeatmapConfig:
    """Tuning for the shader-injected "Integrated" heatmap mode.

    This dataclass is the management surface for the two shader effects
    (gain / importance sampling) — edit and restart. The outline used to be a
    third; it is contour geometry now (scene/heatmap_contours.py). Values
    marked [literal] are baked into the generated GLSL; [uniform] values are
    uploaded as custom uniforms.

    ── HOW TO TUNE ────────────────────────────────────────────────────────
    If the effects look too weak or too strong, reach for these in order:

    1. MAP CONTRAST (`map_*`) — the biggest lever, and usually the right one.
       Real combination fields are sparse: a typical 16x16 map has ~55% empty
       cells and a nonzero median near 0.11 of its own maximum. Under plain
       max-normalization every effect then applies its "cold" end almost
       everywhere and reads as a uniform dim. `map_contrast_mode="rank"`
       spreads the nonzero cells evenly across [floor, 1] so small
       differences become visible differences.

    2. EFFECT STRENGTH — gain: widen the gap between `*_low`/`base` (cold)
       and `*_high`/weights (hot). Sampling: `sampling_max_step_scale` is how
       coarsely cold regions are marched.

       Note this app's opacity transfer functions cap around 0.12 (see
       `build_histogram_tf` in scene/volumes.py) — tissue here is far more
       transparent than in the standalone experiment these constants came
       from (0.45), so per-sample modulation composites into a smaller final
       difference and the constants have to work harder.

    3. SPATIAL RESOLUTION (`grid_w`/`grid_h`) — matters most when zoomed in.
       At 16x16 one cell spans ~682x344 voxels (~95x48 um on mis_full), so a
       zoomed-in viewport can sit inside a couple of cells and see a nearly
       constant field. Raising the grid is bounded by the NVIDIA constant
       register budget (~1024 per fragment program, shared with VTK's own):

           registers per map = ceil(grid_w * grid_h / 16) * 4
           total = (1 + max_member_maps) maps

           16x16 ->  64/map ->  320 total   (current, ample headroom)
           24x24 -> 144/map ->  720 total   (safe at max_member_maps=4)
           32x32 -> 256/map -> 1280 total   (needs max_member_maps <= 2)

       Overrunning the budget shows up as a shader link failure, which the
       harness (scratchpad verify_ihm_shader.py) captures — so this is
       checkable, not guesswork. Set BIOSET_DUMP_SHADER=1 to dump the
       generated GLSL (streaming/shader_debug.py) and confirm a change
       actually reached the GPU.
    """
    # ── Shader grid over the full volume XY (see HOW TO TUNE #3) ──
    grid_w: int = 16                                # [literal]
    grid_h: int = 16                                # [literal]
    # Per-channel member maps bound alongside the interaction map.
    max_member_maps: int = 4                        # [structural]

    # ── Map contrast (see HOW TO TUNE #1) ──
    # "rank"       histogram-equalize the nonzero cells onto [floor, 1].
    #              Guarantees the full range is used whatever the
    #              distribution, and matches what the glyph heatmap already
    #              does. Best for "make subtle differences stand out".
    # "percentile" clip to [pct_lo, pct_hi] of the nonzero values and
    #              rescale. Preserves relative magnitudes better than rank.
    # "max"        divide by the maximum (raw fidelity; what produced the
    #              barely-visible original).
    map_contrast_mode: str = "rank"
    # Where the weakest NON-empty cell lands. Empty cells always stay at 0,
    # so this keeps "present but weak" distinguishable from "absent".
    map_nonzero_floor: float = 0.15
    # Applied after the mode above; < 1 lifts mid-tones, > 1 suppresses them.
    map_gamma: float = 1.0
    # "percentile" mode only.
    map_pct_lo: float = 2.0
    map_pct_hi: float = 98.0

    # ── Gain: single-channel formula (non-member ports, driven by the
    # interaction map): rgb *= mix(low, high, value) ──
    single_low_gain: float = 0.30                   # [literal]
    single_high_gain: float = 4.0                   # [literal]
    # ── Gain: combined formula (member ports: own map + interaction map) ──
    #   rgb   *= clamp(base + ch_w*own + int_w*inter, 0, base+ch_w+int_w)
    #   alpha *= mix(min_alpha, 1, max(own, inter))
    combined_base_rgb_gain: float = 0.30            # [literal]
    combined_channel_rgb_weight: float = 0.70       # [literal]
    combined_interaction_rgb_weight: float = 0.90   # [literal]
    combined_min_alpha_gain: float = 0.20           # [literal]

    # The halo-outline settings lived here. The outline is real contour
    # geometry now — see scene/heatmap_contours.py and `ContourConfig`, whose
    # levels are percentiles of the visible field and whose resolution follows
    # the camera instead of being pinned to this grid.

    # ── Importance sampling: step = base * mix(max_scale, 1, importance) ──
    sampling_max_step_scale: float = 10.0           # [uniform]

    @property
    def mat4_count(self) -> int:
        return -(-(self.grid_w * self.grid_h) // 16)

    @property
    def combined_max_rgb_gain(self) -> float:
        return (self.combined_base_rgb_gain
                + self.combined_channel_rgb_weight
                + self.combined_interaction_rgb_weight)


INTEGRATED_HEATMAP = IntegratedHeatmapConfig()


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
        (1000.0, 3),  # far out    → Overview  (1024-voxel cells)
        (300.0,  2),  # medium     → Coarse    (256-voxel cells)
        (120.0,  1),  # close      → Medium    (64-voxel cells)
        (-100.0, 0),  # very close → Fine      (16-voxel cells, ~subcellular)
    )

    # Heatmap LOD level → cell edge length in voxels (y/x; z spans the volume).
    analysis_cell_sizes: dict = None  # None → analysis.constants.DEFAULT_CELL_SIZES_VOX

    # ROI padding (voxels at the chosen component)
    roi_margin_vox: int = 16

    # caching
    cache_enabled: bool = True
    cache_dir: Path = Path.home() / ".cache" / "bioset_zarr_cache"
    cache_size_gb: float = 8.0
    # In-memory decoded-chunk LRU budgets (RAM). Fine full-Z tiles are large, so
    # too small a budget thrashes (re-decoding tiles every settle). Tune up on a
    # workstation with spare RAM; down on a memory-constrained box.
    chunk_cache_gb: float = 6.0
    lowres_cache_gb: float = 0.5

    # Globus HTTPS streaming (for `globus://<path>` or *.gaccess.io URLs).
    # Collection/host differ per collection; token file caches the refresh token.
    globus_client_id: Optional[str] = None
    globus_collection_id: Optional[str] = None
    globus_https_base: Optional[str] = None
    globus_token_file: str = "~/.bioset/globus_token.json"

    # Surfaces directory. Normally None: the meshes ship inside the analysis
    # results, so the manager is pointed at <results>/meshes when one loads.
    # Set this only to use meshes without an analysis directory.
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
        mesh_dir=None,  # taken from <results>/meshes on analysis load
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
