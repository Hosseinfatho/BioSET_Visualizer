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

def interleave_maps(maps: Sequence[Optional[np.ndarray]],
                    shape: Tuple[int, int]) -> np.ndarray:
    """Pack up to 4 (h, w) maps into one contiguous (h, w, 4) float32 image.

    Missing slots stay zero, which every effect formula reads as "absent".
    """
    h, w = shape
    out = np.zeros((h, w, 4), dtype=np.float32)
    for i, m in enumerate(maps[:4]):
        if m is not None:
            out[..., i] = m
    return np.ascontiguousarray(out)


def slot_layout(n_members: int) -> List[List[Optional[int]]]:
    """Which member index lives in which (texture, component) slot.

    Texture 0 reserves component 0 (R) for the interaction map, so it holds
    members 0-2; each further texture holds 4 more. Returns one 4-entry list
    per texture, each holding a member index or None.
    """
    layout: List[List[Optional[int]]] = []
    member = 0
    while True:
        slots: List[Optional[int]] = [None, None, None, None]
        start = 1 if not layout else 0
        for c in range(start, 4):
            if member < n_members:
                slots[c] = member
                member += 1
        layout.append(slots)
        if member >= n_members:
            return layout


def textures_needed(n_members: int) -> int:
    return len(slot_layout(n_members))


def blur_map(m: np.ndarray, sigma_cells: float) -> np.ndarray:
    """Gaussian-blur a map, mixing occupied cells only.

    The extra smoothing above what the texture filter gives (config
    `map_smoothing > 1`). A plain blur would bleed signal into empty cells and
    soften the tissue border, so the weights are normalized by a blur of the
    occupancy mask — occupied cells average with occupied neighbours — and the
    empty cells are restored to exactly 0 afterwards. That keeps the invariant
    the whole map pipeline rests on: 0 means "absent", never "weak".

    Runs per level change, not per frame.
    """
    if sigma_cells <= 0.0 or m.size == 0:
        return m
    from scipy import ndimage as ndi
    occupied = m > 0
    if not occupied.any():
        return m
    weight = ndi.gaussian_filter(np.where(occupied, m, 0.0).astype(np.float32),
                                 sigma_cells, mode="nearest")
    norm = ndi.gaussian_filter(occupied.astype(np.float32),
                               sigma_cells, mode="nearest")
    smoothed = np.where(norm > 1e-6, weight / np.maximum(norm, 1e-6), m)
    return np.where(occupied, smoothed, 0.0).astype(np.float32)


def occupancy_gamma(occupancy: float,
                    cfg: IntegratedHeatmapConfig = INTEGRATED_HEATMAP) -> float:
    """Gamma that makes a map of this occupancy read as dark as a fine one.

    Coarse levels lose their picture to aggregation, not to contrast. Measured
    on mis_v3, the share of cells with any signal climbs 45% (Fine) -> 73%
    (Overview) while rank equalization holds the median at 0.575 either way, so
    the empty-vs-occupied distinction that carries the fine view flattens into
    a uniform mid-tone: only 48% of the Overview map reads dark against 68% of
    the Fine one.

    So target that 68% at every level and solve for the gamma that gets there.
    Rank equalization leaves the value uniform on [0,1] before the floor is
    applied, which makes it a closed form rather than a search:

        need  = share of NONZERO cells that must fall below the dark threshold
        gamma = log(threshold in equalized units) / log(need)

    Level 0 lands on 1.0 by construction, so fine detail is untouched. Derived
    from occupancy rather than a per-level table so it still holds for other
    datasets, combinations and radii, whose occupancy curves differ.
    """
    occ = float(np.clip(occupancy, 1e-6, 1.0))
    floor = float(cfg.map_nonzero_floor)
    # Where the dark threshold sits once the floor is removed.
    thresh = (float(cfg.map_dark_threshold) - floor) / max(1.0 - floor, 1e-6)
    if not 0.0 < thresh < 1.0:
        return 1.0
    need = (float(cfg.map_target_dark_fraction) - (1.0 - occ)) / occ
    if need <= 0.0:
        # Already dark enough from empty cells alone — nothing to do.
        return 1.0
    if need >= 1.0:
        return float(cfg.map_gamma_max)
    return float(np.clip(np.log(thresh) / np.log(need),
                         1.0, cfg.map_gamma_max))


def apply_map_contrast(frac: np.ndarray,
                       cfg: IntegratedHeatmapConfig = INTEGRATED_HEATMAP,
                       gamma: Optional[float] = None
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

    g = cfg.map_gamma if gamma is None else float(gamma)
    if g != 1.0:
        scaled = scaled ** g

    floor = float(cfg.map_nonzero_floor)
    out[nz] = (floor + (1.0 - floor) * scaled).astype(np.float32)
    return out


# ──────────────────────────────────────────────────────────────
# GPU map textures
# ──────────────────────────────────────────────────────────────

class IhmMapTextures:
    """The heatmap maps as RGBA float32 GPU textures.

    Why textures rather than the mat4 uniforms this replaces: a dynamically
    indexed uniform array occupies real constant registers, ceil(gw*gh/16)*4
    per map against ~1024 on NVIDIA, which is what pinned the map to a 16x16
    grid over the whole volume (95 x 48 um cells) at every zoom.

    THE BINDING IS THE WHOLE TRICK, and it is not obvious. Taken from
    integrated_heatmap_trame/final_texture_heatmaps.py, which gets right three
    things an earlier attempt here got wrong:

      * vtkOpenGLUniforms will push an int every draw but insists on declaring
        it `uniform int`. So the unit is stored with SetUniformi and only the
        GLSL DECLARATION is rewritten to `uniform sampler2D` (see
        `declaration_replacements`). Declaring the sampler separately and
        pushing an unrelated int leaves the declaration and the value as two
        different objects, and nothing arrives. Doing it this way also covers
        the first frame after a shader rebuild, which an UpdateShaderEvent
        observer would miss.

      * the texture must stay ACTIVE for the mapper's whole render — Render()
        on VolumeMapperRenderStartEvent, PostRender() on
        VolumeMapperRenderEndEvent. That is what makes VTK's texture-unit
        manager reserve the unit; otherwise the mapper takes it for its own
        volume texture and every sample reads zero.

      * vtkOpenGLTexture + SetInputData, NOT vtkTextureObject.Create2DFromRaw.
        The high-level object participates in unit management; the low-level
        one bypasses it.

    Main thread only (GL objects).
    """

    MAX_TEXTURES = 3          # interaction + up to 11 member slots
    UNIFORMS = ("in_ihm_maps0", "in_ihm_maps1", "in_ihm_maps2")

    def __init__(self, interpolate: bool = True):
        # False renders cells as hard squares (config `map_smoothing = 0`).
        self.interpolate = bool(interpolate)
        self._render_window = None
        self._textures: List[object] = []
        self._keep: List[tuple] = []      # numpy/vtk refs must outlive a render
        self._units: List[int] = []
        self._shape: Optional[Tuple[int, int]] = None
        self._bound = False

    def set_render_window(self, render_window):
        self._render_window = render_window

    @property
    def count(self) -> int:
        return len(self._textures)

    @property
    def shape(self) -> Optional[Tuple[int, int]]:
        return self._shape

    def ensure(self, n_textures: int) -> int:
        """Allocate lazily; returns the count now held. The count is
        STRUCTURAL (baked into the generated GLSL), so callers must rebuild
        the shader when it changes."""
        from vtkmodules.vtkRenderingOpenGL2 import vtkOpenGLTexture
        from vtkmodules.vtkRenderingCore import vtkTexture
        n = max(1, min(int(n_textures), self.MAX_TEXTURES))
        while len(self._textures) > n:
            self._textures.pop()
            self._keep.pop()
            if self._units:
                self._units.pop()
        while len(self._textures) < n:
            tex = vtkOpenGLTexture()
            tex.SetColorModeToDirectScalars()
            tex.SetQualityTo32Bit()
            # Linear filtering, so the map does not read as hard-edged blocks.
            # The reference smooths in the shader with a 4-tap bilinear; one
            # filtered fetch is the same result for a quarter of the taps.
            # RGBA32F filtering is not promised by every GL profile, so it was
            # measured (scratchpad/probe_filter.py): a two-cell step sampled at
            # its midpoint reads 0.489, i.e. hardware linear ran.
            tex.SetInterpolate(1 if self.interpolate else 0)
            tex.MipmapOff()
            tex.SetWrap(vtkTexture.ClampToEdge)
            try:
                tex.UseSRGBColorSpaceOff()
            except Exception:
                pass
            self._textures.append(tex)
            self._keep.append(())
            self._units.append(0)
        return len(self._textures)

    def upload(self, images: Sequence[np.ndarray]) -> bool:
        """One (h, w, 4) float32 image per texture."""
        from vtkmodules.util.numpy_support import numpy_to_vtk
        from vtkmodules.vtkCommonCore import VTK_FLOAT
        from vtkmodules.vtkCommonDataModel import vtkImageData
        if not self._textures or not images:
            return False
        h, w = images[0].shape[:2]
        for i, (tex, img) in enumerate(zip(self._textures, images)):
            packed = np.ascontiguousarray(img, dtype=np.float32)
            image = vtkImageData()
            image.SetDimensions(w, h, 1)
            image.SetSpacing(1.0, 1.0, 1.0)
            image.SetOrigin(0.0, 0.0, 0.0)
            arr = numpy_to_vtk(packed.reshape((-1, 4), order="C"), deep=True,
                               array_type=VTK_FLOAT)
            arr.SetName(f"ihm_maps{i}")
            image.GetPointData().SetScalars(arr)
            tex.SetInputData(image)
            # Hold the numpy AND vtk objects: dropping them mid-render is a
            # use-after-free, not merely a reload.
            self._keep[i] = (packed, image, arr)
        self._shape = (h, w)
        return True

    def declaration_replacements(self) -> List[Tuple[str, str]]:
        """(original, replacement) pairs turning each pushed int uniform's
        declaration into a sampler2D."""
        return [(f"uniform int {self.UNIFORMS[i]};",
                 f"uniform sampler2D {self.UNIFORMS[i]};")
                for i in range(len(self._textures))]

    def seed_uniforms(self, uniforms):
        """Register the ints so VTK emits `uniform int` declarations for the
        replacements above to rewrite."""
        for i in range(len(self._textures)):
            uniforms.SetUniformi(self.UNIFORMS[i], int(self._units[i]))

    def bind(self, renderer, uniforms) -> bool:
        """VolumeMapperRenderStartEvent: activate and publish the units."""
        if not self._textures or renderer is None:
            return False
        if self._render_window is not None:
            self._render_window.MakeCurrent()
        for i, tex in enumerate(self._textures):
            tex.Render(renderer)
            unit = int(tex.GetTextureUnit())
            if unit < 0:
                tex.PostRender(renderer)
                print(f"[ihm] texture {i} failed to activate")
                return False
            self._units[i] = unit
            uniforms.SetUniformi(self.UNIFORMS[i], unit)
        self._bound = True
        return True

    def release(self, renderer):
        """VolumeMapperRenderEndEvent."""
        if not self._bound:
            return
        for tex in self._textures:
            tex.PostRender(renderer)
        self._bound = False

    def clear(self):
        self._textures = []
        self._keep = []
        self._units = []
        self._shape = None
        self._bound = False


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

    # Sampler uniforms come from the texture helper, NOT from the member cap:
    # the old MEMBER_UNIFORMS was sized off the config singleton at import
    # time, which silently caps the member count the moment the cap is raised.
    SAMPLER_UNIFORMS = IhmMapTextures.UNIFORMS
    FLOAT_UNIFORMS = (
        "in_ihm_inv_x", "in_ihm_inv_y",
        "in_ihm_max_step_scale",
    )

    def __init__(self, cfg: IntegratedHeatmapConfig = INTEGRATED_HEATMAP):
        self.cfg = cfg
        self._multi_volume = None
        self._mapper = None
        self._channel_port: Dict[int, int] = {}   # channel id -> port
        self._dummy_port: Optional[int] = None

        self._active = False
        self._gain = True
        self._sampling = True

        self._textures = IhmMapTextures(interpolate=cfg.uv_smoothing > 0.0)
        self._have_maps = False
        self._n_textures = 1
        self._observer_mapper = None
        self._renderer = None
        self._member_ids: Tuple[int, ...] = ()

        self._inv_x = 0.0
        self._inv_y = 0.0

        self._installed_signature: Optional[tuple] = None
        self._map_cache: "OrderedDict[tuple, tuple]" = OrderedDict()

    @property
    def uniform_names(self) -> tuple:
        return self.SAMPLER_UNIFORMS + self.FLOAT_UNIFORMS

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
        self._attach_texture_observers(mapper)
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

    def set_effects(self, gain: bool, sampling: bool):
        changed = (self._gain, self._sampling) != (gain, sampling)
        self._gain, self._sampling = bool(gain), bool(sampling)
        if changed and self._active:
            self.rebuild()

    def set_render_window(self, render_window):
        """Needed for MakeCurrent before the texture is activated."""
        self._textures.set_render_window(render_window)

    def _attach_texture_observers(self, mapper):
        """Hold the map textures active for the mapper's whole render.

        That is what makes VTK's texture-unit manager reserve their units; the
        mapper otherwise claims them for its own volume textures and every
        sample reads zero. Registered per mapper, so a multivolume rebuild
        re-arms them.

        Deliberately NOT UpdateShaderEvent: observing that with callData
        segfaults this VTK build, and it would also miss the first frame after
        a shader rebuild.
        """
        if mapper is None or self._observer_mapper is mapper:
            return
        from vtkmodules.vtkCommonCore import vtkCommand

        def _start(caller, event):
            if self._active and self._have_maps:
                sp = self._shader_property()
                if sp is not None:
                    self._textures.bind(self._renderer, sp.GetFragmentCustomUniforms())

        def _end(caller, event):
            self._textures.release(self._renderer)

        mapper.AddObserver(vtkCommand.VolumeMapperRenderStartEvent, _start)
        mapper.AddObserver(vtkCommand.VolumeMapperRenderEndEvent, _end)
        self._observer_mapper = mapper

    def set_renderer(self, renderer):
        self._renderer = renderer

    def set_world_extent(self, x_max: float, y_max: float):
        self._inv_x = 1.0 / x_max if x_max > 0 else 0.0
        self._inv_y = 1.0 / y_max if y_max > 0 else 0.0

    # ── maps ───────────────────────────────────────────────

    def compute_maps(self, loader, combo_names: Sequence[str], dilation: float,
                     name_to_id: Dict[str, int], active_ids: Sequence[int],
                     hierarchy_level: int = 0):
        """(interaction map, member maps by channel id) at `hierarchy_level`.

        Resolution follows the heatmap LOD, so the map the shader samples
        resolves as you zoom instead of sitting on a fixed 16x16 grid over the
        whole slide (95 x 48 um cells).

        The field must be UNCROPPED. HeatmapLOD crops at the fine levels, and
        `crop_field_to_roi` zeroes everything outside the ROI — gain multiplies
        volume colour by this map, so a cropped one would dim the volume
        everywhere outside the viewport.
        """
        key = (tuple(combo_names), round(float(dilation), 4), int(hierarchy_level))
        cached = self._map_cache.get(key)
        if cached is None:
            inter = self._field_to_map(loader.get_heatmap_field(
                channels=list(combo_names), dilation=dilation,
                hierarchy_level=hierarchy_level))
            member_by_name = {}
            for name in combo_names:
                m = self._field_to_map(loader.get_heatmap_field(
                    channels=[name], dilation=dilation,
                    hierarchy_level=hierarchy_level))
                if m is not None:
                    member_by_name[name] = m
            cached = (inter, member_by_name)
            self._map_cache[key] = cached
            while len(self._map_cache) > 12:
                self._map_cache.popitem(last=False)

        inter, member_by_name = cached
        active_set = set(active_ids)
        members_by_id: Dict[int, np.ndarray] = {}
        skipped = 0
        for name in combo_names:
            ch_id = name_to_id.get(name)
            if ch_id is None or ch_id not in active_set:
                continue
            if name not in member_by_name:
                continue
            if len(members_by_id) >= self.cfg.max_member_maps:
                skipped += 1
                continue
            members_by_id[ch_id] = member_by_name[name]
        if skipped:
            print(f"[ihm] member-map cap {self.cfg.max_member_maps} reached; "
                  f"{skipped} member channel(s) fall back to the interaction map")
        return inter, members_by_id

    def _field_to_map(self, field) -> Optional[np.ndarray]:
        """Dense contrast-stretched (ny, nx) map from a HeatmapField.

        `downsample_field_to_grid` used to reduce the field onto the fixed
        shader grid with analytic denominators. At the field's own resolution
        that reduction is the identity — `field.fractions` is already the
        per-cell fraction — so this is a scatter plus the same contrast stretch.

        The stretch is gamma-corrected for how occupied THIS map is, which is
        what keeps the coarse levels from washing out (see `occupancy_gamma`).
        """
        if field is None or field.counts.size == 0:
            return None
        dense = np.zeros((field.ny, field.nx), dtype=np.float32)
        dense[field.cells_yx[:, 0], field.cells_yx[:, 1]] = field.fractions
        occupancy = float(np.count_nonzero(dense)) / max(dense.size, 1)
        gamma = occupancy_gamma(occupancy, self.cfg)
        out = apply_map_contrast(dense, self.cfg, gamma=gamma)
        out = blur_map(out, self.cfg.map_blur_cells)
        return np.ascontiguousarray(out, dtype=np.float32)

    def set_maps(self, inter: Optional[np.ndarray],
                 members_by_id: Dict[int, np.ndarray]):
        """Install new maps into the GPU textures.

        Uniform-push when nothing structural moved; full rebuild when the
        member id set or the TEXTURE COUNT changes — both are baked into the
        generated GLSL (slot assignment, and one sampler declaration each).
        """
        if not self._check_main_thread():
            return
        if inter is None:
            self._have_maps = False
            if self._active:
                self.clear()
            return

        new_ids = tuple(members_by_id.keys())
        n_tex = textures_needed(len(new_ids))
        if self._textures.ensure(n_tex) != n_tex:
            print("[ihm] map textures unavailable — effects off")
            self._have_maps = False
            if self._active:
                self.clear()
            return

        member_maps = [members_by_id[c] for c in new_ids]
        images = []
        for tex, slots in enumerate(slot_layout(len(new_ids))):
            chans: List[Optional[np.ndarray]] = [None, None, None, None]
            if tex == 0:
                chans[0] = inter
            for comp, member in enumerate(slots):
                if member is not None:
                    chans[comp] = member_maps[member]
            images.append(interleave_maps(chans, inter.shape[:2]))
        self._textures.upload(images)
        self._have_maps = True

        self._member_ids = new_ids
        self._n_textures = n_tex
        if not self._active:
            return
        if self._installed_signature != self._signature():
            self.rebuild()
        else:
            self.update_uniforms()

    # ── install / clear ────────────────────────────────────

    def _signature(self) -> tuple:
        return (
            self._active, self._gain, self._sampling,
            tuple(sorted(self._channel_port.items())),
            self._member_ids, self._n_textures, self._dummy_port,
            # The grid size is baked into the generated texelFetch, so a level
            # change is structural even when the member set is unchanged.
            self._textures.shape,
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

        if (self._active and self._channel_port and self._have_maps
                and (self._gain or self._sampling)):
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
            ])
            print(f"[ihm] installed: gain={self._gain} "
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
        # Seed the sampler units as ordinary ints. VTK declares them
        # `uniform int` and pushes them every draw; `_install_replacements`
        # rewrites only those declarations to sampler2D. That pairing is what
        # makes the binding work at all — see IhmMapTextures.
        self._textures.seed_uniforms(uniforms)
        uniforms.SetUniformf("in_ihm_inv_x", float(self._inv_x))
        uniforms.SetUniformf("in_ihm_inv_y", float(self._inv_y))
        uniforms.SetUniformf("in_ihm_max_step_scale", float(cfg.sampling_max_step_scale))

    # ── GLSL generation ────────────────────────────────────

    def _gain_ports(self) -> List[Tuple[int, int]]:
        """(port, channel_id) for real ports, sorted by port. Never the dummy."""
        ports = sorted((port, ch) for ch, port in self._channel_port.items())
        assert self._dummy_port is None or all(
            p != self._dummy_port for p, _ in ports), "dummy port in gain ports"
        return ports

    def _sampler_lookup(self, n_members: int) -> List[str]:
        """One filtered fetch per texture, filling g_ihmInter and g_ihmM{slot}.

        Smoothed, not blocky: an unfiltered fetch made every cell boundary a
        hard edge, worst at the coarse levels where one cell spans ~1024
        voxels. The texture filters in hardware (see `ensure`), and the UV is
        warped by a smoothstep first so the blend is C1 at cell centres rather
        than merely continuous — the same curve the reference applies to its
        4-tap bilinear, at one tap instead of four.

        Cost: the warp is 6 ALU ops per ray step, shared by every map in every
        texture; only the fetches scale with the texture count.
        """
        shape = self._textures.shape or (1, 1)
        gh, gw = shape
        size = f"vec2({float(gw):.1f}, {float(gh):.1f})"
        w = self.cfg.uv_smoothing
        if w <= 0.0:
            # map_smoothing = 0: no warp, and `ensure` leaves the texture on
            # nearest filtering, so cells render as hard squares.
            lines = ["vec2 g_ihmSm = g_ihmUV;"]
        else:
            # Texel centres sit at (i+0.5)/N, so shift by half a texel to get
            # cell-relative coordinates, warp the fractional part, shift back.
            warp = "g_ihmF * g_ihmF * (3.0 - 2.0 * g_ihmF)"
            if w < 1.0:
                warp = f"mix(g_ihmF, {warp}, {w:.6f})"
            lines = [
                f"vec2 g_ihmP = g_ihmUV * {size} - 0.5;",
                "vec2 g_ihmF = fract(g_ihmP);",
                f"vec2 g_ihmSm = (floor(g_ihmP) + {warp} + 0.5) / {size};",
            ]
        for tex, slots in enumerate(slot_layout(n_members)):
            lines.append(
                f"vec4 g_ihmT{tex} = texture({self.SAMPLER_UNIFORMS[tex]}, "
                f"g_ihmSm);")
            if tex == 0:
                lines.append("g_ihmInter = clamp(g_ihmT0.r, 0.0, 1.0);")
            for comp, member in enumerate(slots):
                if member is not None:
                    lines.append(f"g_ihmM{member} = clamp("
                                 f"g_ihmT{tex}.{'rgba'[comp]}, 0.0, 1.0);")
        return lines

    def _install_replacements(self, sp):
        cfg = self.cfg
        need_loop_lookups = self._gain or self._sampling
        n_members = len(self._member_ids)

        # 1. Global declarations, piggybacked on a statement that sits at
        # global scope in the generated shader (leaves //VTK::Base::Dec free
        # for the shader-dump tool).
        # Turn each pushed int uniform's DECLARATION into a sampler2D. The
        # value keeps arriving through VTK's own uniform machinery, which is
        # what covers the first frame after a shader rebuild.
        if need_loop_lookups:
            for original, replacement in self._textures.declaration_replacements():
                sp.AddFragmentShaderReplacement(original, False, replacement, False)

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
            loop.extend(self._sampler_lookup(n_members))
            if self._sampling:
                imp = "g_ihmInter"
                for slot in range(n_members):
                    imp = f"max({imp}, g_ihmM{slot})"
                loop.append(f"float ihmImportance = {imp};")
                # Same response curve as the gain, so the step ramp and the
                # brightness ramp agree instead of differing by a curve.
                loop.append(self._curve_line("ihmImportance"))
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

        # The outline used to be injected at //VTK::Base::Exit here. It is
        # real contour geometry now (scene/heatmap_contours.py): the shader
        # version thresholded a maximum-intensity projection and divided by
        # dFdx/dFdy, so it broke into dashes wherever the arg-max sample
        # changed between neighbouring pixels, vanished on plateaus, and
        # changed shape as the camera orbited.

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
        the globals computed at PreComputeGradients — no lookups here.

        NEITHER RAMP MAY PUSH A CHANNEL PAST 1.0, and that is load-bearing.
        They used to run to 1.9 (combined) and 4.0 (single) and clamp per
        channel, which silently desaturated any colour without a zero
        component: the channel colour is the user's tint straight off the
        transfer function, so (1.0, 0.5, 0.5) x 1.9 clamped to (1.0, .95, .95)
        — white. A saturated primary survived only because its zero channels
        had nothing to clip.

        So the hot end is not a constant: it is PER FRAGMENT, the largest
        factor that still leaves the brightest channel at 1.0. The colour
        transfer function ramps black -> tint, so most samples sit well below
        their tint and that factor is genuinely > 1 — hot regions brighten
        (~1.8x measured) while every channel keeps a fixed ratio to the others,
        which is what makes whitening impossible rather than merely unlikely.

        A flat 1.0 hot end — the previous fix — could not clip either, but it
        made a hot region identical to an unmodulated volume, so nothing
        popped and neighbouring cells were hard to tell apart. Measured on two
        map values 0.061 apart (the level-0 median between neighbours), the
        rendered separation goes 1.0x -> 3.2x from the per-fragment ceiling
        and 4.5x once the response curve is added.
        """
        cfg = self.cfg
        slot = self._member_slot(ch_id)
        p = f"ihmG{variant}{port}"
        cold = min(max(float(cfg.cold_dimness), 0.0), 1.0)
        # Per-fragment ceiling, shared by both formulas below.
        hi = [
            f"float {p}Mx = max(max(g_srcColor.r, g_srcColor.g), g_srcColor.b);",
            f"float {p}Hi = ({p}Mx > 1e-4) ? min(1.0 / {p}Mx, "
            f"{max(float(cfg.hot_brightness), 1.0):.6f}) : 1.0;",
        ]
        if slot is not None:
            # The two map weights, renormalized so V is a true 0..1 ramp
            # parameter — the curve has to act on the interpolation fraction,
            # not on a value that already carries the cold offset.
            span = (cfg.combined_channel_rgb_weight
                    + cfg.combined_interaction_rgb_weight)
            wc = cfg.combined_channel_rgb_weight / span if span > 0 else 0.0
            wi = cfg.combined_interaction_rgb_weight / span if span > 0 else 0.0
            return hi + [
                f"float {p}V = clamp({wc:.6f} * g_ihmM{slot} "
                f"+ {wi:.6f} * g_ihmInter, 0.0, 1.0);",
                self._curve_line(f"{p}V"),
                f"float {p}A = mix({cfg.combined_min_alpha_gain:.6f}, 1.0, "
                f"max(g_ihmM{slot}, g_ihmInter));",
                # No clamp on rgb: {p}Hi is by definition the factor that lands
                # the brightest channel exactly on 1.0, and a clamp here is
                # precisely what used to break the colours.
                f"g_srcColor.rgb *= {p}Hi * mix({cold:.6f}, 1.0, {p}V);",
                f"g_srcColor.a = clamp(g_srcColor.a * {p}A, 0.0, 1.0);",
            ]
        return hi + [
            f"float {p}V = g_ihmInter;",
            self._curve_line(f"{p}V"),
            f"g_srcColor.rgb *= {p}Hi * mix({cold:.6f}, 1.0, {p}V);",
        ]

    def _curve_line(self, var: str) -> str:
        """S-curve the ramp value, so the steep part of the response lands
        where rank equalization put the bulk of the cells.

        `cell_contrast` is how many times the smoothstep is applied — each
        application steepens the middle further, toward a binary hot/cold
        split. Fractional values blend the last one, so the knob is continuous.

        Empty cells sit at 0 and smoothstep(0) == 0 however many times it is
        applied, so the nonzero floor's "absent vs present-but-weak"
        distinction survives untouched at any setting.
        """
        c = max(0.0, float(self.cfg.cell_contrast))
        if c <= 0.0:
            return f"// {var}: linear response (cell_contrast = 0)"
        lines = []
        whole, frac = int(c), c - int(c)
        for _ in range(whole):
            lines.append(f"{var} = smoothstep(0.0, 1.0, {var});")
        if frac > 1e-6:
            lines.append(
                f"{var} = mix({var}, smoothstep(0.0, 1.0, {var}), {frac:.6f});")
        return "\n".join(lines)
