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
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import sites as _sites
from .flagpole import (
    COLOC_MERGE_BINS, COLOC_MIN_BINS, COLOC_MIN_SEPARATION, COLOC_MIN_VALUE,
    COLOC_WRAP_ASPECT, INTERACTION_LEASH_PX, INTERACTION_MERGE_BINS,
    INTERACTION_MIN_BINS, INTERACTION_MIN_SEPARATION, INTERACTION_PRIORITY,
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

    def __init__(self, renderer, render_window=None):
        self.renderer = renderer
        self.render_window = render_window or (
            renderer.GetRenderWindow() if renderer else None)

        self._layout: Optional[FlagpoleLayout] = None
        self._conformer: Optional[ColocConformer] = None
        self._channels: List[Channel] = []
        self._preprocessed = False
        self._visible = True
        self._gesturing = False
        self._owned_props: List[object] = []
        self._result_queue: queue.Queue = queue.Queue()

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
                print("[labels] no surfaces in the viewport — nothing to label")
                self._result_queue.put(None)
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
                ch = Channel(name=key, display_text=text, polydata=pd,
                             label_color=colors.get(key, (1.0, 1.0, 1.0)),
                             font_size=15)
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
            proxies = []
            for pd in raw_pd:
                pr = build_proxy_surface(pd, COLOC_PROXY_DILATION,
                                         COLOC_PROXY_SMOOTH)
                if COLOC_PROXY_DECIMATE > 0.0:
                    pr = decimate_polydata(pr, COLOC_PROXY_DECIMATE)
                proxies.append(pr)
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
                ch = Channel(name=key, display_text=text,
                             polydata=_empty_polydata(),
                             label_color=(1.0, 1.0, 1.0), font_size=14,
                             priority_weight=INTERACTION_PRIORITY,
                             max_label_distance_px=INTERACTION_LEASH_PX)
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
            print(f"[labels] preprocessing failed: {e}")
            traceback.print_exc()
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

        before = self._prop_ids()
        self._channels = result["channels"]
        if self._channels:
            self._layout = FlagpoleLayout(
                self.renderer, self.render_window, self._channels)
        if result["coloc_sites"]:
            self._conformer = ColocConformer(
                self.renderer, self.render_window, result["coloc_sites"],
                result["soup"], text=result["coloc_text"] or "colocalization",
                wrap_aspect=COLOC_WRAP_ASPECT)
            # A per-cell label inside a colocalization patch is redundant with
            # the text already lying on that surface.
            try:
                self._conformer.suppress_overlapping_components(self._channels)
            except Exception as e:
                print(f"[labels] suppression skipped: {e}")

        self._owned_props = [p for p in self._props() if id(p) not in before]
        self._preprocessed = True
        n_comp = sum(len(c.components) for c in self._channels)
        print(f"[labels] ready in {result['ms']:.0f} ms: {len(self._channels)} "
              f"channel(s), {n_comp} component(s), "
              f"{len(result['coloc_sites'])} conforming site(s)")
        self.update(force=True)
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
        """Every prop currently in the renderer, 2D included."""
        out = []
        if self.renderer is None:
            return out
        for coll in (self.renderer.GetViewProps(),):
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
            try:
                self.renderer.RemoveViewProp(prop)
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
