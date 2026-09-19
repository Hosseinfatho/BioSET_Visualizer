"""Label pipeline: LLM label text in, placed labels out.

Replaces the previous hierarchy/placement system with the vendored engine in
`flagpole.py`. The old one re-ran per-label raycasting on every camera settle
and chose a label style from camera distance; this one solves the whole screen
at once in NumPy and is fixed at the one scale labels are offered at.

Three label kinds come from ONE Biomni /label response, and the prompt already
encodes which is which in the key format:

    "MART1"           a single marker            -> per-component callouts
    "MART1+SOX10"     co-expression, same cells  -> conforming surface text
    "CD8a/MART1"      two populations in contact -> interaction callouts

Splitting on those separators is the whole parse. `+` becomes colocalization
because co-expression means the markers occupy the same voxels; `/` becomes
interaction because it means adjacency without overlap — which is exactly the
distinction `build_coloc_sites` and `build_interaction_components` draw from
the r=0 fields.

Threading follows the old contract: heavy work on a worker, VTK actor creation
on the main thread via `check_and_apply_setup()` from the poll loop.
"""
from __future__ import annotations

import queue
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import sites as _sites
from .flagpole import (
    COLOC_MERGE_BINS, COLOC_MIN_BINS, COLOC_MIN_SEPARATION, COLOC_MIN_VALUE,
    COLOC_WRAP_ASPECT, INTERACTION_LEASH_PX, INTERACTION_MERGE_BINS,
    INTERACTION_FONT_SIZE, SINGLE_MARKER_FONT_SIZE,
    INTERACTION_LABEL_BACKGROUND, INTERACTION_LABEL_BACKGROUND_OPACITY,
    INTERACTION_LABEL_COLOR, INTERACTION_LABEL_UPPERCASE,
    INTERACTION_MIN_BINS, INTERACTION_MIN_SEPARATION, INTERACTION_PRIORITY,
    SINGLE_MARKER_MAX_SHOWN, SINGLE_MARKER_MIN_SCREEN_PX, SINGLE_MARKER_PRIORITY,
    INTERACTION_RADIUS_BINS, MAX_COLOC_SITES, MAX_COMPONENTS_PER_CHANNEL,
    MAX_INTERACTION_SITES, COLOC_PROXY_DECIMATE, COLOC_PROXY_DILATION,
    COLOC_PROXY_SMOOTH, Channel, ColocConformer, FlagpoleLayout, MeshSoup,
    build_coloc_sites, build_interaction_components, build_proxy_surface,
    decimate_polydata, extract_components,
)

# `sites.dense_field` yields ACTIVE BIN COUNTS, matching the units the
# reference's own heatmaps used, so its thresholds carry over directly:
# COLOC_MIN_VALUE is "a bin needs at least this much to join a region", and a
# cell counts as occupied for interaction purposes when it holds any signal.
INTERACTION_MIN_COUNT = 0


def parse_label_keys(raw_labels: dict):
    """Split a /label response into the three kinds by key format.

    Returns (singles, colocs, interactions), each {key: display_text}.
    """
    singles: Dict[str, str] = {}
    colocs: Dict[str, str] = {}
    inters: Dict[str, str] = {}
    for key, value in (raw_labels or {}).items():
        text = _label_text(value)
        if not text:
            continue
        if "/" in key:
            inters[key] = text
        elif "+" in key:
            colocs[key] = text
        else:
            singles[key] = text
    return singles, colocs, inters


def _label_text(value) -> str:
    """['title', 'subtitle'] -> 'title\\nsubtitle'."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)) and value:
        parts = [str(v).strip() for v in value if str(v).strip()]
        parts = [p for p in parts if p.lower() != "none"]
        return "\n".join(parts[:2])
    return ""


def _members(key: str) -> List[str]:
    """Marker names in a key, in the order written."""
    out: List[str] = []
    for side in key.split("/"):
        out.extend(m.strip() for m in side.split("+") if m.strip())
    return out


class LabelSceneManager:
    """Owns the label engine for one placement pass.

    Rebuilt whenever a new /label response arrives, because the labels, the
    viewport and the loaded tiles all change together.
    """

    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bioset_labels")

    # Proxies hold geometry, so only a few viewports' worth are kept.
    PROXY_CACHE_SIZE = 6

    def __init__(self, renderer, render_window=None, overlay_renderer=None):
        self.renderer = renderer
        self.render_window = render_window or (
            renderer.GetRenderWindow() if renderer else None)
        self.overlay_renderer = (overlay_renderer
                                 if overlay_renderer is not None
                                 else self._find_overlay_renderer())

        self._layout: Optional[FlagpoleLayout] = None
        self._conformer: Optional[ColocConformer] = None
        self._channels: List[Channel] = []
        self._preprocessed = False
        self._visible = True
        self._gesturing = False
        self._owned_props: List[object] = []
        self._proxy_cache: "OrderedDict[tuple, object]" = OrderedDict()
        # Set by _fail, drained by the UI so a silent give-up becomes a message.
        self.last_error: Optional[str] = None
        # Set on a successful build, drained by the UI. Says what was placed.
        self.last_report: Optional[dict] = None
        self._result_queue: queue.Queue = queue.Queue()

    def _find_overlay_renderer(self):
        """Frontmost renderer sharing our camera, or None.

        The app stacks three renderers on one camera: heatmap fill at layer 0,
        the volume at layer 1, the heatmap outline at layer 2. Conforming label
        patches sit on a surface INSIDE the tissue, so in the volume's own
        renderer the ray caster composites the block in front of them and the
        text washes out. Layer 2 already exists to draw in front of the volume,
        and shares the camera, so putting the patches there keeps registration
        exact and makes them visible.

        Found rather than plumbed: "the frontmost layer on this camera" is
        well defined from the render window, and threading a new argument
        through the scene builder and the callback layer to say the same thing
        would be more to keep in step. Falls back to None — meaning "use the
        main renderer" — whenever there is no later layer, as in the tests.
        """
        rw, ren = self.render_window, self.renderer
        if rw is None or ren is None:
            return None
        try:
            cam = ren.GetActiveCamera()
            best, best_layer = None, ren.GetLayer()
            coll = rw.GetRenderers()
            coll.InitTraversal()
            for _ in range(coll.GetNumberOfItems()):
                r = coll.GetNextItem()
                if r is None or r is ren:
                    continue
                # Same camera only: a different camera would put the labels
                # somewhere else entirely.
                if r.GetActiveCamera() is not cam:
                    continue
                if r.GetLayer() > best_layer:
                    best, best_layer = r, r.GetLayer()
            return best
        except Exception:
            return None

    # ── build ──────────────────────────────────────────────

    def start_preprocessing(self, mesh_mgr, loader, roi_vox,
                            name_to_channel_idx: Dict[str, int],
                            raw_labels: dict, overall: Optional[list] = None,
                            colors: Optional[Dict[str, tuple]] = None):
        """Kick off the off-thread build for the CURRENT viewport."""
        self._preprocessed = False
        singles, colocs, inters = parse_label_keys(raw_labels)
        print(f"[labels] parsed {len(singles)} single, {len(colocs)} coloc, "
              f"{len(inters)} interaction label(s)")
        self._executor.submit(
            self._preprocess, mesh_mgr, loader, roi_vox,
            dict(name_to_channel_idx), singles, colocs, inters,
            dict(colors or {}))

    def _proxy_for(self, name: str, polydata):
        """Cached dilated/smoothed/decimated proxy for one channel's surface.

        Building one costs a few hundred milliseconds per channel, and every
        Label press over the same tiles would otherwise pay it again. Keyed on
        the geometry's own size so a changed viewport misses and a repeat
        press hits.
        """
        key = (name, polydata.GetNumberOfPoints(), polydata.GetNumberOfCells())
        hit = self._proxy_cache.get(key)
        if hit is not None:
            self._proxy_cache.move_to_end(key)
            return hit
        pr = build_proxy_surface(polydata, COLOC_PROXY_DILATION,
                                 COLOC_PROXY_SMOOTH)
        if COLOC_PROXY_DECIMATE > 0.0:
            pr = decimate_polydata(pr, COLOC_PROXY_DECIMATE)
        self._proxy_cache[key] = pr
        while len(self._proxy_cache) > self.PROXY_CACHE_SIZE:
            self._proxy_cache.popitem(last=False)
        return pr

    def _preprocess(self, mesh_mgr, loader, roi_vox, name_to_idx,
                    singles, colocs, inters, colors):
        """Worker: geometry and site detection. No GL, no actors."""
        t0 = time.perf_counter()
        try:
            surfaces: Dict[str, object] = {}
            for name, idx in name_to_idx.items():
                pd = _sites.welded_surface(mesh_mgr, idx, roi_vox)
                if pd is not None and pd.GetNumberOfPoints() > 0:
                    surfaces[name] = pd
            if not surfaces:
                self._fail("No surface geometry is loaded for the channels in "
                           "view — the tiles may still be streaming.")
                return

            channels: List[Channel] = []

            # 1. one Channel per single-marker label, components from its mesh
            for key, text in singles.items():
                pd = surfaces.get(key)
                if pd is None:
                    continue
                comps = extract_components(key, pd, MAX_COMPONENTS_PER_CHANNEL)
                if not comps:
                    continue
                # Per-cell labels are the background layer. They are gated
                # on how big their component is ON SCREEN, so their density
                # follows the zoom instead of dumping a hundred callouts over
                # the tissue the moment labelling is switched on, and they rank
                # below every colocalization and contact site.
                ch = Channel(name=key, display_text=text, polydata=pd,
                             label_color=colors.get(key, (1.0, 1.0, 1.0)),
                             font_size=SINGLE_MARKER_FONT_SIZE,
                             priority_weight=SINGLE_MARKER_PRIORITY,
                             min_screen_px=SINGLE_MARKER_MIN_SCREEN_PX,
                             max_shown=SINGLE_MARKER_MAX_SHOWN)
                ch.components = comps
                channels.append(ch)

            # Fitting targets a dilated, smoothed, decimated PROXY of each
            # surface; clearance targets the raw geometry. Both matter:
            # text fitted to the raw surface bends about half its own height
            # over a label-sized window and is unreadable, and raycasting the
            # full-resolution mesh on every refit is what made settles cost
            # hundreds of milliseconds. The proxy is only ever sampled at
            # scaffold resolution, so decimating it is free in quality.
            raw_pd = list(surfaces.values())
            proxies = [self._proxy_for(name, surfaces[name])
                       for name in surfaces]
            soup = MeshSoup(proxies, occluders=raw_pd)

            # 2. colocalization sites, from the r=0 intersection field
            coloc_sites, coloc_text = [], ""
            for key, text in colocs.items():
                names = _members(key)
                if len(names) < 2 or not all(n in surfaces for n in names):
                    continue
                heat, origin, binsz = _sites.dense_field(loader, names, roi_vox)
                if heat is None:
                    continue
                found = build_coloc_sites(
                    heat, soup, origin, binsz,
                    COLOC_MIN_VALUE, COLOC_MIN_BINS, MAX_COLOC_SITES,
                    COLOC_MERGE_BINS, COLOC_MIN_SEPARATION)
                if found:
                    coloc_sites.extend(found)
                    coloc_text = coloc_text or text
                print(f"[labels] coloc '{key}': {len(found)} site(s)")

            # 3. interaction sites: near both, overlapping neither
            for key, text in inters.items():
                sides = [s for s in key.split("/") if s.strip()]
                if len(sides) != 2:
                    continue
                a_names, b_names = _members(sides[0]), _members(sides[1])
                if not all(n in surfaces for n in a_names + b_names):
                    continue
                occ_a, origin, binsz = _sites.dense_field(loader, a_names, roi_vox)
                occ_b, _, _ = _sites.dense_field(loader, b_names, roi_vox)
                coloc, _, _ = _sites.dense_field(
                    loader, sorted(set(a_names + b_names)), roi_vox)
                if occ_a is None or occ_b is None:
                    continue
                if coloc is None:
                    coloc = np.zeros_like(occ_a)
                comps = build_interaction_components(
                    occ_a > INTERACTION_MIN_COUNT,
                    occ_b > INTERACTION_MIN_COUNT,
                    coloc, soup, origin, binsz,
                    INTERACTION_RADIUS_BINS, INTERACTION_MERGE_BINS,
                    INTERACTION_MIN_BINS, INTERACTION_MIN_SEPARATION,
                    MAX_INTERACTION_SITES)
                print(f"[labels] interaction '{key}': {len(comps)} site(s)")
                if not comps:
                    continue
                # Black on white, in caps — the inverse of every other label.
                # Single-marker labels take their channel's colour, so one on a
                # white channel used to be indistinguishable from a contact
                # site. Inverting the plate makes the two different kinds of
                # thing at a glance rather than different shades.
                ch = Channel(name=key,
                             display_text=(text.upper()
                                           if INTERACTION_LABEL_UPPERCASE
                                           else text),
                             polydata=_empty_polydata(),
                             label_color=INTERACTION_LABEL_COLOR,
                             background_color=INTERACTION_LABEL_BACKGROUND,
                             background_opacity=(
                                 INTERACTION_LABEL_BACKGROUND_OPACITY),
                             font_size=INTERACTION_FONT_SIZE,
                             priority_weight=INTERACTION_PRIORITY,
                             max_label_distance_px=INTERACTION_LEASH_PX,
                             orient_to_data=True)
                ch.components = comps
                channels.append(ch)

            self._result_queue.put({
                "channels": channels,
                "coloc_sites": coloc_sites,
                "coloc_text": coloc_text,
                "soup": soup,
                "ms": (time.perf_counter() - t0) * 1000.0,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._fail(f"Label placement failed: {e}")

    def _fail(self, reason: str):
        """Give up with a reason the UI can show.

        The app runs with stdout redirected to devnull unless --logs is
        passed, so a print here reaches nobody. `last_error` is drained by the
        caller and put in the chat panel instead.
        """
        print(f"[labels] {reason}")
        self.last_error = reason
        self._result_queue.put(None)

    def check_and_apply_setup(self) -> bool:
        """Main thread: build the actors. True when a new set just landed."""
        try:
            result = self._result_queue.get_nowait()
        except queue.Empty:
            return False
        self.clear()
        if not result:
            return False

        # A fresh build places NOW, whatever the camera was doing while it ran.
        # `_gesturing` makes update() a no-op, and a gesture starting during
        # the (multi-second) LLM round trip would otherwise leave the labels
        # built but never placed — indistinguishable from labelling failing.
        self._gesturing = False

        before = self._prop_ids()
        self._channels = result["channels"]
        if self._channels:
            self._layout = FlagpoleLayout(
                self.renderer, self.render_window, self._channels)
        if result["coloc_sites"]:
            self._conformer = ColocConformer(
                self.renderer, self.render_window, result["coloc_sites"],
                result["soup"], text=result["coloc_text"] or "colocalization",
                wrap_aspect=COLOC_WRAP_ASPECT,
                overlay_renderer=self.overlay_renderer)
            # A per-cell label inside a colocalization patch is redundant with
            # the text already lying on that surface.
            try:
                self._conformer.suppress_overlapping_components(self._channels)
            except Exception as e:
                print(f"[labels] suppression skipped: {e}")

        self._owned_props = [p for p in self._props() if id(p) not in before]
        self._preprocessed = True
        if not self._channels and not result["coloc_sites"]:
            self.last_error = (
                "Nothing to label: no components or colocalization sites were "
                "found for these markers in this view.")
        n_comp = sum(len(c.components) for c in self._channels)
        print(f"[labels] ready in {result['ms']:.0f} ms: {len(self._channels)} "
              f"channel(s), {n_comp} component(s), "
              f"{len(result['coloc_sites'])} conforming site(s)")
        self.update(force=True)

        # A one-line account of what was actually placed, for the UI to show.
        # Everything upstream of drawing can succeed while nothing reaches the
        # screen, and with stdout at devnull there is otherwise no way to tell
        # the two apart from inside the app.
        try:
            size = self.render_window.GetSize() if self.render_window else (0, 0)
        except Exception:
            size = (0, 0)
        # `shown` vs `components` is the decisive split: it separates "nothing
        # was built" from "labels were built and then culled off-screen by the
        # solver", which look identical from the outside.
        self.last_report = {
            "channels": len(self._channels),
            "components": n_comp,
            "shown": getattr(self._layout, "shown_count", 0),
            "hidden": getattr(self._layout, "hidden_count", 0),
            "coloc_sites": len(result["coloc_sites"]),
            "coloc_shown": getattr(self._conformer, "shown_count", 0),
            "props_added": len(self._owned_props),
            "window": tuple(size),
            "ms": round(result["ms"]),
        }
        return True

    # ── per-frame ──────────────────────────────────────────

    def update(self, force: bool = False) -> bool:
        """Re-place for the current camera. Call on interaction settle."""
        if not self._preprocessed or not self._visible or self._gesturing:
            return False
        placed = False
        if self._conformer is not None:
            # Conformed patches are world-space geometry, so they only need a
            # refit when the camera actually moved; the guard inside handles it.
            self._conformer.update(force=force)
        if self._layout is not None:
            if self._conformer is not None:
                # Projected every pass, not cached as screen rects — stale
                # rectangles are exactly when the two label kinds collide.
                self._layout.set_obstacle_points(self._conformer.obstacle_points())
            self._layout.update(force=force)
            placed = True
        return placed

    def set_channel_color(self, name: str, rgb) -> bool:
        """Recolour one marker's labels to follow its channel colour.

        Only single-marker labels track a channel colour. A coloc or
        interaction key names two markers, so there is no single colour to
        follow, and interaction labels are deliberately black-on-white to mark
        them as a different KIND of label — recolouring those would undo the
        one thing that distinguishes them.
        """
        if self._layout is None or not name or "/" in name or "+" in name:
            return False
        swap = self._layout.set_channel_color(name, rgb)
        if swap is None:
            return False
        old_actor, new_actor = swap
        # `clear()` removes exactly the props recorded here, so the retired
        # actor has to come off the list and its replacement go on — otherwise
        # clearing would leave the new label layer on screen forever.
        self._owned_props = [p for p in self._owned_props if p is not old_actor]
        self._owned_props.append(new_actor)
        return True

    def resolve_if_resized(self) -> bool:
        """Re-solve if the render window changed size since the last solve.

        Flat callouts are placed in DISPLAY PIXELS, so a solve is only valid
        for the window size it was computed against. The window is not stable:
        `VtkRemoteView` is configured with `interactive_ratio=0.4`, and
        trame-vtk implements that by calling `SetSize(original * 0.4)` on the
        render window while the user interacts, restoring it on the still
        render.

        The settle fires on a 0.18 s timer after EndInteractionEvent, which can
        easily beat the client's still-render round trip — so the solve lands
        while the window is still at 40%, and then the window grows back with
        nothing re-running the layout. Measured: every label ends up inside the
        lower-left 40% of the viewport (median 0.19W, 0.18H), and the shown
        count halves (100 -> 47), because label rectangles are a fixed pixel
        size and are therefore 2.5x larger relative to a 40% viewport, so the
        collision pass drops far more of them. Both of those are what a user
        sees as "the labels went to the bottom-left and got sparse".

        A genuine browser resize has exactly the same effect, and this covers
        that too. Driven by the 10 Hz poll loop, so a stale layout survives at
        most one tick.
        """
        if self._layout is None or self.render_window is None:
            return False
        if not self._preprocessed or not self._visible or self._gesturing:
            return False
        try:
            size = tuple(self.render_window.GetSize())
        except Exception:
            return False
        if size == self._layout.solved_size or min(size) < 80:
            return False
        return self.update(force=True)

    def begin_gesture(self):
        """Camera is moving: stop paying for labels until it settles.

        Flat callouts are screen-space, so they are wrong the instant the
        camera moves and are hidden outright. Conformed patches are world-
        space geometry — they stay correct under any camera at zero CPU, so
        they stay up. Nothing is recomputed until the gesture ends.
        """
        if self._gesturing:
            return
        self._gesturing = True
        if self._layout is not None:
            try:
                self._layout.set_visible(False)
            except Exception:
                pass

    def end_gesture(self, resolve: bool = True):
        """Camera settled: bring the flat labels back.

        `resolve=False` restores them without re-solving, for the case where
        the camera ended up where it started — their positions are still
        correct, so the solve would be wasted work.
        """
        self._gesturing = False
        if self._layout is not None and self._visible:
            try:
                self._layout.set_visible(True)
            except Exception:
                pass
        if resolve:
            self.update()

    def set_visible(self, visible: bool):
        self._visible = bool(visible)
        if self._layout is not None:
            for ch in self._channels:
                self._layout.set_channel_visible(ch.name, self._visible)
        if self._conformer is not None:
            self._conformer.set_visible(self._visible)

    def _props(self):
        """Every prop in the renderers we draw into, 2D included.

        Both of them: flat callouts go in the main renderer, conforming patches
        in the overlay. Ownership is taken by diffing this around construction,
        so a renderer missing here is a label actor that clear() would never
        remove.
        """
        out = []
        for ren in (self.renderer, self.overlay_renderer):
            if ren is None:
                continue
            coll = ren.GetViewProps()
            coll.InitTraversal()
            for _ in range(coll.GetNumberOfItems()):
                out.append(coll.GetNextProp())
        return [p for p in out if p is not None]

    def _prop_ids(self):
        return {id(p) for p in self._props()}

    def clear(self):
        """Drop every label actor.

        The engine classes add their actors in __init__ and never remove them
        — they were written for an app that built them once — so ownership is
        taken by diffing the renderer's props around construction.
        """
        for prop in self._owned_props:
            # Try both: removing a prop a renderer does not hold is a no-op,
            # and which one owns it depends on the label kind.
            for ren in (self.renderer, self.overlay_renderer):
                if ren is None:
                    continue
                try:
                    ren.RemoveViewProp(prop)
                except Exception:
                    pass
        self._owned_props = []
        self._layout = None
        self._conformer = None
        self._channels = []
        self._preprocessed = False


def _empty_polydata():
    """Interaction channels carry no mesh — the solver reads only components."""
    import vtk
    return vtk.vtkPolyData()
