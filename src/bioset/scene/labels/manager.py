"""
LabelSceneManager — orchestrates all label placement for BioSET.

Usage:
    mgr = LabelSceneManager(renderer)
    mgr.start_preprocessing(polydata_by_name, raw_labels)  # background thread

    # poll loop (every 0.1s):
    if mgr.check_and_apply_setup():   # True once preprocessing finishes
        view.update()

    # EndInteractionEvent observer:
    if mgr.update():
        view.update()

    # When Biomni /label returns a new response:
    mgr.clear()
    mgr.start_preprocessing(polydata_by_name, new_raw_labels)

The raw_labels dict from Biomni /label looks like:
    {
        "MART1": ["melanoma cells", "subtitle"],
        "MART1+CD8": ["co-loc label"],
        "MART1/CD8": ["interaction label"],
        "overall": ["tissue overview"],
    }

Keys are normalized internally:
  - co-loc: sorted("+".split(key))  → "CD8+MART1" == "MART1+CD8"
  - interaction: sorted("/".split(key)) sides, each side sorted  → canonical form
"""

from __future__ import annotations

import queue
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import vtk


# ==========================================================================
# Label lookup helper
# ==========================================================================

def _make_lookup_fn(raw_labels: dict, overall: list):
    """Return a callable(key) -> str | None for region/interaction/coloc lookups."""

    def _norm_coloc(key: str) -> str:
        return "+".join(sorted(key.split("+")))

    def _norm_interaction(key: str) -> str:
        sides = key.split("/")
        return "/".join(sorted(_norm_coloc(s) for s in sides))

    def lookup(key: str):
        if key == "overall":
            return overall[0] if overall else None

        # Exact match first
        if key in raw_labels:
            val = raw_labels[key]
            return val[0] if val else None

        # Normalized co-loc match  (A+B == B+A)
        if "+" in key and "/" not in key:
            norm = _norm_coloc(key)
            for k, v in raw_labels.items():
                if "+" in k and "/" not in k and _norm_coloc(k) == norm:
                    return v[0] if v else None

        # Normalized interaction match  (A/B == B/A, A+B/C == C/A+B etc.)
        if "/" in key:
            norm = _norm_interaction(key)
            for k, v in raw_labels.items():
                if "/" in k and _norm_interaction(k) == norm:
                    return v[0] if v else None

        return None

    return lookup


# ==========================================================================
# LabelSceneManager
# ==========================================================================

class LabelSceneManager:
    """Manages the full label pipeline for all active mesh channels.

    One instance is kept in callbacks._refs["label_manager"] and
    replaced whenever a new Biomni /label response arrives.
    """

    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bioset_labels")

    def __init__(self, renderer):
        self.renderer = renderer

        self._channels = []          # list of channel dicts (marker_name, mesh, …)
        self._single_regions = []
        self._composite_regions = []
        self._hierarchy = None
        self._interactions = []
        self._label_lookup_fn = None
        self._preprocessed = False

        self._current_actors = []
        self._result_queue: queue.Queue = queue.Queue()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_preprocessing(self, polydata_by_name: dict, raw_labels: dict,
                             overall: list | None = None):
        """Begin background preprocessing for the given channels and labels.

        polydata_by_name: {marker_name: vtkPolyData}  (world-space, from MeshManager)
        raw_labels: Biomni /label 'labels' dict
        overall: Biomni /label 'overall' list (used for the overview cluster label)
        """
        self._preprocessed = False
        self._label_lookup_fn = _make_lookup_fn(raw_labels, overall or [])
        self._executor.submit(self._preprocess, dict(polydata_by_name))
        print(f"[label_manager] Preprocessing started for {list(polydata_by_name.keys())}")

    def check_and_apply_setup(self) -> bool:
        """Check if preprocessing completed. Call from the async poll loop.

        Returns True if preprocessing just finished (caller should trigger
        an initial label placement + view update).
        """
        try:
            result = self._result_queue.get_nowait()
        except queue.Empty:
            return False

        self._channels = result["channels"]
        self._single_regions = result["single_regions"]
        self._composite_regions = result["composite_regions"]
        self._hierarchy = result["hierarchy"]
        self._interactions = result["interactions"]
        self._preprocessed = True
        print(f"[label_manager] Setup applied: "
              f"{len(self._single_regions)} single, "
              f"{len(self._composite_regions)} composite regions, "
              f"{len(self._interactions)} interactions")
        return True

    def update(self) -> bool:
        """Recompute and redisplay labels for the current camera position.

        Call on EndInteractionEvent. Returns True if any actors were placed.
        """
        if not self._preprocessed:
            return False

        from .placement import place_labels_hierarchical
        self.clear()
        self._current_actors = place_labels_hierarchical(
            self._hierarchy,
            self._single_regions,
            self._composite_regions,
            self._channels,
            self.renderer,
            self._interactions,
            self._label_lookup_fn,
        )
        return len(self._current_actors) > 0

    def clear(self):
        """Remove all label actors from the renderer."""
        for a in self._current_actors:
            self.renderer.RemoveActor(a)
        self._current_actors = []

    # ------------------------------------------------------------------
    # Background preprocessing
    # ------------------------------------------------------------------

    def _preprocess(self, polydata_by_name: dict):
        """Runs on background thread. Extracts geometry, builds hierarchy."""
        try:
            from .regions import (
                extract_channel_geometry, compute_overlap_geometry, build_regions,
            )
            from .hierarchy import RegionHierarchy
            from .interactions import detect_interactions
            from .placement import preprocess_channel, clear_channel_caches
            from .settings import label_config as config

            clear_channel_caches()  # invalidate stale locators/dilation from previous runs

            # ----------------------------------------------------------
            # Step 1: Extract geometry for each channel
            # ----------------------------------------------------------
            channels = []
            for marker_name, polydata in polydata_by_name.items():
                print(f"[label_manager] Extracting geometry for '{marker_name}'...")
                # Deep copy so we don't modify MeshManager's polydata
                mesh_copy = vtk.vtkPolyData()
                mesh_copy.DeepCopy(polydata)
                mesh, rids, region_info, cell_normals = extract_channel_geometry(mesh_copy)
                channels.append({
                    "marker_name": marker_name,
                    "mesh": mesh,
                    "region_ids": rids,
                    "region_info": region_info,
                    "cell_normals": cell_normals,
                })
                print(f"[label_manager] '{marker_name}': {len(region_info)} regions")

            if not channels:
                print("[label_manager] No channels to preprocess")
                self._result_queue.put({
                    "channels": [], "single_regions": [], "composite_regions": [],
                    "hierarchy": RegionHierarchy([], []),
                    "interactions": [],
                })
                return

            # ----------------------------------------------------------
            # Step 2: Co-localization detection between channel pairs
            # ----------------------------------------------------------
            overlap_data = {}
            if config.get("DETECT_COLOC", True) and len(channels) > 1:
                from itertools import combinations as _combs
                for ch_a, ch_b in _combs(channels, 2):
                    ma, mb = ch_a["marker_name"], ch_b["marker_name"]
                    print(f"[label_manager] Checking co-localization: {ma} ∩ {mb}")
                    overlaps = compute_overlap_geometry(ch_a, ch_b)
                    if overlaps:
                        overlap_data[(ma, mb)] = overlaps

            # ----------------------------------------------------------
            # Step 3: Build Region objects
            # ----------------------------------------------------------
            single_regions, composite_regions = build_regions(
                channels, overlap_data, self._label_lookup_fn
            )

            # ----------------------------------------------------------
            # Step 4: Build hierarchy dendrogram
            # ----------------------------------------------------------
            overall_text = self._label_lookup_fn("overall")
            hierarchy = RegionHierarchy(
                single_regions, composite_regions,
                overall_label_text=overall_text,
            )

            # ----------------------------------------------------------
            # Step 5: Interaction detection
            # ----------------------------------------------------------
            interactions = []
            if config.get("DETECT_INTERACTIONS", True):
                interactions = detect_interactions(
                    single_regions, composite_regions, self._label_lookup_fn
                )

            # ----------------------------------------------------------
            # Step 6: Preprocess dilated meshes for SURFACE labels
            # ----------------------------------------------------------
            for ch in channels:
                print(f"[label_manager] Preprocessing dilated mesh for '{ch['marker_name']}'...")
                try:
                    preprocess_channel(ch)
                except Exception as e:
                    print(f"[label_manager] Dilation failed for '{ch['marker_name']}': {e}")

            self._result_queue.put({
                "channels": channels,
                "single_regions": single_regions,
                "composite_regions": composite_regions,
                "hierarchy": hierarchy,
                "interactions": interactions,
            })
            print(f"[label_manager] Preprocessing complete")

        except Exception as e:
            print(f"[label_manager] Preprocessing failed: {e}")
            traceback.print_exc()
