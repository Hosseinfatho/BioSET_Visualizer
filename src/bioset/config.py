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

    ══ THE FOUR KNOBS ═════════════════════════════════════════════════════
    These are the ones to reach for. Each is independent of the others, and
    each says in its name what it does to the picture. Edit and restart.

      cell_contrast   how EXAGGERATED the difference between neighbouring
                      cells is.  0 = faithful, 1 = default, 3 = near-binary.
      hot_brightness  how much BRIGHTER the highlighted regions get, as a
                      multiple of the unmodulated volume.  1 = no boost.
      cold_dimness    how much brightness the QUIETEST regions keep.
                      0 = black, 0.15 = default, 1 = no dimming at all.
      map_smoothing   how much the cell boundaries are SMOOTHED.
                      0 = hard squares, 1 = default, >1 = progressively
                      blurrier.

    A worked example — "I want a stark, high-contrast look":
        cell_contrast=2.5, hot_brightness=6.0, cold_dimness=0.05
    and a subtle one — "just tint it, don't shout":
        cell_contrast=0.0, hot_brightness=1.5, cold_dimness=0.5

    Two of these interact in one way worth knowing: `hot_brightness` is a
    CEILING, not a promise. A fragment can only be brightened until one of its
    colour channels reaches 1.0, because going past that clips the channel and
    drags the colour toward white — which is the bug this whole design exists
    to prevent. Bright tissue therefore hits its own limit before the ceiling
    does, so raising `hot_brightness` past ~4 mostly affects faint tissue.

    ── The rest ───────────────────────────────────────────────────────────
    `map_*` shape the map BEFORE it reaches the shader (which cells count as
    empty, how the values are spread, how the coarse levels are rescued).
    `sampling_max_step_scale` is how coarsely cold regions are ray-marched.
    Resolution needs no tuning: the maps are GPU textures at the heatmap LOD's
    own grid, so they follow the camera like the grid heatmap does.

    Values marked [literal] are baked into the generated GLSL; [uniform]
    values are uploaded as custom uniforms. Set BIOSET_DUMP_SHADER=1 to dump
    the generated GLSL (streaming/shader_debug.py) and confirm a change
    actually reached the GPU.
    """
    # ══ THE FOUR KNOBS ═════════════════════════════════════════════════════

    # ── 1. How exaggerated are the differences between cells? ──
    # An S-curve on the map value, applied this many times. Rank equalization
    # spreads cells uniformly, so the steep middle of the curve lands where
    # the bulk of them sit and pushes neighbours apart.
    #
    #   0.0  linear — differences render exactly in proportion to the data
    #   1.0  one smoothstep (default): measured, a 0.061 map difference (the
    #        median between adjacent cells at the finest level) renders 1.9x
    #        to 4.4x more separated than with no curve, depending on how
    #        bright the tissue is
    #   2-3  progressively harder, toward a near-binary hot/cold split
    #
    # Raising this trades away the ability to tell the HOTTEST cells apart
    # from each other — they all flatten toward the top of the curve.
    cell_contrast: float = 3.0                      # [literal]

    # ── 2. How much brighter are the highlighted regions? ──
    # Ceiling on the per-fragment brightness boost, as a multiple of the
    # unmodulated volume. 1.0 disables the boost entirely (highlighted regions
    # then merely fail to be dimmed, which is what made the effect hard to see
    # before this knob existed).
    #
    # Inert for bright tissue, which reaches its own clipping limit first —
    # measured on a 0.15-to-1.0 map, hot-region red of 0.153 at 2.0, 0.299 at
    # 4.0, 0.317 at 8.0 on faint tissue, and identical at all three on normal
    # tissue. Raise it to lift faint channels; it cannot cause whitening at
    # any value.
    hot_brightness: float = 3.0                     # [literal]

    # ── 3. How dull are the quiet regions? ──
    # What fraction of its normal brightness the coldest region keeps.
    # 0.0 = black (maximum contrast, but empty areas vanish entirely),
    # 0.15 = default, 1.0 = no dimming (highlighting then comes only from
    # `hot_brightness`).
    #
    # This is the knob for "the whole scene is too dark" — raise it.
    cold_dimness: float = 0.3                      # [literal]

    # ── 4. How much are cell boundaries smoothed? ──
    #   0.0      hard-edged squares: no filtering at all. Cells read as
    #            blocks, worst at the coarse levels where one spans ~1024
    #            voxels.
    #   0.0-1.0  hardware bilinear, blended from straight-linear toward a
    #            smoothstep that is also smooth ACROSS cell centres
    #   1.0      default
    #   >1.0     additionally blurs the map itself before upload, with a
    #            Gaussian of (value - 1) cells. 2.0 is a one-cell blur.
    #            Costs CPU per level change, nothing per frame.
    #
    # Blurring only ever mixes occupied cells with each other; empty cells
    # stay exactly 0, so the tissue border stays crisp however high this goes.
    map_smoothing: float = 0.5                      # [literal + CPU]

    # ══ Everything below shapes the map before the knobs above act on it ═══

    # Per-channel member maps bound alongside the interaction map. Matched to
    # the multivolume's own 10-channel limit: the maps ride in the RGBA
    # components of up to 3 textures, so this no longer costs shader
    # registers the way the mat4 uniforms it replaced did.
    max_member_maps: int = 10                       # [structural]

    # ── How the raw fractions are spread across [floor, 1] ──
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
    # Only a manual override — the effective gamma is derived per map from its
    # own occupancy (see the two constants below and `occupancy_gamma`).
    map_gamma: float = 1.0
    # "percentile" mode only.
    map_pct_lo: float = 2.0
    map_pct_hi: float = 98.0

    # ── Coarse-level rescue ──
    # Aggregation is what breaks the coarse levels, not contrast. Measured on
    # mis_v3, the share of cells holding any signal climbs 45% (Fine) -> 52%
    # -> 59% -> 73% (Overview), so the empty-vs-occupied distinction that
    # carries the fine view flattens into a mid-tone wash: 48% of the Overview
    # map reads dark against 68% of the Fine one.
    #
    # `occupancy_gamma` solves for the gamma that restores the target below at
    # any occupancy, yielding ~1.0 / 1.28 / 1.57 / 2.11 for those four levels.
    # Fine lands on 1.0 by construction and is untouched.
    map_target_dark_fraction: float = 0.68   # what Fine already shows
    map_dark_threshold: float = 0.40         # map value that reads as "dark"
    map_gamma_max: float = 3.0               # ceiling on the derived gamma

    # ── Gain: what DRIVES a member channel's ramp ──
    # A member port sees two maps — its own channel's and the interaction's —
    # and these weight how much each one drives its ramp. Only their ratio
    # matters; the ends of the ramp are `cold_dimness` and `hot_brightness`.
    # Raise the interaction weight to make co-localization dominate, or the
    # channel weight to make each channel follow its own density.
    #
    #   v      = clamp(ch_w*own + int_w*inter, 0, 1)     (weights normalized)
    #   rgb   *= Hi * mix(cold_dimness, 1, curve(v))
    #   alpha *= mix(min_alpha, 1, max(own, inter))
    #
    # The alpha ramp is separate on purpose: opacity is nearly saturated in
    # this app (raising per-sample alpha 0.12 -> 0.80 buys 7% brightness,
    # measured), so it dims cold regions but is not a route to making hot ones
    # pop. That is `hot_brightness`'s job.
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
    def map_blur_cells(self) -> float:
        """Gaussian sigma, in cells, applied to the map before upload."""
        return max(0.0, float(self.map_smoothing) - 1.0)

    @property
    def uv_smoothing(self) -> float:
        """How far the texture-filter blend is warped toward a smoothstep."""
        return min(1.0, max(0.0, float(self.map_smoothing)))


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

    # ── The interactive placeholder ────────────────────────────────────────
    # While you interact, each channel's texture is swapped for a whole-volume
    # "base" image so the volume is never empty and motion stays cheap. This
    # picks which pyramid level that base is built at, counted from the
    # COARSEST end so it means the same thing on any pyramid depth:
    #
    #   -1   the coarsest level available     (what this used to do)
    #   -2   one level finer                  <- default
    #   -3   two levels finer
    #
    # A non-negative value is read as a literal component index.
    #
    # Default -2 because the coarsest level is often too coarse to keep your
    # bearings in: on mis_v3 it is 3 x 86 x 170 voxels — three z-slices — and
    # dropping to it loses all sense of where you were. One level finer is 8x
    # the data and still only 0.7 MB per channel against the 512 MB pinned
    # low-res budget below, so the cost is noise.
    #
    # This does NOT affect how far out the camera-driven LOD goes: fully zoomed
    # out still renders at max_component. Only the loading placeholder changes.
    #
    # Resolved into `base_component` by
    # VolumeStreamer.configure_lod_from_source, which knows the real depth.
    interactive_base_component: int = -2

    # Resolved absolute index for the placeholder. Written at load time; do not
    # set it by hand. Defaults to the coarsest level rather than 0 — an
    # unresolved 0 would build a whole-volume base image at FULL resolution,
    # which is 22 GB per channel on mis_v3.
    base_component: int = 6

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
