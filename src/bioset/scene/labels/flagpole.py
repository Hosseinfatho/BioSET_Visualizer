"""Flagpole, conforming and interaction labels — vendored engine.

Ported from the reference application (`trame_flagpole_labels_remote.py`) and
its INTEGRATION.md. The solver is kept intact; what was stripped is everything
that read the reference's own data — CLI parsing, `.glb` tile assembly and
`.npy` heatmap loading — because bioset already has meshes and colocalization
of its own. `bioset/scene/labels/sites.py` feeds those in instead.

Three label kinds, all flowing through one solver:

  * per-marker    boxed 2D callouts with leader lines, one per connected
                  component of a channel's surface
  * colocalization  white text that CONFORMS to the tissue surface, placed
                  where two markers physically overlap
  * interaction   boxed callouts where two markers are close neighbours but do
                  NOT overlap

The extension point is `Channel`: any new label kind is a `Channel` of
`Component`s appended to the list handed to `FlagpoleLayout`, and it inherits
camera-facing anchoring, leader lines, non-overlap against every other kind,
and obstacle avoidance with no solver changes.

Notes carried over from INTEGRATION.md that are load-bearing here:
  * priority is normalised PER CHANNEL then floored, because raw areas are not
    comparable across kinds — a nucleus is thousands of square units while an
    interaction site is scored in heatmap bins, and without this every
    interaction label loses every collision;
  * conforming patches must be excluded from bounds (`SetUseBounds(False)`) or
    hidden labels parked off-screen wreck ResetCamera;
  * fitting targets a dilated, smoothed proxy surface, not the raw mesh —
    text on the raw surface bends ~50% of its own height and is unreadable.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy



# ==========================================================================
# tunables: components + anchoring + screen layout
# ==========================================================================


# ── Component filtering, sized for THIS dataset ────────────────────────────
# Every world-unit constant in this file was tuned for cells 60-100 world
# units across (INTEGRATION.md 6.5). bioset's are ~17x smaller, so the ones
# that scale with cell size are re-derived here rather than inherited.
#
# Measured on a 2x2 mis_v3 viewport, raising the cell floor walks the median
# component from a mesh shard up to a plausible nucleus:
#
#     min cells   kept   median diagonal
#            12     86           3.1 um     shards
#            80     21           9.4 um     nuclei
#           300      8          12.8 um     only the largest
#
# A melanoma nucleus is ~8-15 um across, so 80 is where components start being
# cells. It also cuts the label count to something a screen can carry, which
# is the same lever from the other direction.
# Ranked by area at EXTRACT time, before the camera is known, so this is a
# ceiling on what can EVER be labelled rather than on what is shown. At 24 it
# was the binding constraint: a 4x4 view yields 109 MART1 and 103 SOX10
# components past the quality filter, so ~78% of labelable cells could never
# receive a label however far you zoomed into them.
#
# Per-frame cost is bounded elsewhere — FlagpoleLayout drops anchors outside
# the viewport before solving, and its n^2 overlap pass runs only on what
# survives (0.36 ms at 150, 2.5 ms at 400). Zoomed out this changes nothing
# visible: the extra components project off-screen or lose their collision.
MAX_COMPONENTS_PER_CHANNEL: Optional[int] = 150
MIN_COMPONENT_AREA_FRACTION = 0.001
MIN_COMPONENT_CELLS = 80

# Tile assembly. Tiles count as "already positioned" when column 0 geometry
# lies entirely before column 1 geometry along the column axis (and likewise for
# rows). This much slop is allowed at the shared boundary.
PLACEMENT_ORDER_TOLERANCE_FRACTION = 0.02

# A few well-spread surface samples are enough to choose a camera-facing
# leader-line anchor. This is NOT visibility testing and performs no raycasts.
ANCHOR_CANDIDATES = 20
ANCHOR_SWITCH_FRACTION = 0.06  # hysteresis, fraction of component diagonal

# Screen-space flag layout. Labels stay LOCAL to their projected component.
SCREEN_MARGIN_PX = 14
LABEL_GAP_PX = 5
LABEL_PADDING_PX = 5
OFFSCREEN_ALLOWANCE_FRACTION = 0.15

# Preferred label offset from its component in screen pixels.
BASE_LABEL_OFFSET_PX = 28.0
# A label may move only this far from its projected component while resolving
# collisions. This is the key constraint that prevents viewport-edge rails.
MAX_LABEL_DISTANCE_PX = 110.0
# Relaxation passes. Fully vectorized, so each pass costs microseconds.
COLLISION_ITERATIONS = 12
# Simultaneous (Jacobi) updates overshoot, so damp the separation impulse.
COLLISION_DAMPING = 0.5
# Spring strength pulling labels back toward their preferred near-anchor spot.
ANCHOR_SPRING = 0.22
# Hard overlap slop used in the final validation pass.
OVERLAP_EPS_PX = 0.5

# Text sizes. MART1 is wrapped to two lines to keep its callout compact.

# ==========================================================================
# tunables: layout solver
# ==========================================================================


# Interaction update rate. With the batched solver a full layout runs in well
# under two milliseconds, so this no longer needs to be throttled hard.
MAX_LAYOUT_HZ = 60.0

# Remote rendering remains server-side.
INTERACTIVE_RATIO = 2
INTERACTIVE_QUALITY = 45
STILL_QUALITY = 92

# Mesh level of detail. A decimated copy is built once at load and swapped in
# while the camera moves. Anchors come from the full-resolution mesh, so
# swapping does not affect label placement.
LOD_REDUCTION = 0.9
# Was 150k, which no real channel here reaches, so the LOD never engaged and
# every interactive frame drew full-resolution translucent geometry.
LOD_MIN_CELLS = 20_000
# Translucency costs about three times an opaque pass. Drop it while the camera
# moves, the same bargain the LOD swap already makes.
INTERACTIVE_OPAQUE = True

# Mesh appearance.

# Label/leader appearance.
LABEL_BACKGROUND = (0.025, 0.028, 0.038)
LABEL_BACKGROUND_OPACITY = 0.84

# Typeface for the flat callouts. Either a family name, resolved against the
# repo's assets/fonts and the system font directories, or an absolute path to a
# .ttf/.otf. Anything that does not resolve to a file on disk falls back to
# VTK's built-in Arial, with one line on stdout saying so.
#
# The check matters: vtkTextProperty.SetFontFile does NOT fail on a bad path.
# It silently renders Arial, so a typo would look like the setting being
# ignored rather than like an error. `_resolve_font_file` therefore only hands
# VTK a path it has already stat'd.
#
# This reaches the flat callouts ONLY. The surface-conforming colocalization
# text is built by vtkVectorText, which exposes no font API at all — it has one
# hardcoded stroke font — so changing it there would mean converting TTF
# outlines to polygons ourselves.
# Empty means VTK's built-in Arial, which is what labels are drawn in.
# Merriweather was tried here and read poorly at label sizes — a text face with
# that much contrast turns mushy in a 28 px plate — so the default is back to
# the sans. The lookup below is kept because swapping the face is now a one-line
# change: put a name here, or set BIOSET_LABEL_FONT, and drop the .ttf in
# assets/fonts/ or install it.
LABEL_FONT = os.environ.get("BIOSET_LABEL_FONT", "")

# Labels are drawn bold. With a font FILE, VTK renders the file as it is and
# ignores SetBold, so a bold face has to be picked by filename instead.
_FONT_WEIGHT_ORDER = ("bold", "semibold", "regular", "")
LEADER_WIDTH = 1.5


# ==========================================================================
# tunables: interaction labels
# ==========================================================================

# --- Interaction labels ------------------------------------------------------
# Where the two markers are close neighbors but do not overlap. Detected from
# the single-channel occupancy maps rather than the dilated-overlap map, which
# is far too coarse to resolve which cells are actually adjacent.
# Much larger than SINGLE_MARKER_FONT_SIZE, not a point or two — an interaction
# label names the thing the view is FOR, and there are only ever a handful of
# them against a hundred-odd per-cell labels. Measured on a 4x4 mis_v3 viewport
# at 1600x1000: going 14 -> 24 costs about two single-marker placements out of
# 109, and none of its own, because interaction labels already outrank per-cell
# ones (INTERACTION_PRIORITY) and carry a longer leash to get clear of them.
# Wrapping to two lines was tried and is worse — it costs five more per-cell
# labels for a taller box without making the glyphs any bigger.
SINGLE_MARKER_FONT_SIZE = 15
INTERACTION_FONT_SIZE = 24
# Interaction labels are the visual INVERSE of every other label: black text on
# a white plate, in caps. A single-marker label is drawn in its own channel's
# colour, and a channel that happens to be white produced white-on-dark text
# that read as the same kind of thing as a contact site. Recolouring the text
# alone could never fix that — the two would still differ only by hue, and one
# of the hues in play IS white. Inverting the whole plate makes the difference
# structural instead.
INTERACTION_LABEL_COLOR = (0.0, 0.0, 0.0)
INTERACTION_LABEL_BACKGROUND = (1.0, 1.0, 1.0)
# Higher than the dark plate's 0.84: black text needs the white behind it to be
# near-opaque, or the tissue shows through and the contrast collapses.
INTERACTION_LABEL_BACKGROUND_OPACITY = 0.96
INTERACTION_LABEL_UPPERCASE = True
# Cell-scale proximity. Bins are 2.24 um here (16 voxels), so two bins is a
# ~4.5 um reach — about half a nucleus, which is the scale adjacency means at.
INTERACTION_RADIUS_BINS = 2
INTERACTION_MERGE_BINS = 3
INTERACTION_MIN_BINS = 3
# ~1.4x a nucleus, scaled from the reference's 110 against its ~80-unit cells.
INTERACTION_MIN_SEPARATION = 14.0
MAX_INTERACTION_SITES = 18
# Interaction sites are the point of the view, so they outrank per-cell labels
# when the screen gets tight. Raised well clear of the single-marker band: with
# per-cell priority now capped below 1.0 and this at 4.0, no per-cell label can
# ever outrank a contact site however large its component is.
INTERACTION_PRIORITY = 4.0
INTERACTION_LEASH_PX = 260.0
# A component whose second principal extent is more than this fraction of its
# first is treated as round, and its label left horizontal. Orienting a label
# to a direction that is barely there makes it jitter as the camera moves.
AXIS_ROUNDNESS_LIMIT = 0.75
# Oriented labels are SCALED into this much tilt, not clipped at it. Clipping
# piled a majority of them onto the limit exactly (measured: 4 distinct angles
# across 10 labels at one camera), which reads as mechanical rather than as
# following anything. Scaling keeps every label distinct and still bounded:
# past this much tilt the text is hard to read and fights the horizontal
# layout around it.
MAX_LABEL_TILT_DEG = 40.0

# ── How many single-marker labels the screen earns ─────────────────────────
# A per-cell label is only worth drawing when its component is big enough on
# screen to be worth naming. Gating on PROJECTED SIZE rather than a fixed count
# is what makes the density follow the zoom on its own: far out, only the
# largest components clear the bar; as you zoom in, more and more do, with no
# schedule to tune and nothing that has to know the pyramid depth.
#
# Measured on a 4x4 mis_v3 viewport at 1600x1000, the projected size of the
# MART1 components actually in view:
#
#     zoom   in view   p50   p90   max   >=40px  >=60px
#        1       109    19    43    74       17       1
#        2        92    39    85   147       44      30
#        4        22    76   175   206       22      16
#
# 55 px sits just under the largest handful at full zoom-out and is comfortably
# exceeded by most components two steps in, which is the behaviour asked for:
# only the biggest get named when the whole field is in view, and more earn a
# label as you come in. Below this the label is wider than the thing it names.
SINGLE_MARKER_MIN_SCREEN_PX = 55.0
# Hard ceiling on per-cell labels actually drawn, whatever the zoom. The size
# gate alone can still admit a hundred at very high zoom on dense tissue; this
# keeps the screen readable, and it keeps the strongest components by taking
# them in priority (area) order.
SINGLE_MARKER_MAX_SHOWN = 14
# Per-cell labels are a background layer, not the point of the view: their
# priority band sits entirely below INTERACTION_PRIORITY so a contact site or a
# colocalization always wins a contested spot.
SINGLE_MARKER_PRIORITY = 1.0

HIDDEN_PX = -10000.0

AXIS_INDEX = {"x": 0, "y": 1, "z": 2}

# ==========================================================================
# tunables: colocalization labels
# ==========================================================================

# Sites come from connected regions of the r=0 heatmap. Each region is treated
# as a prism extruded through the full depth of the data, and the label anchors
# wherever a camera ray first meets mesh inside that prism.
COLOC_FONT_SIZE = 14
# Plain white, unlit, no background panel and no frame. The text is polygonal
# glyph geometry lying on the tissue, so a panel would defeat the point.
COLOC_LABEL_COLOR = (1.0, 1.0, 1.0)
COLOC_MIN_VALUE = 2          # heatmap value a bin needs to join a region
COLOC_MIN_BINS = 2           # drop single-bin specks
# Backstop on site count. At 12 this was the ONLY thing limiting how many
# conforming labels appeared: measured on mis_v3, a 4x4 viewport yields 15
# sites and a 6x6 yields 20, and every one of them passes the value, bin-count
# and separation filters — so a third to a half were being discarded by this
# number alone.
#
# The per-site refit cost turned out to be much smaller than the fixed
# overhead, which is what made 12 look necessary. Measured refit on rotation
# (the only camera move that triggers one), at 1600x1000:
#
#     4x4:  cap 12 -> 60 ms,  cap 24 -> 61 ms   (only 15 sites exist)
#     6x6:  cap 12 -> 45 ms,  cap 20 -> 68 ms,  cap 24 -> 69 ms
#
# So 24 shows everything available anywhere inside the zoom gate for ~24 ms on
# the settle at the widest view. This is still the lever if rotation drags.
MAX_COLOC_SITES = 24
# Heatmap regions within this many bins of each other are treated as one site,
# so a dense cluster earns a single label instead of a stack of them.
COLOC_MERGE_BINS = 3
# And no two labels are anchored closer than this in world units — ~1.4x a
# nucleus, scaled from the reference's 110 against its ~80-unit cells.
COLOC_MIN_SEPARATION = 14.0

# Scaffold resolution for the conforming patch. Sampling at this density is
# itself a low-pass on the surface, and it turns out to be THE limiter on how
# closely the text follows: measured on mis_v3, 13x5 -> 21x9 took the bend
# from 0.40 to 0.58 um while the bend cap and the smoothing passes changed
# nothing. Costs one ray per vertex per refit, 2.6 -> 7.2 ms for 4 sites.
# Raised from 21x9: measured on a 2x2 mis_v3 viewport, the bend (RMS departure
# of the deformed glyph vertices from their own best-fit plane) goes
# 0.70 -> 0.92 um, and lowering the proxy smoothing below adds a little more.
# 33x13 was also tried and does not beat this while costing half again as much
# per refit, so this is the knee.
CONFORM_SCAFFOLD_U = 27
CONFORM_SCAFFOLD_V = 11
# Laplacian passes over the scaffold, then how much of the remaining bend to
# keep. Zero passes is maximum conformance; the reference needed 1 because its
# cells were ~17x larger relative to the label, so a label-sized window spanned
# far more surface. Here the scaffold's own sampling is enough of a low-pass.
CONFORM_SMOOTH_PASSES = 0
CONFORM_ALPHA = 1.0
# Hard ceiling on bend, as a fraction of label height. NOT currently binding:
# alpha = min(1.0, frac * height / rms), and the measured rms is well under
# height, so this only engages on a genuinely violent surface. Raising it does
# nothing on its own — the scaffold above is the lever.
COLOC_MAX_BEND_FRACTION = 0.35

# Rays landing in the gaps between cells are filled from neighbors. Below this
# hit rate the patch is flattened further rather than trusted.
COLOC_MIN_HIT_FRACTION = 0.35
# Lift toward the camera, as a fraction of label height, so text clears the
# surface it was fitted to. Lower than the reference because the proxy is
# barely dilated now, so there is less to clear — and less float reads as more
# firmly printed on the tissue.
COLOC_LIFT_FRACTION = 0.15
# Local geometry is gathered this far beyond the prism. Fitting still uses only
# the flagged bins, but clearance needs the neighbors that can occlude the text.
# Must cover the label footprint, so it scales with label height, not the cell.
COLOC_SOUP_MARGIN = 14.0
# Labels are fitted to a dilated, smoothed copy of the meshes rather than the
# raw surface. Smoothing is what makes full conformance legible, and the
# dilation lifts the text clear of the geometry it describes.
# Barely dilated, ~0.035x a nucleus. At the inherited 8.0 the proxy was
# inflated by more than a whole cell, erasing the surface it approximates;
# pulling it in this far keeps the real shape for the text to follow, and
# measurably raises the bend (0.58 -> 0.65 um at a 21x9 scaffold).
COLOC_PROXY_DILATION = 0.25
# Lowered from 30. Those passes flatten the proxy the text is fitted to, which
# is the surface detail we want the label to pick up; at the denser scaffold
# above, dropping them measurably raises the bend (0.83 -> 0.94 um). Not taken
# to zero: some smoothing is what keeps the glyphs from going jagged.
COLOC_PROXY_SMOOTH = 10
# The proxy is only ever sampled at scaffold resolution, so fitting against a
# tenth of its triangles costs nothing in quality and roughly quarters the
# refit. Measured: bend is unchanged, refit drops from 163 ms to 35 ms.
COLOC_PROXY_DECIMATE = 0.9

# Glyph cap height in WORLD units. Fixed size makes the label read as a decal
# printed on the tissue: it grows and shrinks with the data, like the surface it
# sits on. Set COLOC_TARGET_PX_HEIGHT above zero to lock it to a screen size
# instead, which stays legible at any zoom but dwarfs the cells when zoomed out.
# Breathing room in pixels between a colocation label and any flat callout.
COLOC_OBSTACLE_PAD_PX = 6.0
# Bigger than a nucleus (~1.1x). The reference used ~0.35x against its own
# cells, but its labels sat on cells 17x larger, where a third of a cell is
# still plenty of text. Scaling that ratio down here produced text that was
# reliably too small to read, so this is set from legibility rather than from
# the reference's proportion. Raising it also raises the ABSOLUTE bend, since
# the bend cap is a fraction of label height.
COLOC_LABEL_HEIGHT = 8.0
# How much the label shrinks in world units as you zoom in. 0 keeps a fixed
# world size, so the text grows on screen exactly like the tissue does and soon
# swamps it. 1 keeps a fixed screen size, which stops it reading as something
# printed on the surface. In between, a 4x zoom enlarges the text on screen by
# only 4^(1-k), so it stays attached without taking over the view.
COLOC_ZOOM_COMPENSATION = 0.75
# Wrap a label once it is wider than this many times its line height.
COLOC_WRAP_ASPECT = 3.2
COLOC_LINE_SPACING = 1.35
COLOC_TARGET_PX_HEIGHT = 0.0
# A refit is skipped outright when the camera barely moved: 0.03 ms
# instead of 33. Covers resizes, clicks that never dragged, and the tail
# of a spin.
REFIT_MIN_CAMERA_CHANGE_DEG = 0.4
# Bounds on the zoom-compensated height, scaled with everything else: half a
# nucleus at the low end, ~1.4 nuclei at the high end.
# The floor matters as much as the nominal size: zoom compensation shrinks
# the label as you close in, and it was hitting a floor too small to read.
COLOC_MIN_LABEL_HEIGHT = 5.0
COLOC_MAX_LABEL_HEIGHT = 28.0



# ==========================================================================
# geometry helpers
# ==========================================================================

def bounds_diagonal(bounds: Sequence[float]) -> float:
    return float(
        math.sqrt(
            (bounds[1] - bounds[0]) ** 2
            + (bounds[3] - bounds[2]) ** 2
            + (bounds[5] - bounds[4]) ** 2
        )
    )


def _principal_axis_endpoints(components) -> np.ndarray:
    """Two world points spanning each component's long axis, shaped (N, 2, 3).

    The axis is the first principal direction of the component's surface
    samples; the endpoints are its extremes among those samples. An interaction
    site is a contact between two populations, so this direction is the
    interface itself — which is what a label describing it should run along
    rather than sitting horizontal regardless of the geometry.

    Degenerate components (too few samples, or genuinely round) fall back to
    two coincident points, which the caller reads as "no preferred direction".
    """
    out = np.zeros((len(components), 2, 3), dtype=np.float64)
    for i, c in enumerate(components):
        pts = np.asarray(getattr(c, "candidate_points", None), dtype=np.float64)
        if pts is None or pts.ndim != 2 or len(pts) < 3:
            out[i, 0] = out[i, 1] = np.asarray(c.center, dtype=np.float64)
            continue
        centre = pts.mean(axis=0)
        rel = pts - centre
        try:
            _, sv, vt = np.linalg.svd(rel, full_matrices=False)
        except np.linalg.LinAlgError:
            out[i, 0] = out[i, 1] = centre
            continue
        # Round enough that any axis would be arbitrary: leave it unoriented
        # rather than let numerical noise pick a direction that then flickers.
        if len(sv) < 2 or sv[0] <= 1e-9 or (sv[1] / sv[0]) > AXIS_ROUNDNESS_LIMIT:
            out[i, 0] = out[i, 1] = centre
            continue
        t = rel @ vt[0]
        out[i, 0] = centre + vt[0] * float(t.min())
        out[i, 1] = centre + vt[0] * float(t.max())
    return out


def normalize(v: np.ndarray, fallback=(0.0, 0.0, 1.0)) -> np.ndarray:
    a = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(a))
    if n < 1e-12:
        return np.asarray(fallback, dtype=np.float64)
    return a / n


def triangulate(polydata: vtk.vtkPolyData) -> vtk.vtkPolyData:
    tri = vtk.vtkTriangleFilter()
    tri.SetInputData(polydata)
    tri.Update()
    out = vtk.vtkPolyData()
    out.DeepCopy(tri.GetOutput())
    return out


def weld(polydata: vtk.vtkPolyData, tolerance: float = 0.0) -> vtk.vtkPolyData:
    """Merge coincident points. This is what closes tile seams.

    Tolerance is in world units. Zero merges only exactly coincident vertices,
    which is enough when the tiles were meshed on a shared voxel lattice.
    """
    clean = vtk.vtkCleanPolyData()
    clean.SetInputData(polydata)
    clean.PointMergingOn()
    clean.ConvertLinesToPointsOff()
    clean.ConvertPolysToLinesOff()
    clean.ConvertStripsToPolysOff()
    if tolerance > 0.0:
        clean.ToleranceIsAbsoluteOn()
        clean.SetAbsoluteTolerance(float(tolerance))
    else:
        clean.SetTolerance(0.0)
    clean.Update()

    out = vtk.vtkPolyData()
    out.DeepCopy(clean.GetOutput())
    return out


_GLB_CACHE: Dict[str, vtk.vtkPolyData] = {}


def decimate_polydata(polydata: vtk.vtkPolyData, reduction: float) -> vtk.vtkPolyData:
    """Build a reduced-triangle copy for use during camera interaction."""
    dec = vtk.vtkQuadricDecimation()
    dec.SetInputData(polydata)
    dec.SetTargetReduction(float(reduction))
    dec.VolumePreservationOn()
    dec.Update()

    out = vtk.vtkPolyData()
    out.DeepCopy(dec.GetOutput())
    return out


def build_proxy_surface(
    polydata: vtk.vtkPolyData, dilation: float, smooth_iters: int
) -> vtk.vtkPolyData:
    """A dilated, smoothed stand-in used only for fitting labels.

    Text that follows the raw surface is unreadable: measured on this data, the
    frontmost surface bends a label by about half its own height over a
    label-sized window. Pushing the surface outward along its normals and
    relaxing it removes the crevices between touching cells, so the label can
    follow the proxy exactly and still read cleanly. The dilation also lifts the
    text clear of the geometry it labels.
    """
    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(polydata)
    normals.ComputePointNormalsOn()
    normals.ComputeCellNormalsOff()
    normals.ConsistencyOn()
    normals.AutoOrientNormalsOn()
    normals.SplittingOff()
    normals.Update()

    oriented = normals.GetOutput()
    oriented.GetPointData().SetActiveVectors("Normals")

    warp = vtk.vtkWarpVector()
    warp.SetInputData(oriented)
    warp.SetScaleFactor(float(dilation))
    warp.Update()

    smooth = vtk.vtkSmoothPolyDataFilter()
    smooth.SetInputConnection(warp.GetOutputPort())
    smooth.SetNumberOfIterations(int(smooth_iters))
    smooth.SetRelaxationFactor(0.1)
    smooth.FeatureEdgeSmoothingOff()
    smooth.BoundarySmoothingOn()
    smooth.Update()

    out = vtk.vtkPolyData()
    out.DeepCopy(smooth.GetOutput())
    out.ComputeBounds()
    return out


def count_regions(polydata: vtk.vtkPolyData) -> int:
    conn = vtk.vtkPolyDataConnectivityFilter()
    conn.SetInputData(polydata)
    conn.SetExtractionModeToAllRegions()
    conn.Update()
    return int(conn.GetNumberOfExtractedRegions())


def farthest_sample_indices(points: np.ndarray, count: int, pool_limit: int = 2048) -> np.ndarray:
    """Cheap farthest-point sampling performed once at startup."""
    n = len(points)
    if n <= count:
        return np.arange(n, dtype=np.int64)

    if n > pool_limit:
        pool_ids = np.linspace(0, n - 1, pool_limit, dtype=np.int64)
    else:
        pool_ids = np.arange(n, dtype=np.int64)

    pool = points[pool_ids]
    center = pool.mean(axis=0)
    first = int(np.argmax(np.sum((pool - center) ** 2, axis=1)))
    selected = [first]
    min_d2 = np.sum((pool - pool[first]) ** 2, axis=1)

    for _ in range(1, min(count, len(pool))):
        idx = int(np.argmax(min_d2))
        selected.append(idx)
        d2 = np.sum((pool - pool[idx]) ** 2, axis=1)
        min_d2 = np.minimum(min_d2, d2)

    return pool_ids[np.asarray(selected, dtype=np.int64)]



# ==========================================================================
# components
# ==========================================================================

# Components
# -----------------------------------------------------------------------------


@dataclass
class Component:
    channel_name: str
    component_id: int
    area: float
    diagonal: float
    center: np.ndarray
    candidate_points: np.ndarray
    label_index: int = -1
    anchor_index: int = 0
    suppressed: bool = False   # a colocation label covers this cell


@dataclass
class Channel:
    name: str
    display_text: str
    polydata: vtk.vtkPolyData
    label_color: Tuple[float, float, float]
    font_size: int
    mesh: "MeshLOD" = None
    # How strongly this channel's labels resist being dropped or displaced,
    # relative to the others. Applied after per-channel normalization.
    priority_weight: float = 1.0
    # How far a label may travel from its anchor while dodging. Interaction
    # sites sit right beside colocalizations, so they need a longer leash to
    # get clear of those large blocks.
    max_label_distance_px: float = MAX_LABEL_DISTANCE_PX
    # Plate behind the text. None means the shared dark plate; interaction
    # labels override it to white so they read as a different KIND of label
    # rather than another colour of the same one.
    background_color: Optional[Tuple[float, float, float]] = None
    background_opacity: Optional[float] = None
    # Smallest projected component size, in pixels, that earns a label. 0
    # disables the gate. Per-cell channels set it so their density follows the
    # zoom; interaction and colocalization labels are never gated this way —
    # they name a site, not a blob, and there are only ever a handful.
    min_screen_px: float = 0.0
    # Ceiling on how many of this channel's labels may be drawn at once. None
    # for no limit.
    max_shown: Optional[int] = None
    # Screen-space rotation for this channel's labels, in radians, one per
    # component. None keeps them horizontal.
    orient_to_data: bool = False
    components: List[Component] = field(default_factory=list)


def _triangle_array(polydata: vtk.vtkPolyData) -> np.ndarray:
    """Nx3 vertex indices. Input is already triangulated."""
    polys = polydata.GetPolys()
    if polys.GetNumberOfCells() == 0:
        return np.zeros((0, 3), dtype=np.int64)

    conn = vtk_to_numpy(polys.GetConnectivityArray()).astype(np.int64, copy=False)
    offsets = vtk_to_numpy(polys.GetOffsetsArray()).astype(np.int64, copy=False)
    sizes = np.diff(offsets)
    if len(sizes) and not np.all(sizes == 3):
        raise ValueError("Expected a purely triangular mesh after triangulation")
    return conn.reshape(-1, 3)


def extract_components(
    channel_name: str,
    polydata: vtk.vtkPolyData,
    max_components: Optional[int] = MAX_COMPONENTS_PER_CHANNEL,
) -> List[Component]:
    """One connectivity pass, then all per-region work in NumPy.

    The previous implementation ran a connectivity filter and a mass-properties
    filter per region, which scales as regions times mesh size. With four tiles
    per channel that dominated startup. This version colors regions once and
    computes areas, centroids, and samples with array operations.
    """
    t0 = time.perf_counter()

    conn = vtk.vtkPolyDataConnectivityFilter()
    conn.SetInputData(polydata)
    conn.SetExtractionModeToAllRegions()
    conn.ColorRegionsOn()
    conn.Update()

    colored = conn.GetOutput()
    n_regions = int(conn.GetNumberOfExtractedRegions())
    if n_regions == 0:
        return []

    region_array = colored.GetPointData().GetArray("RegionId")
    if region_array is None:
        raise RuntimeError("Connectivity filter did not produce RegionId")

    point_regions = vtk_to_numpy(region_array).astype(np.int64, copy=False)
    points = vtk_to_numpy(colored.GetPoints().GetData()).astype(np.float64, copy=False)

    # The RegionId point array is not always the same length as the point
    # array. On a welded multi-tile surface it comes back LONGER, and since
    # region membership is later grouped by argsort over RegionId and those
    # indices are used to index `points`, the extra entries index past the end.
    # Truncating both to the common length is the only interpretation that is
    # certainly right: an id with no point is meaningless either way.
    if len(point_regions) != len(points):
        common = min(len(point_regions), len(points))
        print(f"[{channel_name}] RegionId/points length mismatch "
              f"({len(point_regions)} vs {len(points)}) — using {common}")
        point_regions = point_regions[:common]
        points = points[:common]

    tris = _triangle_array(colored)

    # Region per TRIANGLE, from cell data when the filter provides it.
    #
    # The obvious route — point_regions[tris[:, 0]] — assumes the RegionId
    # point array is exactly as long as the point array, and it is not always:
    # on a welded multi-tile surface it can come back one or more entries
    # short, and the lookup then raises IndexError. The cell array asks the
    # question directly and needs no indirection. Fall back to the point route
    # with a bounds guard on filters that only populate point data.
    cell_region_array = colored.GetCellData().GetArray("RegionId")
    if cell_region_array is not None:
        tri_regions = vtk_to_numpy(cell_region_array).astype(np.int64, copy=False)
        if len(tri_regions) != len(tris):
            tri_regions = tri_regions[:len(tris)]
            tris = tris[:len(tri_regions)]
    else:
        usable = tris[:, 0] < len(point_regions)
        if not usable.all():
            tris = tris[usable]
        tri_regions = point_regions[tris[:, 0]]
    if len(tris) == 0:
        return []

    a = points[tris[:, 0]]
    b = points[tris[:, 1]]
    c = points[tris[:, 2]]
    tri_areas = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)

    areas = np.bincount(tri_regions, weights=tri_areas, minlength=n_regions)
    cell_counts = np.bincount(tri_regions, minlength=n_regions)

    total_area = float(areas.sum())
    min_area = total_area * MIN_COMPONENT_AREA_FRACTION

    keep = (cell_counts >= MIN_COMPONENT_CELLS) & (areas >= min_area)
    candidates = np.nonzero(keep)[0]
    if len(candidates) == 0:
        print(f"[{channel_name}] no components passed the size filters")
        return []

    order = candidates[np.argsort(-areas[candidates])]
    if max_components is not None:
        order = order[:max_components]

    # Group point indices by region with one sort instead of a mask per region.
    sort_idx = np.argsort(point_regions, kind="stable")
    sorted_regions = point_regions[sort_idx]
    all_regions = np.arange(n_regions)
    region_starts = np.searchsorted(sorted_regions, all_regions, side="left")
    region_ends = np.searchsorted(sorted_regions, all_regions, side="right")

    components: List[Component] = []
    for label_index, region_id in enumerate(order):
        idx = sort_idx[region_starts[region_id] : region_ends[region_id]]
        if len(idx) == 0:
            continue
        pts = points[idx]

        center = pts.mean(axis=0)
        lo = pts.min(axis=0)
        hi = pts.max(axis=0)
        diagonal = float(np.linalg.norm(hi - lo))

        sample_ids = farthest_sample_indices(pts, ANCHOR_CANDIDATES)
        cand = np.asarray(pts[sample_ids], dtype=np.float64).copy()
        start_idx = int(np.argmin(np.sum((cand - center) ** 2, axis=1)))

        components.append(
            Component(
                channel_name=channel_name,
                component_id=int(region_id),
                area=float(areas[region_id]),
                diagonal=max(diagonal, 1e-9),
                center=center.copy(),
                candidate_points=cand,
                label_index=label_index,
                anchor_index=start_idx,
            )
        )

    print(
        f"[{channel_name}] {len(components)} labelable components kept from "
        f"{n_regions} regions in {time.perf_counter() - t0:.1f}s"
    )
    return components



# ==========================================================================
# label layers + leaders
# ==========================================================================

# Pre-rasterized label layers
# -----------------------------------------------------------------------------


def font_search_dirs() -> List[Path]:
    """Where a named font may live, nearest first."""
    dirs = [Path(__file__).resolve().parents[4] / "assets" / "fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:                                   # per-user installs on Windows
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    win = os.environ.get("SystemRoot") or "C:\\Windows"
    dirs.append(Path(win) / "Fonts")
    dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
             Path.home() / ".fonts", Path("/Library/Fonts"),
             Path.home() / "Library" / "Fonts"]
    return dirs


@lru_cache(maxsize=8)
def _resolve_font_file(name: str) -> Optional[str]:
    """Absolute path to `name`'s font file, or None if it is not installed.

    Returns only a path that exists. VTK treats a missing font file as "use
    Arial" without complaining, so handing it an unchecked path turns a missing
    font into a silently ignored setting.
    """
    if not name:
        return None
    direct = Path(name).expanduser()
    if direct.suffix.lower() in (".ttf", ".otf") and direct.is_file():
        return str(direct)

    key = name.replace(" ", "").replace("-", "").lower()
    best: Optional[Tuple[int, str]] = None
    for d in font_search_dirs():
        try:
            if not d.is_dir():
                continue
            entries = list(d.iterdir())
        except OSError:
            continue
        for f in entries:
            if f.suffix.lower() not in (".ttf", ".otf"):
                continue
            stem = f.stem.replace(" ", "").replace("-", "").replace("_", "").lower()
            if not stem.startswith(key):
                continue
            # Prefer a bold face, and never an italic one.
            if "italic" in stem or "oblique" in stem:
                continue
            rest = stem[len(key):]
            rank = next((i for i, w in enumerate(_FONT_WEIGHT_ORDER)
                         if w and w in rest), len(_FONT_WEIGHT_ORDER) - 1)
            if best is None or rank < best[0]:
                best = (rank, str(f))
    return best[1] if best else None


_font_warned = set()


def _apply_font(tprop: vtk.vtkTextProperty, name: str):
    """Point the text property at `name`, or leave VTK's default in place."""
    path = _resolve_font_file(name)
    if path:
        tprop.SetFontFamily(vtk.VTK_FONT_FILE)
        tprop.SetFontFile(path)
    if name and name not in _font_warned:
        _font_warned.add(name)
        print(f"[labels] font '{name}': "
              + (f"using {path}" if path else
                 "not found on this machine — falling back to Arial. Install "
                 "it, or drop the .ttf in assets/fonts/, or set "
                 "BIOSET_LABEL_FONT."))


def _make_text_property(channel: Channel) -> vtk.vtkTextProperty:
    tprop = vtk.vtkTextProperty()
    tprop.SetFontSize(channel.font_size)
    tprop.SetBold(True)
    _apply_font(tprop, LABEL_FONT)
    tprop.SetColor(*channel.label_color)
    tprop.SetJustificationToCentered()
    tprop.SetVerticalJustificationToCentered()
    bg = channel.background_color or LABEL_BACKGROUND
    opacity = (LABEL_BACKGROUND_OPACITY if channel.background_opacity is None
               else channel.background_opacity)
    tprop.SetBackgroundColor(*bg)
    tprop.SetBackgroundOpacity(opacity)
    tprop.FrameOn()
    tprop.SetFrameColor(*channel.label_color)
    tprop.SetFrameWidth(1)
    return tprop


def _measure_text(tprop: vtk.vtkTextProperty, text: str, dpi: int, font_size: int):
    """Fallback text metrics used if the renderer cannot measure the string."""
    text_renderer = vtk.vtkTextRenderer.GetInstance()
    if text_renderer is not None:
        bbox = [0, 0, 0, 0]
        try:
            if text_renderer.GetBoundingBox(tprop, text, bbox, max(int(dpi), 72)):
                return float(bbox[1] - bbox[0] + 1), float(bbox[3] - bbox[2] + 1)
        except Exception:
            pass

    lines = text.splitlines() or [text]
    w = float(max(len(x) for x in lines) * font_size * 0.65)
    h = float(len(lines) * font_size * 1.25)
    return w, h


class TexturedLabelLayer:
    """One texture and one quad mesh for an entire channel.

    The channel's string is rasterized exactly once. Every label for that
    channel is a textured quad pointing at the same image, so moving labels
    costs four NumPy writes each and never re-enters the text pipeline.
    """

    def __init__(self, renderer: vtk.vtkRenderer, channel: Channel, dpi: int):
        self.channel = channel
        self.count = len(channel.components)
        self.text = channel.display_text
        self.kind = "textured quads"

        tprop = _make_text_property(channel)
        self.text_property = tprop

        image = vtk.vtkImageData()
        dims = [0, 0]
        text_renderer = vtk.vtkTextRenderer.GetInstance()
        if text_renderer is None:
            raise RuntimeError("no vtkTextRenderer instance available")

        ok = text_renderer.RenderString(tprop, self.text, image, dims, max(int(dpi), 72))
        if not ok:
            raise RuntimeError("vtkTextRenderer.RenderString failed")

        tex_w, tex_h, _ = image.GetDimensions()
        if tex_w <= 0 or tex_h <= 0:
            raise RuntimeError("empty label texture")

        # RenderString may pad the image, typically to a power of two. The text
        # occupies the lower-left corner, so the texture coordinates cover only
        # the reported dimensions.
        if dims[0] <= 0 or dims[1] <= 0:
            dims = [tex_w, tex_h]
        umax = float(dims[0]) / float(tex_w)
        vmax = float(dims[1]) / float(tex_h)

        self._half_w = 0.5 * float(dims[0])
        self._half_h = 0.5 * float(dims[1])
        # Collision extents include padding so rectangles keep breathing room.
        self.width = float(dims[0] + 2 * LABEL_PADDING_PX)
        self.height = float(dims[1] + 2 * LABEL_PADDING_PX)

        self.points = vtk.vtkPoints()
        self.points.SetDataTypeToDouble()
        self.points.SetNumberOfPoints(max(4 * self.count, 1))

        tcoords = vtk.vtkFloatArray()
        tcoords.SetName("tcoords")
        tcoords.SetNumberOfComponents(2)
        tcoords.SetNumberOfTuples(max(4 * self.count, 1))

        quads = vtk.vtkCellArray()
        for i in range(self.count):
            base = 4 * i
            quad = vtk.vtkQuad()
            for k in range(4):
                quad.GetPointIds().SetId(k, base + k)
            quads.InsertNextCell(quad)
            tcoords.SetTuple2(base + 0, 0.0, 0.0)
            tcoords.SetTuple2(base + 1, umax, 0.0)
            tcoords.SetTuple2(base + 2, umax, vmax)
            tcoords.SetTuple2(base + 3, 0.0, vmax)

        self.polydata = vtk.vtkPolyData()
        self.polydata.SetPoints(self.points)
        self.polydata.SetPolys(quads)
        self.polydata.GetPointData().SetTCoords(tcoords)

        mapper = vtk.vtkPolyDataMapper2D()
        mapper.SetInputData(self.polydata)
        mapper.ScalarVisibilityOff()

        self.texture = vtk.vtkTexture()
        self.texture.SetInputData(image)
        self.texture.InterpolateOff()
        self.texture.RepeatOff()
        self.texture.EdgeClampOn()

        self.actor = vtk.vtkTexturedActor2D()
        self.actor.SetMapper(mapper)
        self.actor.SetTexture(self.texture)
        # The color already lives in the texture. Keep the property neutral so
        # it does not tint the rasterized pixels.
        self.actor.GetProperty().SetColor(1.0, 1.0, 1.0)
        self.actor.GetProperty().SetOpacity(1.0)
        renderer.AddViewProp(self.actor)

        self._np = vtk_to_numpy(self.points.GetData())
        self.hide_all()

    def set_position(self, index: int, x: float, y: float):
        base = 4 * index
        left = x - self._half_w
        right = x + self._half_w
        bottom = y - self._half_h
        top = y + self._half_h
        self._np[base + 0] = (left, bottom, 0.0)
        self._np[base + 1] = (right, bottom, 0.0)
        self._np[base + 2] = (right, top, 0.0)
        self._np[base + 3] = (left, top, 0.0)

    def set_positions(self, indices: np.ndarray, xs: np.ndarray, ys: np.ndarray,
                      angles: Optional[np.ndarray] = None):
        """Vectorized bulk placement for a whole channel.

        `angles` rotates each quad about its own centre, in radians, so a label
        can follow the direction of the thing it names instead of sitting
        stubbornly horizontal. None keeps every quad axis-aligned, which is the
        cheaper path and what per-cell labels use.
        """
        if len(indices) == 0:
            return
        base = 4 * np.asarray(indices, dtype=np.int64)
        if angles is None:
            left = xs - self._half_w
            right = xs + self._half_w
            bottom = ys - self._half_h
            top = ys + self._half_h
            self._np[base + 0, 0] = left
            self._np[base + 0, 1] = bottom
            self._np[base + 1, 0] = right
            self._np[base + 1, 1] = bottom
            self._np[base + 2, 0] = right
            self._np[base + 2, 1] = top
            self._np[base + 3, 0] = left
            self._np[base + 3, 1] = top
        else:
            # Corner offsets in the label's own frame, in the same order the
            # texture coordinates were built with, then rotated per label.
            ox = np.array([-1.0, 1.0, 1.0, -1.0]) * self._half_w
            oy = np.array([-1.0, -1.0, 1.0, 1.0]) * self._half_h
            c = np.cos(angles)[:, None]
            s = np.sin(angles)[:, None]
            rx = ox[None, :] * c - oy[None, :] * s
            ry = ox[None, :] * s + oy[None, :] * c
            for k in range(4):
                self._np[base + k, 0] = xs + rx[:, k]
                self._np[base + k, 1] = ys + ry[:, k]
        self._np[np.concatenate([base, base + 1, base + 2, base + 3]), 2] = 0.0

    def hide(self, index: int):
        self.set_position(index, HIDDEN_PX, HIDDEN_PX)

    def hide_all(self):
        self._np[:] = HIDDEN_PX

    def modified(self):
        self.points.GetData().Modified()
        self.points.Modified()
        self.polydata.Modified()


class MapperLabelLayer:
    """Fallback path using VTK's label mappers.

    Only used if the text renderer cannot produce a texture. This is the
    original approach and is noticeably slower, because the mapper rebuilds its
    label set whenever the point set is marked modified.
    """

    def __init__(self, renderer: vtk.vtkRenderer, channel: Channel, dpi: int):
        self.channel = channel
        self.count = len(channel.components)
        self.text = channel.display_text

        self.text_property = _make_text_property(channel)
        w, h = _measure_text(self.text_property, self.text, dpi, channel.font_size)
        self.width = float(w + 2 * LABEL_PADDING_PX)
        self.height = float(h + 2 * LABEL_PADDING_PX)

        self.points = vtk.vtkPoints()
        self.points.SetDataTypeToDouble()
        self.points.SetNumberOfPoints(max(self.count, 1))

        self.polydata = vtk.vtkPolyData()
        self.polydata.SetPoints(self.points)

        labels = vtk.vtkStringArray()
        labels.SetName("labels")
        labels.SetNumberOfValues(max(self.count, 1))
        for i in range(self.count):
            labels.SetValue(i, self.text)
        self.polydata.GetPointData().AddArray(labels)

        if hasattr(vtk, "vtkOpenGLBatchedLabeledDataMapper"):
            self.mapper = vtk.vtkOpenGLBatchedLabeledDataMapper()
            self.kind = "batched label mapper"
        elif hasattr(vtk, "vtkBatchedLabeledDataMapper"):
            self.mapper = vtk.vtkBatchedLabeledDataMapper()
            self.kind = "batched label mapper"
        else:
            self.mapper = vtk.vtkLabeledDataMapper()
            self.kind = "vtkLabeledDataMapper"

        self.mapper.SetInputData(self.polydata)
        self.mapper.SetLabelModeToLabelFieldData()
        self.mapper.SetFieldDataName("labels")
        self.mapper.CoordinateSystemDisplay()
        self.mapper.SetLabelTextProperty(self.text_property)

        if hasattr(self.mapper, "SetTextAnchor"):
            try:
                self.mapper.SetTextAnchor(8)  # Center
            except Exception:
                pass

        self.actor = vtk.vtkActor2D()
        self.actor.SetMapper(self.mapper)
        renderer.AddViewProp(self.actor)

        self._np = vtk_to_numpy(self.points.GetData())
        self.hide_all()

    def set_position(self, index: int, x: float, y: float):
        self._np[index] = (x, y, 0.0)

    def set_positions(self, indices: np.ndarray, xs: np.ndarray, ys: np.ndarray,
                      angles=None):
        # `angles` accepted and ignored: this path draws through VTK's label
        # mappers, which place upright text and offer no rotation. Taking the
        # argument keeps it interchangeable with the textured path.
        if len(indices) == 0:
            return
        idx = np.asarray(indices, dtype=np.int64)
        self._np[idx, 0] = xs
        self._np[idx, 1] = ys
        self._np[idx, 2] = 0.0

    def hide(self, index: int):
        self.set_position(index, HIDDEN_PX, HIDDEN_PX)

    def hide_all(self):
        self._np[:] = HIDDEN_PX

    def modified(self):
        self.points.GetData().Modified()
        self.points.Modified()
        self.polydata.Modified()


def make_label_layer(renderer: vtk.vtkRenderer, channel: Channel, dpi: int):
    try:
        return TexturedLabelLayer(renderer, channel, dpi)
    except Exception as exc:  # pragma: no cover
        print(f"[labels] texture path unavailable ({exc}), falling back")
        return MapperLabelLayer(renderer, channel, dpi)


class LeaderLayer:
    """All flagpoles for one channel in one vtkActor2D."""

    def __init__(self, renderer: vtk.vtkRenderer, count: int, color):
        self.count = count
        self.points = vtk.vtkPoints()
        self.points.SetDataTypeToDouble()
        self.points.SetNumberOfPoints(max(2 * count, 1))

        lines = vtk.vtkCellArray()
        for i in range(count):
            a = 2 * i
            line = vtk.vtkLine()
            line.GetPointIds().SetId(0, a)
            line.GetPointIds().SetId(1, a + 1)
            lines.InsertNextCell(line)

        self.polydata = vtk.vtkPolyData()
        self.polydata.SetPoints(self.points)
        self.polydata.SetLines(lines)

        mapper = vtk.vtkPolyDataMapper2D()
        mapper.SetInputData(self.polydata)
        mapper.ScalarVisibilityOff()

        self.actor = vtk.vtkActor2D()
        self.actor.SetMapper(mapper)
        self.actor.GetProperty().SetColor(*color)
        self.actor.GetProperty().SetOpacity(0.88)
        self.actor.GetProperty().SetLineWidth(LEADER_WIDTH)
        renderer.AddViewProp(self.actor)

        self._np = vtk_to_numpy(self.points.GetData())
        self.hide_all()

    def set_segment(self, index: int, a: Tuple[float, float], b: Tuple[float, float]):
        j = 2 * index
        self._np[j] = (a[0], a[1], 0.0)
        self._np[j + 1] = (b[0], b[1], 0.0)

    def set_segments(
        self,
        indices: np.ndarray,
        ax: np.ndarray,
        ay: np.ndarray,
        bx: np.ndarray,
        by: np.ndarray,
    ):
        if len(indices) == 0:
            return
        base = 2 * np.asarray(indices, dtype=np.int64)
        self._np[base, 0] = ax
        self._np[base, 1] = ay
        self._np[base, 2] = 0.0
        self._np[base + 1, 0] = bx
        self._np[base + 1, 1] = by
        self._np[base + 1, 2] = 0.0

    def hide(self, index: int):
        self.set_segment(index, (HIDDEN_PX, HIDDEN_PX), (HIDDEN_PX, HIDDEN_PX))

    def hide_all(self):
        self._np[:] = HIDDEN_PX

    def modified(self):
        self.points.GetData().Modified()
        self.points.Modified()
        self.polydata.Modified()



# ==========================================================================
# anchor cache
# ==========================================================================

# Batched anchor selection
# -----------------------------------------------------------------------------


class AnchorCache:
    """Camera-facing anchor choice for every component of a channel at once.

    Score each precomputed surface sample by its offset along the direction to
    the camera, and switch only when the best sample beats the current one by a
    fraction of the component diagonal.
    """

    def __init__(self, components: List[Component]):
        self.n = len(components)
        if self.n == 0:
            self.k = 0
            return

        self.k = max(len(c.candidate_points) for c in components)
        self.points = np.zeros((self.n, self.k, 3), dtype=np.float64)
        self.valid = np.zeros((self.n, self.k), dtype=bool)
        self.centers = np.zeros((self.n, 3), dtype=np.float64)
        self.thresholds = np.zeros(self.n, dtype=np.float64)
        self.index = np.zeros(self.n, dtype=np.int64)
        self.rows = np.arange(self.n, dtype=np.int64)

        for i, comp in enumerate(components):
            m = len(comp.candidate_points)
            self.points[i, :m] = comp.candidate_points
            # Pad by repeating the last sample so argmax never lands on garbage.
            if m < self.k:
                self.points[i, m:] = comp.candidate_points[-1]
            self.valid[i, :m] = True
            self.centers[i] = comp.center
            self.thresholds[i] = ANCHOR_SWITCH_FRACTION * comp.diagonal
            self.index[i] = comp.anchor_index

    def choose(self, camera_pos: np.ndarray) -> np.ndarray:
        if self.n == 0:
            return np.zeros((0, 3), dtype=np.float64)

        to_camera = camera_pos[None, :] - self.centers
        norms = np.linalg.norm(to_camera, axis=1, keepdims=True)
        to_camera = to_camera / np.maximum(norms, 1e-12)

        offsets = self.points - self.centers[:, None, :]
        scores = np.einsum("nkc,nc->nk", offsets, to_camera)
        scores = np.where(self.valid, scores, -np.inf)

        best = np.argmax(scores, axis=1)
        best_score = scores[self.rows, best]
        current_score = scores[self.rows, self.index]
        switch = (best_score - current_score) > self.thresholds
        self.index = np.where(switch, best, self.index)

        return self.points[self.rows, self.index]



# ==========================================================================
# flagpole layout solver
# ==========================================================================

# Local screen-space flag layout
# -----------------------------------------------------------------------------


class FlagpoleLayout:
    """Keep callouts close to their data while resolving label collisions.

    The solver works entirely in display pixels and entirely in NumPy:
      1. project every component anchor in one matrix multiply,
      2. place each label a small radial offset away from the anchor centroid,
      3. relax overlapping rectangles simultaneously,
      4. pull labels back toward their preferred local positions,
      5. enforce a hard maximum distance from each component,
      6. hide only low-priority labels that still cannot be separated.

    There are no viewport rails and no raycasts.
    """

    def __init__(
        self,
        renderer: vtk.vtkRenderer,
        render_window: vtk.vtkRenderWindow,
        channels: List[Channel],
    ):
        self.renderer = renderer
        self.render_window = render_window
        self.channels = channels
        dpi = int(render_window.GetDPI()) if render_window.GetDPI() > 0 else 72

        self.label_layers: Dict[str, object] = {}
        self.leader_layers: Dict[str, LeaderLayer] = {}
        self.anchor_caches: Dict[str, AnchorCache] = {}

        for ch in channels:
            self.label_layers[ch.name] = make_label_layer(renderer, ch, dpi)
            self.leader_layers[ch.name] = LeaderLayer(
                renderer, len(ch.components), (1.0, 1.0, 1.0)
            )
            self.anchor_caches[ch.name] = AnchorCache(ch.components)

        # Per-channel static arrays. Built once, reused every frame.
        self._channel_priority: Dict[str, np.ndarray] = {}
        self._channel_diag: Dict[str, np.ndarray] = {}
        self._channel_axis: Dict[str, Optional[np.ndarray]] = {}
        self._channel_min_px: Dict[str, np.ndarray] = {}
        self._channel_indices: Dict[str, np.ndarray] = {}
        self._channel_fallback_angle: Dict[str, np.ndarray] = {}
        self._channel_leash: Dict[str, np.ndarray] = {}
        for ch in channels:
            # Priority is normalized WITHIN each channel before the channel
            # weight is applied. Raw areas are not comparable across kinds: a
            # nucleus measures thousands of square units while an interaction
            # site is scored in heatmap bins, so unnormalized areas silently
            # deleted every interaction label in a collision.
            areas = np.array([c.area for c in ch.components], dtype=np.float64)
            if len(areas):
                areas = areas / max(float(areas.max()), 1e-9)
            # Map into [0.6, 1.0] before weighting, so a channel's weight sets a
            # FLOOR rather than only a ceiling. Without the floor the smallest
            # interaction site still ranked below every large nucleus and lost
            # every collision, which left one interaction label on screen.
            self._channel_priority[ch.name] = (
                0.6 + 0.4 * areas
            ) * float(ch.priority_weight)
            self._channel_indices[ch.name] = np.array(
                [c.label_index for c in ch.components], dtype=np.int64
            )
            self._channel_leash[ch.name] = np.full(
                len(ch.components), float(ch.max_label_distance_px), dtype=np.float64
            )
            # World diagonal per component, so the solver can work out how big
            # each one is ON SCREEN for the size gate.
            self._channel_diag[ch.name] = np.array(
                [float(getattr(c, "diagonal", 0.0)) for c in ch.components],
                dtype=np.float64,
            )
            self._channel_min_px[ch.name] = np.full(
                len(ch.components), float(ch.min_screen_px), dtype=np.float64
            )
            # Two world points spanning each component's long axis. Projected
            # every pass, they give the direction the component runs in ON
            # SCREEN, which is what an oriented label follows. Two points
            # rather than a per-frame PCA: the axis is a property of the
            # geometry, only its projection changes with the camera.
            self._channel_axis[ch.name] = (
                _principal_axis_endpoints(ch.components)
                if ch.orient_to_data else None
            )
            self._channel_fallback_angle[ch.name] = np.array(
                [
                    (c.component_id * 2.399963229728653) % (2.0 * math.pi)
                    for c in ch.components
                ],
                dtype=np.float64,
            )

        self._channel_active: Dict[str, np.ndarray] = {}
        self.refresh_active()

        # Colocation patches occupy screen space that flat labels must avoid.
        # Their corners are kept in WORLD space and projected every layout pass,
        # so the exclusion zones track the camera instead of going stale between
        # refits, which is when the two label kinds used to collide.
        self.obstacles = np.zeros((0, 4))  # cx, cy, w, h in display pixels
        self._obstacle_pts = None

        self.last_update_time = 0.0
        self.last_layout_ms = 0.0
        self.shown_count = 0
        self.hidden_count = 0
        # Window size the current label positions belong to. None until solved.
        self.solved_size: Optional[Tuple[int, int]] = None

    def set_channel_visible(self, name: str, visible: bool):
        for store in (self.label_layers, self.leader_layers):
            layer = store.get(name)
            if layer is not None:
                layer.actor.SetVisibility(bool(visible))

    def set_channel_color(self, name: str, color) -> Optional[Tuple]:
        """Redraw one channel's labels in a new colour.

        The colour is baked into a rasterized texture at build time — one
        image per channel, reused by every label — so it cannot be tweaked in
        place. The string has to be re-rendered, which means a new layer and a
        new actor.

        Returns (old_actor, new_actor) so the caller can keep whatever prop
        bookkeeping it does in step; None when the channel is unknown or the
        colour is already what was asked for.
        """
        ch = next((c for c in self.channels if c.name == name), None)
        layer = self.label_layers.get(name)
        if ch is None or layer is None:
            return None
        color = tuple(float(v) for v in color)
        if tuple(float(v) for v in ch.label_color) == color:
            return None

        old_actor = layer.actor
        visible = bool(old_actor.GetVisibility())
        ch.label_color = color
        dpi = (int(self.render_window.GetDPI())
               if self.render_window.GetDPI() > 0 else 72)
        new_layer = make_label_layer(self.renderer, ch, dpi)
        new_layer.actor.SetVisibility(visible)
        self.renderer.RemoveViewProp(old_actor)
        self.label_layers[name] = new_layer
        # Positions live in the layer that was just thrown away, so the labels
        # are nowhere until the next solve. Force one rather than leaving the
        # channel blank until the camera happens to move.
        self.update(force=True)
        return old_actor, new_layer.actor

    def refresh_active(self):
        """Rebuild the per-component active mask after suppression changes."""
        self._channel_active = {
            ch.name: np.array(
                [not c.suppressed for c in ch.components], dtype=bool
            )
            for ch in self.channels
        }

    def set_obstacle_points(self, corners):
        """World-space patch corners, shaped (N, 4, 3)."""
        if corners is None or len(corners) == 0:
            self._obstacle_pts = None
        else:
            self._obstacle_pts = np.asarray(corners, dtype=np.float64).reshape(-1, 3)

    def _project_obstacles(self, width: int, height: int):
        if self._obstacle_pts is None:
            self.obstacles = np.zeros((0, 4))
            return
        ox, oy, _z, ok = self._project(self._obstacle_pts, width, height)
        ox = ox.reshape(-1, 4)
        oy = oy.reshape(-1, 4)
        ok = ok.reshape(-1, 4).all(axis=1)
        if not ok.any():
            self.obstacles = np.zeros((0, 4))
            return
        ox, oy = ox[ok], oy[ok]
        lo_x, hi_x = ox.min(axis=1), ox.max(axis=1)
        lo_y, hi_y = oy.min(axis=1), oy.max(axis=1)
        pad = COLOC_OBSTACLE_PAD_PX
        self.obstacles = np.column_stack([
            0.5 * (lo_x + hi_x), 0.5 * (lo_y + hi_y),
            hi_x - lo_x + 2 * pad, hi_y - lo_y + 2 * pad,
        ])

    def _leader_blocked(self, ax, ay, ex, ey):
        """True where a leader line would run across a colocation label.

        An obstacle that already contains the anchor is exempt. Interaction
        sites sit beside colocalizations, so their anchor is usually under that
        block; a leader emerging from beneath it is correct, and testing it
        would silently delete nearly every interaction label.
        """
        if len(self.obstacles) == 0:
            return np.zeros(len(ax), dtype=bool)
        ox, oy, ow, oh = self.obstacles.T
        holds_anchor = (
            (np.abs(ax[:, None] - ox[None, :]) < 0.5 * ow[None, :])
            & (np.abs(ay[:, None] - oy[None, :]) < 0.5 * oh[None, :])
        )
        blocked = np.zeros(len(ax), dtype=bool)
        for t in (0.2, 0.4, 0.6, 0.8, 1.0):
            px = ax + (ex - ax) * t
            py = ay + (ey - ay) * t
            inside = (
                (np.abs(px[:, None] - ox[None, :]) < 0.5 * ow[None, :])
                & (np.abs(py[:, None] - oy[None, :]) < 0.5 * oh[None, :])
            )
            blocked |= (inside & ~holds_anchor).any(axis=1)
        return blocked

    def set_obstacles(self, rects: np.ndarray):
        self.obstacles = (
            np.zeros((0, 4)) if rects is None or len(rects) == 0
            else np.asarray(rects, dtype=np.float64)
        )

    def _blocked_by_obstacle(self, x, y, w, h):
        """True for any label rectangle overlapping a colocation patch."""
        if len(self.obstacles) == 0:
            return np.zeros(len(x), dtype=bool)
        ox, oy, ow, oh = self.obstacles.T
        gap_x = np.abs(x[:, None] - ox[None, :]) - 0.5 * (w[:, None] + ow[None, :])
        gap_y = np.abs(y[:, None] - oy[None, :]) - 0.5 * (h[:, None] + oh[None, :])
        return ((gap_x < -OVERLAP_EPS_PX) & (gap_y < -OVERLAP_EPS_PX)).any(axis=1)

    # -- projection ---------------------------------------------------------

    def _projection_matrix(self) -> np.ndarray:
        camera = self.renderer.GetActiveCamera()
        m = camera.GetCompositeProjectionTransformMatrix(
            self.renderer.GetTiledAspectRatio(), -1.0, 1.0
        )
        return np.array(
            [[m.GetElement(i, j) for j in range(4)] for i in range(4)], dtype=np.float64
        )

    def _project(self, world_points: np.ndarray, width: int, height: int):
        """World coordinates to display pixels, all points in one multiply."""
        if len(world_points) == 0:
            empty = np.zeros(0, dtype=np.float64)
            return empty, empty, empty, np.zeros(0, dtype=bool)

        mat = self._projection_matrix()
        homogeneous = np.empty((len(world_points), 4), dtype=np.float64)
        homogeneous[:, :3] = world_points
        homogeneous[:, 3] = 1.0
        clip = homogeneous @ mat.T

        w = clip[:, 3]
        in_front = w > 1e-9
        ndc = clip[:, :3] / np.where(in_front, w, 1.0)[:, None]

        vp = self.renderer.GetViewport()
        vx0, vy0 = vp[0] * width, vp[1] * height
        vw, vh = (vp[2] - vp[0]) * width, (vp[3] - vp[1]) * height

        x = vx0 + (ndc[:, 0] * 0.5 + 0.5) * vw
        y = vy0 + (ndc[:, 1] * 0.5 + 0.5) * vh
        return x, y, ndc[:, 2], in_front

    # -- solver -------------------------------------------------------------

    @staticmethod
    def _initial_positions(ax, ay, w, h, fallback_angle):
        """Push each label away from the projected anchor centroid."""
        cx = float(np.mean(ax))
        cy = float(np.mean(ay))
        vx = ax - cx
        vy = ay - cy
        n = np.hypot(vx, vy)

        degenerate = n < 1e-5
        if degenerate.any():
            vx = np.where(degenerate, np.cos(fallback_angle), vx)
            vy = np.where(degenerate, np.sin(fallback_angle), vy)
            n = np.where(degenerate, 1.0, n)

        vx = vx / n
        vy = vy / n

        clearance = BASE_LABEL_OFFSET_PX + 0.45 * (np.abs(vx) * w + np.abs(vy) * h)
        return ax + vx * clearance, ay + vy * clearance

    @staticmethod
    def _constrain(x, y, ax, ay, w, h, width, height, leash=None):
        """Clamp to the anchor radius, then to the viewport, then again."""
        if leash is None:
            leash = MAX_LABEL_DISTANCE_PX
        for _ in range(2):
            rx = x - ax
            ry = y - ay
            dist = np.hypot(rx, ry)
            scale = np.where(
                dist > leash,
                leash / np.maximum(dist, 1e-6),
                1.0,
            )
            x = ax + rx * scale
            y = ay + ry * scale
            x = np.clip(x, SCREEN_MARGIN_PX + 0.5 * w, width - SCREEN_MARGIN_PX - 0.5 * w)
            y = np.clip(y, SCREEN_MARGIN_PX + 0.5 * h, height - SCREEN_MARGIN_PX - 0.5 * h)
        return x, y

    def _push_from_obstacles(self, x, y, w, h, damping=None):
        """Shove labels out of the colocation patches instead of deleting them.

        An interaction site sits right next to a colocalization by definition,
        so its anchor usually lands inside that label's block. Deleting on
        contact removed almost all of them; pushing lets them settle alongside.
        Obstacles are fixed, so the label absorbs the whole displacement.
        """
        if len(self.obstacles) == 0:
            return x, y
        ox, oy, ow, oh = self.obstacles.T
        dx = x[:, None] - ox[None, :]
        dy = y[:, None] - oy[None, :]
        over_x = 0.5 * (w[:, None] + ow[None, :]) - np.abs(dx)
        over_y = 0.5 * (h[:, None] + oh[None, :]) - np.abs(dy)
        hit = (over_x > 0.0) & (over_y > 0.0)
        if not hit.any():
            return x, y
        use_x = over_x < over_y
        sx = np.where(dx >= 0.0, 1.0, -1.0)
        sy = np.where(dy >= 0.0, 1.0, -1.0)
        px = np.where(hit & use_x, sx * (over_x + LABEL_GAP_PX), 0.0).sum(axis=1)
        py = np.where(hit & ~use_x, sy * (over_y + LABEL_GAP_PX), 0.0).sum(axis=1)
        k = COLLISION_DAMPING if damping is None else damping
        return x + k * px, y + k * py

    def _relax(self, x, y, ax, ay, w, h, desired_x, desired_y, priority,
               width, height, leash=None):
        n = len(x)
        if n < 2:
            x, y = self._push_from_obstacles(x, y, w, h)
            return self._constrain(x, y, ax, ay, w, h, width, height, leash)

        max_priority = float(priority.max()) or 1.0
        weight = 0.35 + 0.65 * (priority / max_priority)
        # Each label's share of a pair's separation. High priority moves less.
        share = weight[None, :] / (weight[:, None] + weight[None, :])
        half_w = 0.5 * (w[:, None] + w[None, :])
        half_h = 0.5 * (h[:, None] + h[None, :])
        eye = np.eye(n, dtype=bool)

        for _ in range(COLLISION_ITERATIONS):
            dx = x[:, None] - x[None, :]
            dy = y[:, None] - y[None, :]
            overlap_x = half_w - np.abs(dx)
            overlap_y = half_h - np.abs(dy)

            colliding = (overlap_x > 0.0) & (overlap_y > 0.0)
            colliding[eye] = False
            if not colliding.any() and len(self.obstacles) == 0:
                break

            # Separate along the axis needing the smaller movement.
            use_x = overlap_x < overlap_y
            sign_x = np.where(dx >= 0.0, 1.0, -1.0)
            sign_y = np.where(dy >= 0.0, 1.0, -1.0)

            push_x = np.where(
                colliding & use_x, sign_x * (overlap_x + LABEL_GAP_PX) * share, 0.0
            )
            push_y = np.where(
                colliding & ~use_x, sign_y * (overlap_y + LABEL_GAP_PX) * share, 0.0
            )

            x = x + COLLISION_DAMPING * push_x.sum(axis=1)
            y = y + COLLISION_DAMPING * push_y.sum(axis=1)

            # Weak spring keeps labels near their components instead of drifting.
            x, y = self._push_from_obstacles(x, y, w, h)
            x = x + ANCHOR_SPRING * (desired_x - x)
            y = y + ANCHOR_SPRING * (desired_y - y)
            x, y = self._constrain(x, y, ax, ay, w, h, width, height, leash)

        # The spring drags labels back into an obstacle as fast as the push
        # ejects them, so they can end the loop still inside one. Finish with a
        # few spring-free passes that move each label fully clear.
        for _ in range(4):
            moved_x, moved_y = self._push_from_obstacles(x, y, w, h, damping=1.0)
            if np.allclose(moved_x, x) and np.allclose(moved_y, y):
                break
            x, y = self._constrain(
                moved_x, moved_y, ax, ay, w, h, width, height, leash
            )

        return x, y

    def _screen_angles(self, ch, selector, names, width, height):
        """Screen-space tilt for this channel's labels, or None to stay level.

        The component's long axis is projected and its screen direction taken
        directly, so the label follows the geometry as the camera turns. Folded
        into (-90, 90] so text never reads upside down, then clamped: a label
        past MAX_LABEL_TILT_DEG is hard to read and fights the horizontal
        layout around it.
        """
        axis = self._channel_axis.get(ch.name)
        if axis is None or not len(axis):
            return None
        # `selector` indexes the surviving labels; map back to this channel's
        # own component order to pick the right axis rows.
        own = np.nonzero(names == ch.name)[0]
        rank = np.searchsorted(own, np.nonzero(selector)[0])
        ends = axis[rank]
        p0x, p0y, _z0, ok0 = self._project(ends[:, 0, :], width, height)
        p1x, p1y, _z1, ok1 = self._project(ends[:, 1, :], width, height)
        dx, dy = p1x - p0x, p1y - p0y
        length = np.hypot(dx, dy)
        ang = np.arctan2(dy, dx)
        # Fold into (-90, 90]: a label at 170 deg is the same line as one at
        # -10 deg, but only one of them is readable.
        ang = (ang + 0.5 * math.pi) % math.pi - 0.5 * math.pi
        # Scale the full +/-90 range into +/-MAX rather than clipping, so
        # labels stay distinguishable instead of stacking on the limit.
        ang = ang * (MAX_LABEL_TILT_DEG / 90.0)
        # Too short to have a direction on screen, or an endpoint behind the
        # camera: leave those level rather than orienting them to noise.
        ang[(length < 1.0) | ~(ok0 & ok1)] = 0.0
        return ang

    def _world_per_pixel(self, depth: np.ndarray, height: int) -> np.ndarray:
        """World units spanned by one pixel, per component, at its own depth.

        Under perspective this grows with distance, which is precisely why the
        size gate follows the zoom: the same component covers more pixels as
        the camera comes in. Under parallel projection it is constant.
        """
        cam = self.renderer.GetActiveCamera()
        h = max(int(height), 1)
        if cam.GetParallelProjection():
            return np.full(len(depth), 2.0 * cam.GetParallelScale() / h)
        half = math.radians(0.5 * float(cam.GetViewAngle()))
        return 2.0 * np.maximum(depth, 1e-6) * math.tan(half) / h

    @staticmethod
    def _apply_channel_caps(keep, names, caps, screen_px):
        """Trim each channel to its own ceiling, keeping the biggest on screen.

        Runs after collision resolution so the cap counts labels that would
        actually be DRAWN, not candidates — capping earlier would spend the
        allowance on labels the solver then discards anyway.

        Ranked by projected size rather than by the solver's priority so the
        survivors are the components a viewer would pick out themselves.
        """
        if len(keep) == 0:
            return keep
        out = keep.copy()
        for name in np.unique(names):
            sel = np.nonzero((names == name) & out)[0]
            if len(sel) == 0:
                continue
            cap = int(caps[sel[0]])
            if cap < 0 or len(sel) <= cap:
                continue
            order = sel[np.argsort(-screen_px[sel])]
            out[order[cap:]] = False
        return out

    @staticmethod
    def _select_visible(x, y, w, h, priority):
        """Greedy acceptance by priority using one precomputed overlap matrix."""
        n = len(x)
        keep = np.zeros(n, dtype=bool)
        if n == 0:
            return keep

        half_w = 0.5 * (w[:, None] + w[None, :])
        half_h = 0.5 * (h[:, None] + h[None, :])
        overlap = (half_w - np.abs(x[:, None] - x[None, :]) > OVERLAP_EPS_PX) & (
            half_h - np.abs(y[:, None] - y[None, :]) > OVERLAP_EPS_PX
        )
        np.fill_diagonal(overlap, False)

        for i in np.argsort(-priority):
            if not overlap[i, keep].any():
                keep[i] = True
        return keep

    @staticmethod
    def _leader_endpoints(x, y, w, h, ax, ay):
        """Nearest point on each label rectangle to its component anchor."""
        left = x - 0.5 * w
        right = x + 0.5 * w
        bottom = y - 0.5 * h
        top = y + 0.5 * h

        ex = np.clip(ax, left, right)
        ey = np.clip(ay, bottom, top)

        # If the anchor lies inside the rectangle, attach to the nearest edge.
        inside = (ax >= left) & (ax <= right) & (ay >= bottom) & (ay <= top)
        if inside.any():
            d_left = ax - left
            d_right = right - ax
            d_bottom = ay - bottom
            d_top = top - ay
            stacked = np.stack([d_left, d_right, d_bottom, d_top], axis=1)
            nearest = np.argmin(stacked, axis=1)
            edge_x = np.select([nearest == 0, nearest == 1], [left, right], default=ax)
            edge_y = np.select([nearest == 2, nearest == 3], [bottom, top], default=ay)
            ex = np.where(inside, edge_x, ex)
            ey = np.where(inside, edge_y, ey)

        return ex, ey

    # -- main entry point ---------------------------------------------------

    def update(self, force: bool = False):
        now = time.perf_counter()
        if not force and now - self.last_update_time < 1.0 / MAX_LAYOUT_HZ:
            return
        self.last_update_time = now
        t0 = now

        width, height = self.render_window.GetSize()
        if width < 80 or height < 80:
            return
        # Every position below is in DISPLAY PIXELS, so a solve is only valid
        # for the window size it was computed against. Recorded so the caller
        # can notice the window changed underneath it and re-solve — see
        # LabelSceneManager.resolve_if_resized.
        self.solved_size = (width, height)

        camera = self.renderer.GetActiveCamera()
        camera_pos = np.asarray(camera.GetPosition(), dtype=np.float64)
        self._project_obstacles(width, height)

        # Clearing is a single buffer fill per layer rather than a Python loop.
        for layer in self.label_layers.values():
            layer.hide_all()
        for layer in self.leader_layers.values():
            layer.hide_all()

        allowance_x = OFFSCREEN_ALLOWANCE_FRACTION * width
        allowance_y = OFFSCREEN_ALLOWANCE_FRACTION * height

        world_chunks = []
        channel_of = []
        label_index_chunks = []
        priority_chunks = []
        width_chunks = []
        height_chunks = []
        diag_chunks = []
        min_px_chunks = []
        cap_chunks = []
        angle_chunks = []
        active_chunks = []
        leash_chunks = []

        for ch in self.channels:
            if not ch.components:
                continue
            layer = self.label_layers[ch.name]
            anchors = self.anchor_caches[ch.name].choose(camera_pos)
            count = len(anchors)

            active_chunks.append(
                self._channel_active.get(ch.name, np.ones(count, dtype=bool))
            )
            leash_chunks.append(self._channel_leash[ch.name])
            world_chunks.append(anchors)
            channel_of.append(np.full(count, ch.name, dtype=object))
            label_index_chunks.append(self._channel_indices[ch.name])
            priority_chunks.append(self._channel_priority[ch.name])
            diag_chunks.append(self._channel_diag[ch.name])
            min_px_chunks.append(self._channel_min_px[ch.name])
            cap_chunks.append(np.full(
                count, -1 if ch.max_shown is None else int(ch.max_shown),
                dtype=np.int64))
            width_chunks.append(np.full(count, layer.width, dtype=np.float64))
            height_chunks.append(np.full(count, layer.height, dtype=np.float64))
            angle_chunks.append(self._channel_fallback_angle[ch.name])

        total_components = sum(len(ch.components) for ch in self.channels)

        if not world_chunks:
            self.shown_count = 0
            self.hidden_count = total_components
            self.last_layout_ms = (time.perf_counter() - t0) * 1000.0
            return

        world = np.concatenate(world_chunks, axis=0)
        names = np.concatenate(channel_of, axis=0)
        label_indices = np.concatenate(label_index_chunks, axis=0)
        priority = np.concatenate(priority_chunks, axis=0)
        diag = np.concatenate(diag_chunks, axis=0)
        min_px = np.concatenate(min_px_chunks, axis=0)
        caps = np.concatenate(cap_chunks, axis=0)
        w = np.concatenate(width_chunks, axis=0)
        h = np.concatenate(height_chunks, axis=0)
        angles = np.concatenate(angle_chunks, axis=0)
        active = np.concatenate(active_chunks, axis=0)
        leash = np.concatenate(leash_chunks, axis=0)

        ax, ay, _ndc_z, in_front = self._project(world, width, height)

        # How big is each component ON SCREEN? A per-cell label is only worth
        # drawing once the thing it names is big enough to be worth naming, and
        # gating on that is what makes label density follow the zoom by itself
        # rather than from a hand-tuned schedule.
        #
        # Depth along the view axis, NOT the third value _project returns —
        # that one is normalized device z, which lives in [-1, 1] and would
        # make every component look enormous, passing the gate always.
        camera_dir = normalize(
            np.asarray(camera.GetDirectionOfProjection()), (0.0, 0.0, -1.0))
        depth = (world - camera_pos[None, :]) @ camera_dir
        screen_px = diag / np.maximum(
            self._world_per_pixel(depth, height), 1e-9)
        big_enough = (min_px <= 0.0) | (screen_px >= min_px)

        on_screen = (
            active
            & in_front
            & big_enough
            & (ax >= -allowance_x)
            & (ax <= width + allowance_x)
            & (ay >= -allowance_y)
            & (ay <= height + allowance_y)
        )

        keep_idx = np.nonzero(on_screen)[0]
        if len(keep_idx) == 0:
            for layer in self.label_layers.values():
                layer.modified()
            for layer in self.leader_layers.values():
                layer.modified()
            self.shown_count = 0
            self.hidden_count = total_components
            self.last_layout_ms = (time.perf_counter() - t0) * 1000.0
            return

        ax = ax[keep_idx]
        ay = ay[keep_idx]
        screen_px = screen_px[keep_idx]
        caps = caps[keep_idx]
        names = names[keep_idx]
        label_indices = label_indices[keep_idx]
        priority = priority[keep_idx]
        w = w[keep_idx]
        h = h[keep_idx]
        angles = angles[keep_idx]
        leash = leash[keep_idx]

        desired_x, desired_y = self._initial_positions(ax, ay, w, h, angles)
        x, y = self._constrain(
            desired_x.copy(), desired_y.copy(), ax, ay, w, h, width, height, leash
        )
        x, y = self._relax(
            x, y, ax, ay, w, h, desired_x, desired_y, priority, width, height, leash
        )

        keep = self._select_visible(x, y, w, h, priority)
        keep &= ~self._blocked_by_obstacle(x, y, w, h)
        keep = self._apply_channel_caps(keep, names, caps, screen_px)
        ex, ey = self._leader_endpoints(x, y, w, h, ax, ay)
        keep &= ~self._leader_blocked(ax, ay, ex, ey)

        for ch in self.channels:
            selector = keep & (names == ch.name)
            if not selector.any():
                continue
            idx = label_indices[selector]
            angles = self._screen_angles(ch, selector, names, width, height)
            self.label_layers[ch.name].set_positions(
                idx, x[selector], y[selector], angles)
            self.leader_layers[ch.name].set_segments(
                idx, ax[selector], ay[selector], ex[selector], ey[selector]
            )

        for layer in self.label_layers.values():
            layer.modified()
        for layer in self.leader_layers.values():
            layer.modified()

        self.shown_count = int(keep.sum())
        self.hidden_count = total_components - self.shown_count
        self.last_layout_ms = (time.perf_counter() - t0) * 1000.0

    def set_visible(self, visible: bool):
        for layer in self.label_layers.values():
            layer.actor.SetVisibility(bool(visible))
        for layer in self.leader_layers.values():
            layer.actor.SetVisibility(bool(visible))

    def describe(self) -> str:
        kinds = {getattr(x, "kind", "unknown") for x in self.label_layers.values()}
        return ", ".join(sorted(kinds))



# ==========================================================================
# text wrapping + glyph blocks
# ==========================================================================

# Colocation: heatmap regions -> extruded prisms -> surface-conforming patches
# -----------------------------------------------------------------------------


def _balance_lines(words: List[str], n_lines: int) -> List[str]:
    """Split words across n_lines minimizing the width of the widest line.

    Greedy filling gets this wrong often enough to matter: it breaks as soon as
    a line passes the average, so "Hoechst MART1 colocation" comes out as three
    lines when two are tighter. Words per label are few, so solve it exactly.
    """
    m = len(words)
    if n_lines >= m:
        return list(words)

    def width(i: int, j: int) -> int:
        return sum(len(w) for w in words[i:j]) + (j - i - 1)

    inf = float("inf")
    dp = [[inf] * (m + 1) for _ in range(n_lines + 1)]
    nxt = [[i + 1 for i in range(m + 1)] for _ in range(n_lines + 1)]
    for k in range(n_lines + 1):
        dp[k][m] = 0.0
    for k in range(1, n_lines + 1):
        for i in range(m - 1, -1, -1):
            best, best_j = inf, i + 1
            for j in range(i + 1, m + 1):
                if dp[k - 1][j] == inf:
                    continue
                value = max(width(i, j), dp[k - 1][j])
                if value < best:
                    best, best_j = value, j
            dp[k][i] = best
            nxt[k][i] = best_j

    lines: List[str] = []
    i = 0
    for k in range(n_lines, 0, -1):
        if i >= m:
            break
        j = nxt[k][i]
        lines.append(" ".join(words[i:j]))
        i = j
    if i < m:
        lines.append(" ".join(words[i:]))
    return [l for l in lines if l]


def wrap_text(text: str, max_aspect: float) -> List[str]:
    """Break a label across lines so it stops being a long thin ribbon.

    A wide label sweeps across many cells, which both clutters the view and
    forces the layout to drop neighbors. Stacking lines makes the same text
    occupy a compact block instead. Single words are left alone; breaking a
    word mid-glyph looks worse than a wide label.
    """
    words = [w for w in text.replace("\n", " ").split(" ") if w]
    if len(words) <= 1:
        return [text.strip() or text]

    chosen = _balance_lines(words, len(words))
    for n in range(1, len(words) + 1):
        lines = _balance_lines(words, n)
        widest = max(len(l) for l in lines)
        # Approximate glyph metrics: average advance ~0.62, line pitch ~1.35.
        aspect = (widest * 0.62) / (len(lines) * 1.35)
        if aspect <= max_aspect:
            chosen = lines
            break
    return chosen


def build_text_block(lines: Sequence[str], line_spacing: float):
    """Centered, multi-line glyph geometry from vtkVectorText.

    vtkVectorText understands newlines but left-aligns the result, so each line
    is generated on its own and placed by hand. Returns the merged points, the
    triangles, and the height of a single line, which is what keeps glyph size
    constant no matter how many lines a label wraps to.
    """
    blocks = []
    line_height = 1.0
    for text in lines:
        source = vtk.vtkVectorText()
        source.SetText(text)
        source.Update()
        pd = triangulate(source.GetOutput())
        if pd.GetNumberOfPoints() == 0:
            continue
        pts = vtk_to_numpy(pd.GetPoints().GetData()).astype(np.float64).copy()
        tris = _triangle_array(pd)
        lo, hi = pts[:, 0].min(), pts[:, 0].max()
        pts[:, 0] -= 0.5 * (lo + hi)
        line_height = max(line_height, float(pts[:, 1].max() - pts[:, 1].min()))
        blocks.append((pts, tris))

    if not blocks:
        raise RuntimeError("vtkVectorText produced no geometry")

    n = len(blocks)
    all_pts = []
    all_tris = []
    offset = 0
    for row, (pts, tris) in enumerate(blocks):
        # Row 0 is the top line, so later rows sit lower.
        pts = pts.copy()
        pts[:, 1] += (n - 1 - row) * line_spacing - 0.5 * (n - 1) * line_spacing
        all_pts.append(pts)
        all_tris.append(tris + offset)
        offset += len(pts)

    return np.concatenate(all_pts), np.concatenate(all_tris), line_height


def dilate_mask(mask: np.ndarray, iterations: int) -> np.ndarray:
    """8-connected binary dilation, used to merge neighboring heatmap regions."""
    m = mask
    for _ in range(max(int(iterations), 0)):
        p = np.zeros((m.shape[0] + 2, m.shape[1] + 2), dtype=bool)
        p[1:-1, 1:-1] = m
        m = (
            m
            | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
            | p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:]
        )
    return m


def label_regions(mask: np.ndarray) -> Tuple[np.ndarray, int]:
    """8-connected labeling of a small boolean grid.

    Written out rather than pulled from scipy so the dependency list stays at
    numpy, vtk, and trame. The heatmap is 128x128, so a flood fill is fine.
    """
    h, w = mask.shape
    out = np.zeros((h, w), dtype=np.int32)
    current = 0
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx] or out[sy, sx]:
                continue
            current += 1
            out[sy, sx] = current
            stack = [(sy, sx)]
            while stack:
                y, x = stack.pop()
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not out[ny, nx]:
                            out[ny, nx] = current
                            stack.append((ny, nx))
    return out, current


@dataclass

# ==========================================================================
# coloc sites + mesh soup + interaction sites
# ==========================================================================

class ColocSite:
    """One colocation callout.

    A connected region of the r=0 heatmap, treated as a prism extruded through
    the full depth of the data. The label anchors wherever a camera ray first
    meets mesh inside that prism.
    """

    site_id: int
    label_index: int
    n_bins: int
    mass: float                      # summed heatmap value, used as priority
    prism: Tuple[float, float, float, float]   # xmin, xmax, ymin, ymax
    seed: np.ndarray                 # 3D anchor guess, refined by raycasting
    bin_keys: frozenset = frozenset()  # the flagged bins themselves, as row*K+col
    bin_origin: Tuple[float, float] = (0.0, 0.0)
    bin_size: Tuple[float, float] = (1.0, 1.0)
    tri_ids: np.ndarray = None       # flagged-bin triangles, hit by the fit rays
    occ_ids: np.ndarray = None       # nearby vertices, used only for clearance
    pad: float = 0.0                 # prism tolerance, one heatmap bin
    shown: bool = False
    corners: np.ndarray = None       # world-space patch corners, for exclusion
    screen_rect: Optional[Tuple[float, float, float, float]] = None  # cx, cy, w, h


class MeshSoup:
    """Flat triangle arrays for every channel, shared by all colocation sites.

    Rays are tested against a per-site subset of this, so no locator is built
    and no VTK call happens per ray.
    """

    def __init__(self, polydatas: List[vtk.vtkPolyData],
                 occluders: Optional[List[vtk.vtkPolyData]] = None):
        verts = []
        tris = []
        offset = 0
        for pd in polydatas:
            p = vtk_to_numpy(pd.GetPoints().GetData()).astype(np.float64, copy=False)
            t = _triangle_array(pd)
            verts.append(p)
            tris.append(t + offset)
            offset += len(p)

        self.verts = np.concatenate(verts, axis=0) if verts else np.zeros((0, 3))
        self.tris = np.concatenate(tris, axis=0) if tris else np.zeros((0, 3), np.int64)
        if len(self.tris):
            self.centroids = self.verts[self.tris].mean(axis=1)
        else:
            self.centroids = np.zeros((0, 3))

        # Fitting runs against the proxy, clearance against the real surface.
        src = polydatas if occluders is None else occluders
        occ = [
            vtk_to_numpy(pd.GetPoints().GetData()).astype(np.float64, copy=False)
            for pd in src
        ]
        self.occ_verts = np.concatenate(occ, axis=0) if occ else np.zeros((0, 3))

    def ids_in_prism(self, prism, margin: float) -> np.ndarray:
        return np.nonzero(self._mask(self.centroids, prism, margin))[0]

    def verts_in_prism(self, prism, margin: float) -> np.ndarray:
        return np.nonzero(self._mask(self.occ_verts, prism, margin))[0]

    @staticmethod
    def _mask(pts, prism, margin: float):
        x0, x1, y0, y1 = prism
        return (
            (pts[:, 0] >= x0 - margin)
            & (pts[:, 0] <= x1 + margin)
            & (pts[:, 1] >= y0 - margin)
            & (pts[:, 1] <= y1 + margin)
        )


def build_coloc_sites(
    heatmap: np.ndarray,
    soup: MeshSoup,
    origin_xy: Tuple[float, float],
    bin_size: Tuple[float, float],
    min_value: int,
    min_bins: int,
    max_sites: Optional[int],
    merge_bins: int = COLOC_MERGE_BINS,
    min_separation: float = COLOC_MIN_SEPARATION,
) -> List[ColocSite]:
    """Turn heatmap regions into extruded prisms with local geometry attached."""
    t0 = time.perf_counter()
    mask = heatmap >= min_value
    # Regions that sit within a few bins of each other describe one piece of
    # tissue, so grow the mask before labeling and let them fuse into a single
    # site. Without this a dense cluster produces a pile of overlapping labels
    # and the layout throws most of them away.
    grown = dilate_mask(mask, merge_bins)
    labels, n = label_regions(grown)
    if n == 0:
        print("[coloc] heatmap has no regions above the threshold")
        return []

    ox, oy = origin_xy
    bx, by = bin_size

    raw = []
    for rid in range(1, n + 1):
        sel = (labels == rid) & mask     # score on the real bins, not the grown ones
        count = int(sel.sum())
        if count < min_bins:
            continue
        rows, cols = np.nonzero(sel)
        x0 = ox + cols.min() * bx
        x1 = ox + (cols.max() + 1) * bx
        y0 = oy + rows.min() * by
        y1 = oy + (rows.max() + 1) * by
        mass = float(heatmap[sel].sum())
        keys = frozenset((int(r) * 100000 + int(c)) for r, c in zip(rows, cols))
        raw.append((mass, count, (x0, x1, y0, y1), rid, keys))

    raw.sort(key=lambda r: -r[0])

    # Thin what survives so labels are not stacked on one another. Strongest
    # site wins, and anything anchored too close to an accepted one is dropped.
    if min_separation > 0.0:
        kept = []
        centers = []
        for entry in raw:
            pr = entry[2]
            cx, cy = 0.5 * (pr[0] + pr[1]), 0.5 * (pr[2] + pr[3])
            if any(
                math.hypot(cx - ox, cy - oy) < min_separation for ox, oy in centers
            ):
                continue
            centers.append((cx, cy))
            kept.append(entry)
        dropped = len(raw) - len(kept)
        raw = kept
        if dropped:
            print(
                f"[coloc] thinned {dropped} sites closer than "
                f"{min_separation:.0f} world units to a stronger one"
            )

    if max_sites is not None:
        raw = raw[:max_sites]

    margin = max(bx, by)
    wide = max(margin, COLOC_SOUP_MARGIN)
    sites: List[ColocSite] = []
    for label_index, (mass, count, prism, rid, keys) in enumerate(raw):
        # Geometry the label is fitted to. This must cover the whole LABEL
        # footprint, not just the flagged bins: a label is far wider than a
        # 2-bin prism, and restricting the fit to the prism left most of the
        # scaffold with no ray hits, filled from neighbors, hence flat. The
        # prism still decides WHERE the label is anchored, via the seed.
        tri_ids = soup.ids_in_prism(prism, wide)
        if len(tri_ids) == 0:
            continue
        # Clearance needs the neighbors that can occlude the text. Those are
        # sampled as points, never raycast, so a generous margin costs little.
        occ_ids = soup.verts_in_prism(prism, wide)
        # Seed is the middle of the prism in xy and the mean height of the
        # geometry inside it. Raycasting refines this every recompute.
        pts = soup.centroids[tri_ids]
        seed = np.array(
            [0.5 * (prism[0] + prism[1]), 0.5 * (prism[2] + prism[3]), pts[:, 2].mean()]
        )
        sites.append(
            ColocSite(
                site_id=rid,
                label_index=len(sites),
                n_bins=count,
                mass=mass,
                prism=prism,
                bin_keys=keys,
                bin_origin=(ox, oy),
                bin_size=(bx, by),
                seed=seed,
                tri_ids=tri_ids,
                occ_ids=occ_ids,
                pad=margin,
            )
        )

    tri_counts = [len(s.tri_ids) for s in sites]
    occ_counts = [len(s.occ_ids) for s in sites]
    print(
        f"[coloc] {len(sites)} sites from {n} merged regions "
        f"(value>={min_value}, >={min_bins} bins, merge {merge_bins} bins) "
        f"in {time.perf_counter() - t0:.1f}s"
    )
    if tri_counts:
        print(
            f"[coloc] local geometry per site: median {int(np.median(tri_counts))} "
            f"triangles for fitting, median {int(np.median(occ_counts))} "
            f"vertices for clearance"
        )
    return sites


def build_interaction_components(
    occ_a: np.ndarray,
    occ_b: np.ndarray,
    coloc: np.ndarray,
    soup: "MeshSoup",
    origin_xy: Tuple[float, float],
    bin_size: Tuple[float, float],
    radius_bins: int,
    merge_bins: int,
    min_bins: int,
    min_separation: float,
    max_sites: Optional[int],
) -> List[Component]:
    """Find where the two markers come close without overlapping.

    Each channel's occupancy map is grown by a cell-scale radius and the two are
    intersected, then every bin with a direct overlap is removed. What is left is
    adjacency: the markers are near neighbors but distinct.

    Validated against the meshes: at a two-bin radius the true surface-to-surface
    gap in these bins is a median of 12 world units, against 63 for bins holding
    only one marker, and no touching bin is wrongly included.

    Returns Components so interaction labels flow through the same screen-space
    solver as the per-cell labels, which is what keeps them from colliding.
    """
    t0 = time.perf_counter()
    near_a = dilate_mask(occ_a > 0, radius_bins)
    near_b = dilate_mask(occ_b > 0, radius_bins)
    # One bin of slack around the overlap so a label never lands on the rim of a
    # colocation, where "near but not touching" is a measurement artifact.
    contact = near_a & near_b & ~dilate_mask(coloc > 0, 1)
    if not contact.any():
        print("[interaction] no adjacency found")
        return []

    grown = dilate_mask(contact, merge_bins)
    labels, n = label_regions(grown)

    ox, oy = origin_xy
    bx, by = bin_size
    height, width = contact.shape

    raw = []
    for rid in range(1, n + 1):
        sel = (labels == rid) & contact
        count = int(sel.sum())
        if count < min_bins:
            continue
        rows, cols = np.nonzero(sel)
        prism = (
            ox + cols.min() * bx, ox + (cols.max() + 1) * bx,
            oy + rows.min() * by, oy + (rows.max() + 1) * by,
        )
        raw.append((float(count), sel, prism, rid))

    raw.sort(key=lambda r: -r[0])

    if min_separation > 0.0:
        kept, centers = [], []
        for entry in raw:
            pr = entry[2]
            cx, cy = 0.5 * (pr[0] + pr[1]), 0.5 * (pr[2] + pr[3])
            if any(math.hypot(cx - a, cy - b) < min_separation for a, b in centers):
                continue
            centers.append((cx, cy))
            kept.append(entry)
        raw = kept

    if max_sites is not None:
        raw = raw[:max_sites]

    # Bin coordinates for every surface vertex, computed once.
    pts = soup.occ_verts
    if len(pts) == 0:
        return []
    vr = np.floor((pts[:, 1] - oy) / by).astype(np.int64)
    vc = np.floor((pts[:, 0] - ox) / bx).astype(np.int64)
    in_range = (vr >= 0) & (vr < height) & (vc >= 0) & (vc < width)
    vr = np.clip(vr, 0, height - 1)
    vc = np.clip(vc, 0, width - 1)

    components: List[Component] = []
    for label_index, (strength, sel, prism, rid) in enumerate(raw):
        inside = in_range & sel[vr, vc]
        if inside.sum() < 8:
            continue
        local = pts[inside]

        center = local.mean(axis=0)
        lo, hi = local.min(axis=0), local.max(axis=0)
        ids = farthest_sample_indices(local, ANCHOR_CANDIDATES)
        cand = np.asarray(local[ids], dtype=np.float64).copy()
        start = int(np.argmin(np.sum((cand - center) ** 2, axis=1)))

        components.append(
            Component(
                channel_name="interaction",
                component_id=int(rid),
                area=float(strength),
                diagonal=max(float(np.linalg.norm(hi - lo)), 1e-9),
                center=center.copy(),
                candidate_points=cand,
                label_index=len(components),
                anchor_index=start,
            )
        )

    print(
        f"[interaction] {len(components)} sites from {n} merged regions "
        f"(radius {radius_bins} bins, merge {merge_bins} bins) "
        f"in {time.perf_counter() - t0:.1f}s"
    )
    return components


def ray_triangles(origins: np.ndarray, dirs: np.ndarray, v0, v1, v2):
    """Batched Moller-Trumbore. Returns ray parameter t, inf where there is no hit.

    origins and dirs are (R,3), the triangle arrays are (T,3). Result is (R,T).
    Written out in components rather than with np.cross, because broadcasting
    (R,1,3) against (1,T,3) through np.cross dominated the profile.
    """
    e1 = v1 - v0
    e2 = v2 - v0

    dx, dy, dz = dirs[:, 0:1], dirs[:, 1:2], dirs[:, 2:3]          # (R,1)
    e1x, e1y, e1z = e1[None, :, 0], e1[None, :, 1], e1[None, :, 2]  # (1,T)
    e2x, e2y, e2z = e2[None, :, 0], e2[None, :, 1], e2[None, :, 2]

    px = dy * e2z - dz * e2y
    py = dz * e2x - dx * e2z
    pz = dx * e2y - dy * e2x

    det = e1x * px + e1y * py + e1z * pz
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)

    tvx = origins[:, 0:1] - v0[None, :, 0]
    tvy = origins[:, 1:2] - v0[None, :, 1]
    tvz = origins[:, 2:3] - v0[None, :, 2]

    u = (tvx * px + tvy * py + tvz * pz) * inv

    qx = tvy * e1z - tvz * e1y
    qy = tvz * e1x - tvx * e1z
    qz = tvx * e1y - tvy * e1x

    v = (dx * qx + dy * qy + dz * qz) * inv
    t = (e2x * qx + e2y * qy + e2z * qz) * inv

    good = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1.0 + 1e-9) & (t > 1e-6)
    return np.where(good, t, np.inf)


def _pad_edge(z: np.ndarray) -> np.ndarray:
    """Replicate-pad by one cell. np.pad showed up hot in the profile."""
    h, w = z.shape
    p = np.empty((h + 2, w + 2), dtype=z.dtype)
    p[1:-1, 1:-1] = z
    p[0, 1:-1] = z[0]
    p[-1, 1:-1] = z[-1]
    p[:, 0] = p[:, 1]
    p[:, -1] = p[:, -2]
    return p


def _fill_and_smooth(grid: np.ndarray, passes: int) -> np.ndarray:
    """Fill NaN entries from neighbors, then Laplacian-smooth the scaffold.

    The fill matters as much as the smoothing. Roughly a fifth of scaffold rays
    fall into the gaps between cells and would otherwise leave holes.
    """
    z = grid.copy()
    if np.isnan(z).all():
        return z
    for _ in range(40):
        if not np.isnan(z).any():
            break
        p = _pad_edge(z)
        stack = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        good = ~np.isnan(stack)
        count = good.sum(axis=0)
        total = np.where(good, stack, 0.0).sum(axis=0)
        mean = np.where(count > 0, total / np.maximum(count, 1), np.nan)
        z = np.where(np.isnan(z), mean, z)
    z = np.where(np.isnan(z), np.nanmean(grid), z)

    for _ in range(passes):
        p = _pad_edge(z)
        nb = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) * 0.25
        z = 0.5 * z + 0.5 * nb
    return z



# ==========================================================================
# conforming patches
# ==========================================================================

def _plane_fit(uu: np.ndarray, vv: np.ndarray, z: np.ndarray) -> np.ndarray:
    a = np.column_stack([uu.ravel(), vv.ravel(), np.ones(z.size)])
    coef, *_ = np.linalg.lstsq(a, z.ravel(), rcond=None)
    return (a @ coef).reshape(z.shape)


class ConformingLabelLayer:
    """All colocation labels as deformed glyph geometry in a single actor.

    The text is real polygonal outlines from vtkVectorText, not a rasterized
    texture, so there is no background panel, no frame, and no filtering
    blur. Letters lie directly on the tissue and bend with it.

    One template is built once and instanced per site. Fitting produces a
    scaffold of surface points; every glyph vertex is placed by bilinear
    interpolation into that scaffold, so the whole string follows the surface
    with no per-glyph work. The interpolation weights are precomputed, which
    makes deforming a site a handful of array multiplies.

    These are real 3D props rather than overlay actors, which is what buys
    correct depth testing. A label behind a nucleus is occluded by it.
    """

    def __init__(self, renderer: vtk.vtkRenderer, count: int, text: str,
                 color, font_size: int, dpi: int,
                 wrap_aspect: float = COLOC_WRAP_ASPECT,
                 line_spacing: float = COLOC_LINE_SPACING,
                 overlay_renderer: Optional[vtk.vtkRenderer] = None):
        self.count = count
        self.text = text
        self.nu = CONFORM_SCAFFOLD_U
        self.nv = CONFORM_SCAFFOLD_V

        self.lines = wrap_text(text, wrap_aspect)
        tp, tris, line_height = build_text_block(self.lines, line_spacing)

        x0, x1, y0, y1 = tp[:, 0].min(), tp[:, 0].max(), tp[:, 1].min(), tp[:, 1].max()
        tw = max(x1 - x0, 1e-9)
        th = max(y1 - y0, 1e-9)
        self.aspect = float(tw / th)
        # A two-line label is twice as tall as a one-line label, so the caller's
        # requested height is scaled by this to keep the GLYPHS the same size
        # whatever the wrap produces.
        self.height_ratio = float(th / max(line_height, 1e-9))
        self.per_site = len(tp)

        # Glyph vertices in the label's own unit square. u runs along the
        # baseline, v up the block.
        u = (tp[:, 0] - x0) / tw
        v = (tp[:, 1] - y0) / th
        self._weights = self._scaffold_weights(u, v)

        n_pts = max(count * self.per_site, 1)

        self.points = vtk.vtkPoints()
        self.points.SetDataTypeToDouble()
        self.points.SetNumberOfPoints(n_pts)

        cells = vtk.vtkCellArray()
        for s in range(count):
            base = s * self.per_site
            for a, b, c in tris:
                tri = vtk.vtkTriangle()
                tri.GetPointIds().SetId(0, base + int(a))
                tri.GetPointIds().SetId(1, base + int(b))
                tri.GetPointIds().SetId(2, base + int(c))
                cells.InsertNextCell(tri)

        self.polydata = vtk.vtkPolyData()
        self.polydata.SetPoints(self.points)
        self.polydata.SetPolys(cells)

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(self.polydata)
        mapper.ScalarVisibilityOff()
        try:
            mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(-8.0, -8.0)
        except Exception:
            pass

        self.actor = vtk.vtkActor()
        self.actor.SetMapper(mapper)
        # Unlike the 2D overlay layers, this is a real 3D prop, so its vertices
        # would otherwise feed into ResetCamera. Hidden labels park far off in
        # world space, which would make the reset frame empty air.
        self.actor.SetUseBounds(False)
        prop = self.actor.GetProperty()
        prop.SetColor(*color)
        prop.LightingOff()
        prop.SetAmbient(1.0)
        prop.SetDiffuse(0.0)
        prop.BackfaceCullingOff()
        prop.SetOpacity(1.0)
        # Drawn by the OVERLAY renderer when there is one. These patches sit
        # on a surface inside the tissue, and in the main renderer the volume
        # ray caster composites everything in front of them — which is most of
        # the block — so the text came out washed into the volume. The overlay
        # is a later layer sharing the same camera, so registration is
        # identical and the labels simply survive.
        #
        # Nothing is lost by skipping the depth test against the meshes: the
        # conformer already slides each patch forward past every surface point
        # it sampled (see COLOC_LIFT_FRACTION and _occluder_depth), so mesh
        # occlusion is resolved analytically rather than by the depth buffer.
        (overlay_renderer or renderer).AddActor(self.actor)

        self._np = vtk_to_numpy(self.points.GetData())
        self.hide_all()

    def _scaffold_weights(self, u: np.ndarray, v: np.ndarray):
        """Bilinear weights and corner indices into a flattened nv x nu grid."""
        nu, nv = self.nu, self.nv
        fu = np.clip(u, 0.0, 1.0) * (nu - 1)
        fv = np.clip(v, 0.0, 1.0) * (nv - 1)
        i = np.clip(np.floor(fu).astype(np.int64), 0, nu - 2)
        j = np.clip(np.floor(fv).astype(np.int64), 0, nv - 2)
        fx = (fu - i)[:, None]
        fy = (fv - j)[:, None]
        return (
            j * nu + i, j * nu + i + 1, (j + 1) * nu + i, (j + 1) * nu + i + 1,
            (1.0 - fx) * (1.0 - fy), fx * (1.0 - fy),
            (1.0 - fx) * fy, fx * fy,
        )

    def _deform(self, grid: np.ndarray) -> np.ndarray:
        g = grid.reshape(-1, 3)
        i00, i10, i01, i11, w00, w10, w01, w11 = self._weights
        return g[i00] * w00 + g[i10] * w10 + g[i01] * w01 + g[i11] * w11

    def set_patch(self, index: int, grid: np.ndarray):
        base = index * self.per_site
        self._np[base : base + self.per_site] = self._deform(grid)

    def hide(self, index: int):
        base = index * self.per_site
        self._np[base : base + self.per_site] = HIDDEN_PX

    def hide_all(self):
        self._np[:] = HIDDEN_PX

    def modified(self):
        self.points.GetData().Modified()
        self.points.Modified()
        self.polydata.Modified()


class ColocConformer:
    """Fits colocation label patches onto the mesh surface.

    Runs on interaction end, never during interaction. Once fitted, a patch
    lives in world space, so orbiting costs nothing: the geometry is already
    where it belongs and the GPU just draws it.
    """

    def __init__(
        self,
        renderer: vtk.vtkRenderer,
        render_window: vtk.vtkRenderWindow,
        sites: List[ColocSite],
        soup: MeshSoup,
        text: str = "colocalization",
        wrap_aspect: float = COLOC_WRAP_ASPECT,
        line_spacing: float = COLOC_LINE_SPACING,
        overlay_renderer: Optional[vtk.vtkRenderer] = None,
    ):
        self.renderer = renderer
        self.overlay_renderer = overlay_renderer
        self.render_window = render_window
        self.sites = sites
        self.soup = soup
        dpi = int(render_window.GetDPI()) if render_window.GetDPI() > 0 else 72
        self.layer = ConformingLabelLayer(
            renderer, len(sites), text, COLOC_LABEL_COLOR, COLOC_FONT_SIZE, dpi,
            wrap_aspect=wrap_aspect, line_spacing=line_spacing,
            overlay_renderer=overlay_renderer,
        )
        if len(self.layer.lines) > 1:
            print(f"[coloc] label wrapped to {len(self.layer.lines)} lines: "
                  f"{' / '.join(self.layer.lines)}")
        self._proj = None
        self._last_view = None
        # The size a label is authored for is defined against the view that
        # frames the whole dataset, not against whichever camera happens to be
        # active on the first fit. Deriving it from the data makes the scale
        # reproducible no matter where the user starts or how often we refit.
        if len(soup.occ_verts):
            lo = soup.occ_verts.min(axis=0)
            hi = soup.occ_verts.max(axis=0)
            self._scene_diagonal = float(np.linalg.norm(hi - lo))
        else:
            self._scene_diagonal = 1.0
        self.zoom_compensation = COLOC_ZOOM_COMPENSATION
        self.label_height = COLOC_LABEL_HEIGHT
        self.target_px = COLOC_TARGET_PX_HEIGHT
        self.alpha = CONFORM_ALPHA
        self.passes = CONFORM_SMOOTH_PASSES
        self.enabled = True
        self.last_ms = 0.0
        self.shown_count = 0

        u = np.linspace(-0.5, 0.5, self.layer.nu)
        v = np.linspace(-0.5, 0.5, self.layer.nv)
        self._uu, self._vv = np.meshgrid(u, v)

    def _current_view(self):
        cam = self.renderer.GetActiveCamera()
        pos = np.asarray(cam.GetPosition(), dtype=np.float64)
        focal = np.asarray(cam.GetFocalPoint(), dtype=np.float64)
        return (
            pos,
            normalize(np.asarray(cam.GetDirectionOfProjection()), (0, 0, -1)),
            normalize(np.asarray(cam.GetViewUp()), (0, 1, 0)),
            float(cam.GetParallelScale()),
            tuple(self.render_window.GetSize()),
            # Distance to the focal point. Under PERSPECTIVE projection this is
            # the only quantity a dolly zoom changes: the parallel scale is a
            # constant, and the view/up directions do not rotate. Without it in
            # the tuple, a zoom could not be detected at all.
            float(np.linalg.norm(pos - focal)),
        )

    def _camera_unchanged(self) -> bool:
        """True when a refit would reproduce what is already on screen.

        Covers resizes, clicks that never dragged, and the tail of a spin, all
        of which otherwise pay the full fitting cost for no visible change.
        The recorded state is written by update(), so a forced refit arms the
        guard too rather than leaving the next call to redo the same work.
        """
        pos, view, up, scale, size, dist = self._current_view()
        last = self._last_view
        if last is None or last[4] != size:
            return False
        if abs(last[3] - scale) > 1e-6 * max(abs(scale), 1.0):
            return False

        tol = math.cos(math.radians(REFIT_MIN_CAMERA_CHANGE_DEG))
        if float(np.dot(last[1], view)) < tol or float(np.dot(last[2], up)) < tol:
            return False

        # A dolly zoom moves the camera along its own axis without rotating it
        # and without touching the parallel scale, so under perspective the
        # focal distance is the ONLY thing that registers it. Label size is
        # derived from this distance (see `_world_per_pixel` and
        # COLOC_ZOOM_COMPENSATION), so ignoring it left the text at a stale
        # size on stale geometry — visibly out of register with the surface.
        if abs(dist - last[5]) > 1e-3 * max(dist, 1e-6):
            return False

        # Tolerance scales with distance to the FOCAL POINT, not with distance
        # from the world origin. Origin-relative was 2-6x too loose here, and
        # worst zoomed in — the camera could move ~2 um, a third of a nucleus,
        # and still count as unchanged. `_pose_moved` in ui/callbacks.py has
        # always measured it this way.
        moved = float(np.linalg.norm(pos - last[0]))
        span = max(dist, 1e-6)
        return moved <= 0.002 * span

    def _world_per_pixel(self, distance: float) -> float:
        _, height = self.render_window.GetSize()
        cam = self.renderer.GetActiveCamera()
        if cam.GetParallelProjection():
            return 2.0 * cam.GetParallelScale() / max(height, 1)
        half = math.radians(cam.GetViewAngle()) * 0.5
        return 2.0 * max(distance, 1e-6) * math.tan(half) / max(height, 1)

    def update(self, force: bool = False):
        if not self.enabled or not self.sites:
            return
        if not force and self._camera_unchanged():
            self.last_ms = 0.0
            return
        t0 = time.perf_counter()

        cam = self.renderer.GetActiveCamera()
        cam_pos = np.asarray(cam.GetPosition(), dtype=np.float64)
        view = normalize(np.asarray(cam.GetDirectionOfProjection()), (0, 0, -1))
        up_hint = normalize(np.asarray(cam.GetViewUp()), (0, 1, 0))
        right = normalize(np.cross(view, up_hint), (1, 0, 0))
        up = normalize(np.cross(right, view), (0, 1, 0))
        parallel = bool(cam.GetParallelProjection())

        width, height = self.render_window.GetSize()
        vw_margin = 0.25 * width
        vh_margin = 0.25 * height

        self._refresh_projection()
        verts = self.soup.verts
        tris = self.soup.tris
        shown = 0

        # Phase one. Predict where each label will land and resolve screen
        # collisions first, so raycasting only runs for labels that survive.
        # Rescaling to a constant on-screen size makes the prediction accurate:
        # the rect is the rasterized text, centered on the projected seed.
        for site in self.sites:
            site.shown = False
            site.screen_rect = None
            self.layer.hide(site.label_index)

        planned = []
        for site in self.sites:
            depth = float(np.dot(site.seed - cam_pos, view))
            if depth <= 0.0:
                continue
            sx, sy = self._project_points(site.seed[None, :])
            if sx is None or not (
                -vw_margin <= sx[0] <= width + vw_margin
                and -vh_margin <= sy[0] <= height + vh_margin
            ):
                continue

            # Label size in world units, chosen so it holds a roughly constant
            # size on screen. Clamped so it never dwarfs or vanishes into its site.
            wpp = self._world_per_pixel(depth)
            if self.target_px > 0.0:
                span = max(site.prism[1] - site.prism[0], site.prism[3] - site.prism[2])
                h_world = float(np.clip(
                    self.target_px * wpp * self.layer.height_ratio,
                    COLOC_MIN_LABEL_HEIGHT,
                    max(COLOC_MAX_LABEL_HEIGHT, span),
                ))
            else:
                reference_wpp = self._scene_diagonal / max(height, 1)
                zoom = wpp / max(reference_wpp, 1e-9)
                h_world = (
                    self.label_height
                    * self.layer.height_ratio
                    * (zoom ** self.zoom_compensation)
                )
                span = max(
                    site.prism[1] - site.prism[0], site.prism[3] - site.prism[2]
                )
                h_world = float(np.clip(
                    h_world,
                    COLOC_MIN_LABEL_HEIGHT,
                    max(COLOC_MAX_LABEL_HEIGHT, span),
                ))
            w_world = h_world * self.layer.aspect
            h_px = h_world / max(wpp, 1e-9)
            planned.append(
                (site, w_world, h_world, (float(sx[0]), float(sy[0]),
                                          h_px * self.layer.aspect, h_px))
            )

        planned.sort(key=lambda r: -r[0].mass)
        kept = []
        for entry in planned:
            cx, cy, w, h = entry[3]
            clash = any(
                abs(cx - ox) < 0.5 * (w + ow) - OVERLAP_EPS_PX
                and abs(cy - oy) < 0.5 * (h + oh) - OVERLAP_EPS_PX
                for _, _, _, (ox, oy, ow, oh) in kept
            )
            if not clash:
                kept.append(entry)

        # Phase two. Fit only the survivors.
        for site, w_world, h_world, _rect in kept:
            span = max(site.prism[1] - site.prism[0], site.prism[3] - site.prism[2])

            # Scaffold, laid out camera-facing in the plane through the seed.
            #
            # Tilting these axes toward the surface's own tangent plane was
            # tried, to make the conformance more visible. It does not work on
            # this geometry: these are blobby isosurfaces, and the angle between
            # a surface normal and the view axis measures a median of 73 deg
            # (only 5% of triangles lie within 30 deg of facing the camera).
            # Text laid flat on a surface that steep is edge-on and unreadable,
            # so any alignment strong enough to see is too strong to read.
            # Camera-facing is the right choice here; bend is the lever that
            # actually conveys the surface, and the scaffold density sets it.
            pts = (
                site.seed[None, None, :]
                + (self._uu * w_world)[..., None] * right[None, None, :]
                + (self._vv * h_world)[..., None] * up[None, None, :]
            )
            flat = pts.reshape(-1, 3)

            if parallel:
                dirs = np.repeat(view[None, :], len(flat), axis=0)
                back = max(span, h_world) * 4.0
                origins = flat - view[None, :] * back
            else:
                dirs = flat - cam_pos[None, :]
                dirs = dirs / np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-12)
                origins = np.repeat(cam_pos[None, :], len(flat), axis=0)

            tri = tris[site.tri_ids]
            v0 = verts[tri[:, 0]]
            v1 = verts[tri[:, 1]]
            v2 = verts[tri[:, 2]]

            # The prism can be far larger than the label. Cull to triangles whose
            # footprint in the label's own frame can actually be hit, which is
            # the difference between testing hundreds of triangles and thousands.
            pad = 0.5 * max(w_world, h_world) / max(self.layer.nu - 1, 1)
            tu = np.stack(
                [(v0 - site.seed) @ right, (v1 - site.seed) @ right, (v2 - site.seed) @ right]
            )
            tv = np.stack(
                [(v0 - site.seed) @ up, (v1 - site.seed) @ up, (v2 - site.seed) @ up]
            )
            near = (
                (tu.max(0) >= -0.5 * w_world - pad)
                & (tu.min(0) <= 0.5 * w_world + pad)
                & (tv.max(0) >= -0.5 * h_world - pad)
                & (tv.min(0) <= 0.5 * h_world + pad)
            )
            if not near.any():
                self.layer.hide(site.label_index)
                continue
            v0, v1, v2 = v0[near], v1[near], v2[near]

            tmat = ray_triangles(origins, dirs, v0, v1, v2)
            tbest = tmat.min(axis=1)
            hit = np.isfinite(tbest)
            hit_fraction = float(hit.mean())
            if hit_fraction < COLOC_MIN_HIT_FRACTION and hit_fraction < 1e-9:
                self.layer.hide(site.label_index)
                continue
            if not hit.any():
                self.layer.hide(site.label_index)
                continue

            grid = np.where(hit, tbest, np.nan).reshape(self.layer.nv, self.layer.nu)
            grid = _fill_and_smooth(grid, self.passes)

            plane = _plane_fit(self._uu * w_world, self._vv * h_world, grid)
            residual = grid - plane
            rms = float(np.sqrt((residual ** 2).mean()))

            # Adaptive damping. A label bent by more than a fraction of its own
            # height stops being readable, so flatten exactly as much as needed.
            alpha = self.alpha
            if hit_fraction < COLOC_MIN_HIT_FRACTION:
                alpha *= 0.4
            if rms > 1e-9:
                alpha = min(alpha, COLOC_MAX_BEND_FRACTION * h_world / rms)
            final_t = plane + alpha * residual

            # Smoothing and damping can leave part of the patch behind a bump it
            # was fitted over, and the depth test then slices the text in half.
            # Shift the whole patch forward until it clears every surface point
            # it sampled, then add a margin. Shifting rather than clamping keeps
            # the fitted shape intact.
            world = origins.reshape(self.layer.nv, self.layer.nu, 3) + (
                final_t[..., None] * dirs.reshape(self.layer.nv, self.layer.nu, 3)
            )

            # Smoothing can leave part of the patch behind a bump it was fitted
            # over, and neighboring cells outside the flagged bins can occlude it
            # too. Both are handled by comparing against a depth map of nearby
            # surface points and sliding the whole patch forward. Sliding rather
            # than clamping keeps the fitted shape intact.
            occ = self._occluder_depth(site, cam_pos, view, right, up,
                                       w_world, h_world)
            depth_grid = (world - cam_pos[None, None, :]) @ view
            clear = COLOC_LIFT_FRACTION * h_world
            known = np.isfinite(occ)
            over = float((depth_grid[known] - occ[known]).max()) if known.any() else 0.0
            world = world - view[None, None, :] * (max(over, 0.0) + clear)

            self.layer.set_patch(site.label_index, world)
            site.shown = True
            shown += 1

            site.corners = np.array([
                world[0, 0], world[0, -1], world[-1, -1], world[-1, 0]
            ])
            corners = world.reshape(-1, 3)
            sx, sy = self._project_points(corners)
            if sx is not None:
                site.screen_rect = (
                    0.5 * (sx.min() + sx.max()),
                    0.5 * (sy.min() + sy.max()),
                    sx.max() - sx.min(),
                    sy.max() - sy.min(),
                )

        shown -= self._separate_on_screen()
        self.layer.modified()
        self.shown_count = shown
        self._last_view = self._current_view()
        self.last_ms = (time.perf_counter() - t0) * 1000.0

    def _separate_on_screen(self) -> int:
        """Hide colocation patches that collide on screen, keeping the strongest.

        In 3D the sites are distinct, but two sites at different depths can
        project onto the same pixels. Highest heatmap mass wins.
        """
        live = [s for s in self.sites if s.shown and s.screen_rect]
        live.sort(key=lambda s: -s.mass)
        accepted: List[ColocSite] = []
        dropped = 0
        for site in live:
            cx, cy, w, h = site.screen_rect
            clash = False
            for other in accepted:
                ox, oy, ow, oh = other.screen_rect
                if (
                    abs(cx - ox) < 0.5 * (w + ow) - OVERLAP_EPS_PX
                    and abs(cy - oy) < 0.5 * (h + oh) - OVERLAP_EPS_PX
                ):
                    clash = True
                    break
            if clash:
                site.shown = False
                site.screen_rect = None
                self.layer.hide(site.label_index)
                dropped += 1
            else:
                accepted.append(site)
        return dropped

    def _occluder_depth(self, site, cam_pos, view, right, up, w_world, h_world):
        """Nearest surface depth per scaffold cell, from nearby vertices.

        Points rather than triangles, and a bin-and-minimum rather than a
        raycast. Clearance only needs to know how far forward to slide, so the
        exactness of ray-triangle intersection would be wasted here, and at wide
        zoom the label footprint is large enough that raycasting it was the
        single most expensive thing the conformer did.
        """
        nv, nu = self.layer.nv, self.layer.nu
        grid = np.full((nv, nu), np.inf)
        if site.occ_ids is None or len(site.occ_ids) == 0:
            return grid

        pts = self.soup.occ_verts[site.occ_ids]
        rel = pts - site.seed[None, :]
        u = rel @ right
        v = rel @ up
        iu = np.rint((u / max(w_world, 1e-9) + 0.5) * (nu - 1))
        iv = np.rint((v / max(h_world, 1e-9) + 0.5) * (nv - 1))
        keep = (iu >= 0) & (iu <= nu - 1) & (iv >= 0) & (iv <= nv - 1)
        if not keep.any():
            return grid

        d = (pts[keep] - cam_pos[None, :]) @ view
        flat = (iv[keep].astype(np.int64) * nu + iu[keep].astype(np.int64))
        out = grid.ravel()
        np.minimum.at(out, flat, d)
        return out.reshape(nv, nu)

    def _refresh_projection(self):
        cam = self.renderer.GetActiveCamera()
        m = cam.GetCompositeProjectionTransformMatrix(
            self.renderer.GetTiledAspectRatio(), -1.0, 1.0
        )
        self._proj = np.array(
            [[m.GetElement(i, j) for j in range(4)] for i in range(4)]
        )

    def _project_points(self, pts: np.ndarray):
        width, height = self.render_window.GetSize()
        if getattr(self, "_proj", None) is None:
            self._refresh_projection()
        mat = self._proj
        hom = np.column_stack([pts, np.ones(len(pts))])
        clip = hom @ mat.T
        w = clip[:, 3]
        if not (w > 1e-9).all():
            return None, None
        ndc = clip[:, :3] / w[:, None]
        return (ndc[:, 0] * 0.5 + 0.5) * width, (ndc[:, 1] * 0.5 + 0.5) * height

    def obstacles(self) -> np.ndarray:
        """Screen rectangles the flat label solver must route around."""
        rects = [s.screen_rect for s in self.sites if s.shown and s.screen_rect]
        if not rects:
            return np.zeros((0, 4))
        return np.asarray(rects, dtype=np.float64)

    def obstacle_points(self) -> np.ndarray:
        """World-space patch corners, so exclusion zones track the camera."""
        pts = [s.corners for s in self.sites if s.shown and s.corners is not None]
        if not pts:
            return np.zeros((0, 4, 3))
        return np.asarray(pts, dtype=np.float64)

    def set_visible(self, visible: bool):
        self.enabled = bool(visible)
        self.layer.actor.SetVisibility(bool(visible))

    def suppress_overlapping_components(self, channels: List[Channel]):
        """Silence the plain per-cell label wherever a colocation label covers it.

        A colocalized nucleus is better described as a colocation than as a
        nucleus, and stacking both callouts on one cell just adds clutter.
        """
        n = 0
        for ch in channels:
            for comp in ch.components:
                cx, cy = float(comp.center[0]), float(comp.center[1])
                for site in self.sites:
                    x0, x1, y0, y1 = site.prism
                    if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                        continue
                    # Merged sites have big rectangular prisms that sweep in
                    # cells the heatmap never flagged, so test the bins
                    # themselves rather than the bounding box.
                    ox, oy = site.bin_origin
                    bx, by = site.bin_size
                    key = int((cy - oy) // by) * 100000 + int((cx - ox) // bx)
                    if key in site.bin_keys:
                        comp.suppressed = True
                        n += 1
                        break
        print(f"[coloc] suppressed {n} per-cell labels covered by colocation sites")


# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------

