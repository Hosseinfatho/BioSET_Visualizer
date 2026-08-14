"""Integrated Heatmap: heatmap effects injected into the GPU volume ray-cast
fragment shader via vtkShaderProperty replacements.

Three composable effects (ported from the standalone experiment
integrated_heatmap_trame/final.py, which encodes several hard-won GPU
constraints — preserved verbatim in comments below):

- GAIN: per-sample color/alpha re-weighting inside the ray march. Ports whose
  channel is a member of the selected combination use the combined formula
  (own channel map + interaction map); other ports use the single-channel
  formula driven by the interaction map.
- HALO OUTLINE: a constant-pixel-width outline of the interaction map's
  above-threshold region, computed from a fixed-count ray walk at
  //VTK::Base::Exit (a MIP silhouette) and the screen-space distance trick
  d = (h - threshold) / |grad_screen h|.
- IMPORTANCE SAMPLING: ray step length scaled by heatmap importance —
  deliberately WITHOUT opacity correction; the washed-out look of coarsely
  sampled regions IS the encoding of unimportance.

Data path: 16x16 maps over the full volume XY, packed into mat4[16] uniforms
(a dynamically indexed float[256] would cost 256 constant registers per map on
NVIDIA against a ~1024 budget; a mat4 element costs 4, so mat4 packing cuts a
map to 64. mat4 rather than vec4 because SetUniform4fv is not wrapped in
VTK's Python bindings — SetUniformMatrix4x4v is). VTK uploads matrices
column-major, so flat value i lives at map[i>>4][(i>>2)&3][i&3].

Coordinate path: ray position -> WORLD via
`in_volumeMatrix[0] * in_textureDatasetMatrix[0]` (valid in both generated
shader forms: multivolume, where index 0 is the common bounding box the ray
marches in; and classic single-port, where index 0 is the volume itself),
then world.xy scaled by custom inverse-extent uniforms into [0,1] UV. This
deliberately avoids per-port texture coordinates: the streamer's port
textures are sub-ROI crops whose texture space does NOT span the full volume.

All methods MAIN THREAD ONLY (GL objects).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..config import INTEGRATED_HEATMAP, IntegratedHeatmapConfig


# ──────────────────────────────────────────────────────────────
# Pure helpers (no VTK)
# ──────────────────────────────────────────────────────────────

def pack_map_to_mat4(flat: np.ndarray, mat4_count: int) -> list:
    """Zero-pad a flat float map to mat4_count*16 floats for
    SetUniformMatrix4x4v upload. Pure identity packing — the GLSL side
    reconstructs flat index i as [i>>4][(i>>2)&3][i&3]."""
    values = np.zeros(mat4_count * 16, dtype=np.float32)
    values[: flat.size] = flat
    return values.tolist()


def apply_map_contrast(frac: np.ndarray,
                       cfg: IntegratedHeatmapConfig = INTEGRATED_HEATMAP
                       ) -> np.ndarray:
    """Spread a raw fraction map across [0,1] so subtle differences show.

    Real combination fields are sparse and bunched near zero (a typical 16x16
    map has ~55% empty cells and a nonzero median around 0.11 of its own
    maximum). Every effect formula is linear in this value, so under plain
    max-normalization the whole volume sits at the cold end and the effects
    read as a uniform dim rather than a highlight.

    Empty cells always stay at exactly 0 — "absent" must stay distinguishable
    from "present but weak", which lands at cfg.map_nonzero_floor.

    Modes: see IntegratedHeatmapConfig.map_contrast_mode.
    """
    out = np.zeros_like(frac, dtype=np.float32)
    nz = frac > 0
    vals = frac[nz].astype(np.float64)
    if vals.size == 0:
        return out

    mode = cfg.map_contrast_mode
    if vals.size == 1:
        scaled = np.ones(1)
    elif mode == "rank":
        # Histogram equalization with ties averaged — the same rank mapping
        # HeatmapRenderer.update_field uses for the glyph modes, so both
        # heatmap representations spread values the same way.
        uniq, inverse, counts = np.unique(vals, return_inverse=True,
                                          return_counts=True)
        ends = np.cumsum(counts)
        avg_rank = (ends - counts + ends - 1) / 2.0
        scaled = avg_rank[inverse] / (vals.size - 1)
    elif mode == "percentile":
        lo, hi = np.percentile(vals, [cfg.map_pct_lo, cfg.map_pct_hi])
        scaled = np.clip((vals - lo) / (hi - lo), 0.0, 1.0) if hi > lo \
            else np.ones_like(vals)
    else:  # "max" — raw fidelity
        m = vals.max()
        scaled = vals / m if m > 0 else np.zeros_like(vals)

    if cfg.map_gamma != 1.0:
        scaled = scaled ** cfg.map_gamma

    floor = float(cfg.map_nonzero_floor)
    out[nz] = (floor + (1.0 - floor) * scaled).astype(np.float32)
    return out


def downsample_field_to_grid(field, grid_shape_zyx, bin_factor_y: int,
                             gw: int, gh: int,
                             cfg: IntegratedHeatmapConfig = INTEGRATED_HEATMAP
                             ) -> Optional[np.ndarray]:
    """Reduce a level-0 HeatmapField to a (gh, gw) map over the full volume
    XY, contrast-stretched into [0,1] with zeros preserved.

    Denominators are computed analytically (edge-corrected bins per cell):
    the sparse field omits zero-count cells, but those cells still hold bins.

    Legend caveat (from the experiment, doubly true after contrast
    stretching): per-map normalization means equal tones in two maps do NOT
    mean equal numbers.
    """
    if field is None or field.counts.size == 0:
        return None
    gz, gy_bins, gx_bins = grid_shape_zyx
    cell_bins = max(1, field.cell_size_vox // bin_factor_y)

    # Scatter active-bin counts into the shader grid (floor assignment; at
    # 345x682 -> 16x16 the boundary-straddle error is sub-cell).
    gy = (field.cells_yx[:, 0].astype(np.int64) * gh) // field.ny
    gx = (field.cells_yx[:, 1].astype(np.int64) * gw) // field.nx
    counts = np.zeros((gh, gw), dtype=np.float64)
    np.add.at(counts, (gy, gx), field.counts)

    # Analytical denominators: bins per level-0 cell row/col, edge-corrected,
    # reduced separably into the shader grid.
    rows = np.clip(gy_bins - np.arange(field.ny) * cell_bins, 0, cell_bins)
    cols = np.clip(gx_bins - np.arange(field.nx) * cell_bins, 0, cell_bins)
    row_g = (np.arange(field.ny) * gh) // field.ny
    col_g = (np.arange(field.nx) * gw) // field.nx
    rows_per_g = np.bincount(row_g, weights=rows, minlength=gh)
    cols_per_g = np.bincount(col_g, weights=cols, minlength=gw)
    denoms = gz * np.outer(rows_per_g, cols_per_g)

    frac = counts / np.maximum(denoms, 1.0)
    if not np.all(np.isfinite(frac)):
        print("[ihm] WARNING: non-finite values in downsampled map — zero-filled")
        frac = np.nan_to_num(frac, nan=0.0, posinf=0.0, neginf=0.0)
    return np.ascontiguousarray(apply_map_contrast(frac, cfg), dtype=np.float32)


def bilinear_lookup_lines(uv_expression: str, prefix: str, uniform: str,
                          gw: int, gh: int, smooth_blend: bool) -> List[str]:
    """Inlined GLSL bilinear lookup ending in `float {prefix}Value`.

    smooth_blend gives C1 continuity at cell centers, which looks nicer for
    gain modulation. It also drives the field's derivative to zero at every
    cell center. The halo outline divides by that derivative, so where it
    vanishes the outline width goes to infinity. The halo MUST pass False.
    """
    p, u = prefix, uniform
    lines = [
        f"vec2 {p}GridPosition = clamp({uv_expression}, vec2(0.0), vec2(1.0)) "
        f"* vec2({float(gw):.1f}, {float(gh):.1f}) - vec2(0.5, 0.5);",
        f"ivec2 {p}Cell0 = ivec2(floor({p}GridPosition));",
        f"ivec2 {p}Cell1 = {p}Cell0 + ivec2(1, 1);",
        f"vec2 {p}Blend = fract({p}GridPosition);",
    ]
    if smooth_blend:
        lines.append(f"{p}Blend = smoothstep(vec2(0.0), vec2(1.0), {p}Blend);")
    lines.extend([
        f"{p}Cell0 = clamp({p}Cell0, ivec2(0, 0), ivec2({gw - 1}, {gh - 1}));",
        f"{p}Cell1 = clamp({p}Cell1, ivec2(0, 0), ivec2({gw - 1}, {gh - 1}));",
        f"int {p}Index00 = {p}Cell0.y * {gw} + {p}Cell0.x;",
        f"int {p}Index10 = {p}Cell0.y * {gw} + {p}Cell1.x;",
        f"int {p}Index01 = {p}Cell1.y * {gw} + {p}Cell0.x;",
        f"int {p}Index11 = {p}Cell1.y * {gw} + {p}Cell1.x;",
    ])
    for corner in ("00", "10", "01", "11"):
        lines.append(
            f"float {p}Value{corner} = clamp({u}[{p}Index{corner} >> 4]"
            f"[({p}Index{corner} >> 2) & 3][{p}Index{corner} & 3], 0.0, 1.0);"
        )
    lines.extend([
        f"float {p}ValueX0 = mix({p}Value00, {p}Value10, {p}Blend.x);",
        f"float {p}ValueX1 = mix({p}Value01, {p}Value11, {p}Blend.x);",
        f"float {p}Value = mix({p}ValueX0, {p}ValueX1, {p}Blend.y);",
    ])
    return lines


# ──────────────────────────────────────────────────────────────
# Manager
# ──────────────────────────────────────────────────────────────

class IntegratedHeatmapManager:
    """Owns the Integrated Heatmap shader effects on the streamer's shared
    vtkMultiVolume.

    Lifecycle facts this design rests on:
    - The streamer RECREATES the vtkMultiVolume + mapper on every channel
      membership change; `on_multivolume_rebuilt` is the single reinstall
      choke point (replacements AND uniforms must go onto the new property).
    - Texture/LOD/interaction swaps never touch the multi-volume, so shader
      state survives them untouched.
    - Uniform-only updates must NEVER call shader_property.Modified() — that
      forces a recompile. Structural changes go through rebuild().
    """

    MEMBER_UNIFORMS = tuple(
        f"in_ihm_member{i}" for i in range(INTEGRATED_HEATMAP.max_member_maps))
    FLOAT_UNIFORMS = (
        "in_ihm_inv_x", "in_ihm_inv_y",
        "in_ihm_halo_threshold", "in_ihm_outline_width", "in_ihm_max_step_scale",
    )

    def __init__(self, cfg: IntegratedHeatmapConfig = INTEGRATED_HEATMAP):
        self.cfg = cfg
        self._multi_volume = None
        self._mapper = None
        self._channel_port: Dict[int, int] = {}   # channel id -> port
        self._dummy_port: Optional[int] = None

        self._active = False
        self._gain = True
        self._halo = True
        self._sampling = True

        self._inter_packed: Optional[list] = None
        self._member_packed: "OrderedDict[int, list]" = OrderedDict()  # ch id -> packed
        self._member_ids: Tuple[int, ...] = ()

        self._inv_x = 0.0
        self._inv_y = 0.0

        self._installed_signature: Optional[tuple] = None
        self._map_cache: "OrderedDict[tuple, tuple]" = OrderedDict()

    @property
    def uniform_names(self) -> tuple:
        return ("in_ihm_inter",) + self.MEMBER_UNIFORMS + self.FLOAT_UNIFORMS

    # ── lifecycle ──────────────────────────────────────────

    def _check_main_thread(self):
        if threading.current_thread() is not threading.main_thread():
            print("[ihm] ERROR: shader work off the main thread — refusing")
            return False
        return True

    def _shader_property(self):
        return self._multi_volume.GetShaderProperty() if self._multi_volume else None

    def on_multivolume_rebuilt(self, multi_volume, mapper,
                               channel_port: Dict[int, int],
                               dummy_port: Optional[int]):
        """Streamer hook: the multi-volume + mapper were just recreated.
        The new shader property is blank — reinstall if active."""
        if not self._check_main_thread():
            return
        self._multi_volume = multi_volume
        self._mapper = mapper
        self._channel_port = dict(channel_port)
        self._dummy_port = dummy_port
        if self._active and self._channel_port:
            # Reinstall with the current maps/effects. Membership may have
            # changed; callbacks follow up with a map recompute, but the old
            # member set renders correctly meanwhile (non-members just fall
            # back to the interaction-driven formula).
            self.rebuild()
        print(f"[ihm] multivolume rebuilt: ports={self._channel_port}, "
              f"dummy={self._dummy_port}, active={self._active}")

    def set_active(self, active: bool):
        if not self._check_main_thread():
            return
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        if active:
            self.rebuild()
        else:
            self.clear()

    def set_effects(self, gain: bool, halo: bool, sampling: bool):
        changed = (self._gain, self._halo, self._sampling) != (gain, halo, sampling)
        self._gain, self._halo, self._sampling = bool(gain), bool(halo), bool(sampling)
        if changed and self._active:
            self.rebuild()

    def set_world_extent(self, x_max: float, y_max: float):
        self._inv_x = 1.0 / x_max if x_max > 0 else 0.0
        self._inv_y = 1.0 / y_max if y_max > 0 else 0.0

    # ── maps ───────────────────────────────────────────────

    def compute_maps(self, loader, combo_names: Sequence[str], dilation: float,
                     name_to_id: Dict[str, int], active_ids: Sequence[int]):
        """Compute (inter16, member16_by_channel_id) from the analysis loader.

        Members = combination channels that are bound to active volume ports,
        in combination order, capped at cfg.max_member_maps.
        """
        key = (tuple(combo_names), round(float(dilation), 4))
        cached = self._map_cache.get(key)
        if cached is None:
            grid = loader.grid
            inter_field = loader.get_heatmap_field(
                channels=list(combo_names), dilation=dilation, hierarchy_level=0)
            inter16 = downsample_field_to_grid(
                inter_field, grid.grid_shape_zyx, grid.bin_factors[1],
                self.cfg.grid_w, self.cfg.grid_h, self.cfg)
            member16_by_name = {}
            for name in combo_names:
                f = loader.get_heatmap_field(
                    channels=[name], dilation=dilation, hierarchy_level=0)
                m16 = downsample_field_to_grid(
                    f, grid.grid_shape_zyx, grid.bin_factors[1],
                    self.cfg.grid_w, self.cfg.grid_h, self.cfg)
                if m16 is not None:
                    member16_by_name[name] = m16
            cached = (inter16, member16_by_name)
            self._map_cache[key] = cached
            while len(self._map_cache) > 12:
                self._map_cache.popitem(last=False)

        inter16, member16_by_name = cached
        active_set = set(active_ids)
        members_by_id: Dict[int, np.ndarray] = {}
        skipped = 0
        for name in combo_names:
            ch_id = name_to_id.get(name)
            if ch_id is None or ch_id not in active_set:
                continue
            if name not in member16_by_name:
                continue
            if len(members_by_id) >= self.cfg.max_member_maps:
                skipped += 1
                continue
            members_by_id[ch_id] = member16_by_name[name]
        if skipped:
            print(f"[ihm] member-map cap {self.cfg.max_member_maps} reached; "
                  f"{skipped} member channel(s) fall back to the interaction map")
        return inter16, members_by_id

    def set_maps(self, inter16: Optional[np.ndarray],
                 members_by_id: Dict[int, np.ndarray]):
        """Install new maps. Uniform-push when the member-id set (structural:
        slot assignment is baked into the GLSL) is unchanged; full rebuild
        otherwise."""
        if not self._check_main_thread():
            return
        if inter16 is None:
            self._inter_packed = None
            if self._active:
                self.clear()
            return
        self._inter_packed = pack_map_to_mat4(inter16.ravel(order="C"),
                                              self.cfg.mat4_count)
        new_ids = tuple(members_by_id.keys())
        self._member_packed = OrderedDict(
            (ch, pack_map_to_mat4(m.ravel(order="C"), self.cfg.mat4_count))
            for ch, m in members_by_id.items()
        )
        structural = new_ids != self._member_ids
        self._member_ids = new_ids
        if not self._active:
            return
        if structural or self._installed_signature != self._signature():
            self.rebuild()
        else:
            self.update_uniforms()

    # ── install / clear ────────────────────────────────────

    def _signature(self) -> tuple:
        return (
            self._active, self._gain, self._halo, self._sampling,
            tuple(sorted(self._channel_port.items())),
            self._member_ids, self._dummy_port,
            id(self._multi_volume),
        )

    def rebuild(self):
        """Wholesale reinstall: clear everything, reinstall replacements and
        uniforms, force a recompile."""
        sp = self._shader_property()
        if sp is None:
            return
        sp.ClearAllFragmentShaderReplacements()
        uniforms = sp.GetFragmentCustomUniforms()
        for name in self.uniform_names:
            uniforms.RemoveUniform(name)

        if (self._active and self._channel_port and self._inter_packed is not None
                and (self._gain or self._halo or self._sampling)):
            self._install_replacements(sp)
            self._upload_uniforms(uniforms)
            self._installed_signature = self._signature()
            n_keys = sum([
                1,  # g_fragColor decl
                1 if (self._gain or self._sampling) else 0,  # Base::Init
                1 if (self._gain or self._sampling) else 0,  # PreComputeGradients
                len(self._gain_ports()) if self._gain else 0,
                2 if self._gain else 0,  # classic hedges (port 0)
                2 if self._sampling else 0,  # march advances
                1 if self._halo else 0,  # Base::Exit
            ])
            print(f"[ihm] installed: gain={self._gain} halo={self._halo} "
                  f"sampling={self._sampling}, members={self._member_ids}, "
                  f"~{n_keys} replacement keys")
        else:
            self._installed_signature = None

        sp.Modified()
        if self._mapper is not None:
            self._mapper.Modified()
        self._multi_volume.Modified()

    def update_uniforms(self):
        """Push uniforms only. NEVER call shader_property.Modified() here —
        that recompiles the shader (custom uniforms are re-pushed on every
        render without it)."""
        sp = self._shader_property()
        if sp is None or self._installed_signature is None:
            return
        self._upload_uniforms(sp.GetFragmentCustomUniforms())

    def clear(self):
        sp = self._shader_property()
        if sp is None:
            return
        sp.ClearAllFragmentShaderReplacements()
        uniforms = sp.GetFragmentCustomUniforms()
        for name in self.uniform_names:
            uniforms.RemoveUniform(name)
        self._installed_signature = None
        sp.Modified()
        if self._mapper is not None:
            self._mapper.Modified()
        self._multi_volume.Modified()

    # ── uniforms ───────────────────────────────────────────

    def _member_slot(self, ch_id: int) -> Optional[int]:
        for slot, cid in enumerate(self._member_ids):
            if cid == ch_id:
                return slot
        return None

    def _upload_uniforms(self, uniforms):
        cfg = self.cfg
        if self._inter_packed is not None:
            uniforms.SetUniformMatrix4x4v(
                "in_ihm_inter", cfg.mat4_count, self._inter_packed)
        for slot, ch_id in enumerate(self._member_ids):
            uniforms.SetUniformMatrix4x4v(
                self.MEMBER_UNIFORMS[slot], cfg.mat4_count,
                self._member_packed[ch_id])
        uniforms.SetUniformf("in_ihm_inv_x", float(self._inv_x))
        uniforms.SetUniformf("in_ihm_inv_y", float(self._inv_y))
        uniforms.SetUniformf("in_ihm_halo_threshold", float(cfg.halo_threshold))
        uniforms.SetUniformf("in_ihm_outline_width", float(cfg.halo_outline_width_px))
        uniforms.SetUniformf("in_ihm_max_step_scale", float(cfg.sampling_max_step_scale))

    # ── GLSL generation ────────────────────────────────────

    def _gain_ports(self) -> List[Tuple[int, int]]:
        """(port, channel_id) for real ports, sorted by port. Never the dummy."""
        ports = sorted((port, ch) for ch, port in self._channel_port.items())
        assert self._dummy_port is None or all(
            p != self._dummy_port for p, _ in ports), "dummy port in gain ports"
        return ports

    def _lookup(self, prefix: str, uniform: str, smooth: bool) -> List[str]:
        return bilinear_lookup_lines(
            "g_ihmUV", prefix, uniform, self.cfg.grid_w, self.cfg.grid_h, smooth)

    def _install_replacements(self, sp):
        cfg = self.cfg
        need_loop_lookups = self._gain or self._sampling
        n_members = len(self._member_ids)

        # 1. Global declarations, piggybacked on a statement that sits at
        # global scope in the generated shader (leaves //VTK::Base::Dec free
        # for the shader-dump tool).
        decls = ["vec4 g_fragColor = vec4(0.0);"]
        if need_loop_lookups:
            decls.append("mat4 g_ihmWorldMat;")
            decls.append("vec2 g_ihmUV = vec2(0.0);")
            decls.append("float g_ihmInter = 0.0;")
            for slot in range(n_members):
                decls.append(f"float g_ihmM{slot} = 0.0;")
        if self._sampling:
            decls.append("float g_stepScale = 1.0;")
        sp.AddFragmentShaderReplacement(
            "vec4 g_fragColor = vec4(0.0);", False, "\n".join(decls), False)

        # 2. Base::Init: build the ray->world matrix once per fragment.
        # Index 0 is the common bounding box in the multivolume form (what the
        # ray marches in) and the volume itself in the classic form — the same
        # expression is correct in both.
        if need_loop_lookups:
            sp.AddFragmentShaderReplacement(
                "//VTK::Base::Init", True,
                "//VTK::Base::Init\n"
                "g_ihmWorldMat = in_volumeMatrix[0] * in_textureDatasetMatrix[0];",
                False)

        # 3. PreComputeGradients::Impl: once per ray iteration, BEFORE the
        # per-port computeColor calls (verified against the generated source).
        # Centralized lookups: port-independent thanks to the world-space UV.
        if need_loop_lookups:
            loop = [
                "//VTK::PreComputeGradients::Impl",
                "g_ihmUV = clamp((g_ihmWorldMat * vec4(g_dataPos, 1.0)).xy "
                "* vec2(in_ihm_inv_x, in_ihm_inv_y), 0.0, 1.0);",
            ]
            loop.extend(self._lookup("ihmInterL", "in_ihm_inter", smooth=True))
            loop.append("g_ihmInter = ihmInterLValue;")
            for slot in range(n_members):
                loop.extend(self._lookup(
                    f"ihmM{slot}L", self.MEMBER_UNIFORMS[slot], smooth=True))
                loop.append(f"g_ihmM{slot} = ihmM{slot}LValue;")
            if self._sampling:
                imp = "g_ihmInter"
                for slot in range(n_members):
                    imp = f"max({imp}, g_ihmM{slot})"
                loop.append(f"float ihmImportance = {imp};")
                loop.append(
                    "g_stepScale = mix(max(in_ihm_max_step_scale, 1.0), 1.0, "
                    "ihmImportance);")
            sp.AddFragmentShaderReplacement(
                "//VTK::PreComputeGradients::Impl", True, "\n".join(loop), False)

        # 4. Gain: per real port, hook its computeColor statement. VTK keys
        # replacements by the original source string — one replacement per
        # key, so each port's statement carries exactly one composed block.
        if self._gain:
            for port, ch_id in self._gain_ports():
                stmt = self._multivolume_compute_color(port)
                sp.AddFragmentShaderReplacement(
                    stmt, False,
                    "\n".join([stmt] + self._gain_block(port, ch_id)), False)
            # Classic single-port form hedges (inert when unmatched; the
            # dummy volume keeps port count >= 2 so the multivolume form is
            # effectively always generated).
            ports = self._gain_ports()
            if ports:
                _, ch0 = ports[0]
                for stmt in (
                    "g_srcColor = computeColor(scalar, g_srcColor.a);",
                    "g_srcColor = computeColor(g_dataPos, scalar, g_srcColor.a, "
                    "in_colorTransferFunc_0[0], in_volume[0], "
                    "in_opacityTransferFunc_0[0], 0);",
                ):
                    sp.AddFragmentShaderReplacement(
                        stmt, False,
                        "\n".join([stmt] + self._gain_block(0, ch0, variant="C")),
                        False)

        # 5. Importance sampling march advances. g_terminatePointMax counts
        # BASE steps, so the progress counter must advance by the same scale
        # the position moved.
        if self._sampling:
            sp.AddFragmentShaderReplacement(
                "g_dataPos += g_dirStep;", False,
                "g_dataPos += g_dirStep * g_stepScale;", False)
            sp.AddFragmentShaderReplacement(
                "++g_currentT;", False,
                "g_currentT += g_stepScale;", False)

        # 6. Halo outline at //VTK::Base::Exit.
        if self._halo:
            sp.AddFragmentShaderReplacement(
                "//VTK::Base::Exit", True, "\n".join(self._halo_block()), False)

    @staticmethod
    def _multivolume_compute_color(port: int) -> str:
        return (
            "g_srcColor = computeColor("
            "texPos, "
            "scalar, "
            "g_srcColor.a, "
            f"in_colorTransferFunc_{port}[0], "
            f"in_volume[{port}], "
            f"in_opacityTransferFunc_{port}[0], "
            f"{port});"
        )

    def _gain_block(self, port: int, ch_id: int, variant: str = "M") -> List[str]:
        """Gain lines appended after a port's computeColor statement. Reads
        the globals computed at PreComputeGradients — no lookups here."""
        cfg = self.cfg
        slot = self._member_slot(ch_id)
        p = f"ihmG{variant}{port}"
        if slot is not None:
            return [
                f"float {p}Rgb = clamp({cfg.combined_base_rgb_gain:.6f} "
                f"+ {cfg.combined_channel_rgb_weight:.6f} * g_ihmM{slot} "
                f"+ {cfg.combined_interaction_rgb_weight:.6f} * g_ihmInter, "
                f"0.0, {cfg.combined_max_rgb_gain:.6f});",
                f"float {p}A = mix({cfg.combined_min_alpha_gain:.6f}, 1.0, "
                f"max(g_ihmM{slot}, g_ihmInter));",
                f"g_srcColor.rgb = clamp(g_srcColor.rgb * {p}Rgb, 0.0, 1.0);",
                f"g_srcColor.a = clamp(g_srcColor.a * {p}A, 0.0, 1.0);",
            ]
        return [
            f"float {p}Gain = mix({cfg.single_low_gain:.6f}, "
            f"{cfg.single_high_gain:.6f}, g_ihmInter);",
            f"g_srcColor.rgb = clamp(g_srcColor.rgb * {p}Gain, 0.0, 1.0);",
        ]

    def _halo_block(self) -> List[str]:
        cfg = self.cfg
        n = cfg.halo_exit_samples
        # Plain bilinear, NO smoothstep: the outline divides by the field's
        # screen-space derivative, and smoothstep zeroes it at cell centers.
        lookup = bilinear_lookup_lines(
            "haloUV", "haloSample", "in_ihm_inter",
            cfg.grid_w, cfg.grid_h, smooth_blend=False)
        r, g, b = cfg.halo_outline_color
        return [
            "//VTK::Base::Exit",
            "{",
            "  // The heatmap's maximum along the ray, on its own fixed-count",
            "  // walk from ray entry to ray termination. Deliberately NOT",
            "  // accumulated inside the tissue ray march: there the samples",
            "  // are gated on tissue opacity and the loop breaks early once",
            "  // alpha saturates, which dents the field wherever tissue",
            "  // occludes it and makes the outline trace tissue silhouettes.",
            "  mat4 haloWorldMat = in_volumeMatrix[0] * in_textureDatasetMatrix[0];",
            "  float haloValue = 0.0;",
            f"  for (int haloStep = 0; haloStep <= {n}; ++haloStep)",
            "  {",
            "    vec3 haloSamplePos = mix(g_rayOrigin, g_rayTermination,",
            f"        float(haloStep) / {float(n):.1f});",
            "    vec2 haloUV = (haloWorldMat * vec4(haloSamplePos, 1.0)).xy",
            "        * vec2(in_ihm_inv_x, in_ihm_inv_y);",
            "    if (all(lessThanEqual(haloUV, vec2(1.0))) &&",
            "        all(greaterThanEqual(haloUV, vec2(0.0))))",
            "    {",
            *[f"      {ln}" for ln in lookup],
            "      haloValue = max(haloValue, haloSampleValue);",
            "    }",
            "  }",
            "",
            "  // dFdx is undefined inside the tissue ray loop (non-uniform",
            "  // control flow). The walk above has a uniform trip count, so",
            "  // here screen-space derivatives are legal. Dividing the",
            "  // level-set offset by the gradient magnitude turns it into a",
            "  // signed distance to the boundary measured in PIXELS — a",
            "  // constant-width outline at any zoom, single pass.",
            "  vec2 haloGradient = vec2(dFdx(haloValue), dFdy(haloValue));",
            "  float haloSlope = max(length(haloGradient), 1.0e-7);",
            "  float haloDistance = (haloValue - in_ihm_halo_threshold) / haloSlope;",
            "",
            "  // Nothing but the line. No fill: a filled field behind the",
            "  // tissue reads as tissue-shaped holes punched into a sticker.",
            "  float haloEdge = 1.0 - smoothstep(",
            "      in_ihm_outline_width - 1.0,",
            "      in_ihm_outline_width + 1.0,",
            "      abs(haloDistance));",
            f"  g_fragColor.rgb = mix(g_fragColor.rgb, "
            f"vec3({r:.5f}, {g:.5f}, {b:.5f}), haloEdge);",
            "  g_fragColor.a = mix(g_fragColor.a, 1.0, haloEdge);",
            "}",
        ]
