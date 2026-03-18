label_config = {
    # =====================================================================
    # REGION EXTRACTION
    # =====================================================================
    "MIN_CELLS": 100,
    "MAX_CELLS_PER_REGION": 5000,
    "SPLIT_MIN_CELLS": 500,
    "SPLIT_MAX_DEPTH": 4,

    # =====================================================================
    # CO-LOCALIZATION DETECTION
    # =====================================================================
    "DETECT_COLOC": True,
    "COLOC_MIN_ENCLOSED": 5,

    # =====================================================================
    # INTERACTION DETECTION
    # =====================================================================
    "DETECT_INTERACTIONS": True,
    "INTERACTION_DISTANCE": 5.0,
    "INTERACTION_BILLBOARD_MIN_GAP": 2.0,
    "INTERACTION_BILLBOARD_OFFSET": 2.0,
    "INTERACTION_BILLBOARD_SCALE": 0.5,
    "INTERACTION_BILLBOARD_COLOR": (1.0, 0.8, 0.5),
    "INTERACTION_FLAGPOLE_HEIGHT": 3.0,
    "INTERACTION_FLAGPOLE_SCALE": 0.4,
    "INTERACTION_FLAGPOLE_COLOR": (1.0, 0.8, 0.5),
    "INTERACTION_CLUSTER_RADIUS": 6.0,   # scaled from 60.0
    "MAX_INTERACTION_LABELS": 8,

    # =====================================================================
    # LABEL DISPLAY
    # =====================================================================
    "SHOW_LABELS": True,

    # =====================================================================
    # HIERARCHICAL LABEL LEVELS
    #
    # Camera distance controls level of detail:
    #   dist >= OVERVIEW_DIST  → single "overall" label
    #   REGION_DIST < dist < OVERVIEW_DIST → clustered billboard labels
    #   dist <= REGION_DIST → individual region labels (surface/flagpole/billboard)
    #
    # Keep HIERARCHY_REGION_DIST well below FAR_THRESHOLD so there is a
    # distance band where individual regions are shown as FLAGPOLE labels
    # (REGION_DIST < dist < FAR_THRESHOLD). Without this gap the hierarchy
    # switches to individual regions at the same distance the zoom heuristic
    # switches to SURFACE — skipping FLAGPOLE entirely.
    # =====================================================================
    "HIERARCHY_OVERVIEW_DIST": 500.0,
    "HIERARCHY_REGION_DIST": 200.0,

    "CLUSTER_LABEL_BASE_SCALE": 1.0,
    "CLUSTER_EXTENT_SCALE_FACTOR": 50.0,
    "CLUSTER_LABEL_COLOR": (1.0, 1.0, 0.8),
    "CLUSTER_LABEL_OFFSET": 10.0,

    # =====================================================================
    # ZOOM-BASED LABEL TYPE HEURISTIC
    #
    #   dist > FAR_THRESHOLD              → BILLBOARD  (cluster/overview zoom)
    #   MID_THRESHOLD < dist <= FAR       → FLAGPOLE   (individual regions, mid zoom)
    #   dist <= MID_THRESHOLD             → SURFACE    (close zoom)
    #
    # FAR must be > HIERARCHY_REGION_DIST so individual regions use FLAGPOLE
    # in the band [HIERARCHY_REGION_DIST, FAR_THRESHOLD].
    # MID must be < HIERARCHY_REGION_DIST so SURFACE only triggers up close.
    # =====================================================================
    "FAR_THRESHOLD": 400.0,
    "MID_THRESHOLD": 150.0,

    "MAX_VISIBLE_LABELS": 50,
    "LABEL_SCREEN_PADDING": 15,

    # =====================================================================
    # SURFACE LABEL SETTINGS
    # =====================================================================
    "DILATION_AMOUNT": 3.0,            # BioSET geometry requires smaller value than 10x scaling
    "SMOOTH_ITERATIONS": 100,
    "SURFACE_LABEL_HEIGHT": 1.0,       # scaled from 10.0
    "SURFACE_HEIGHT_FACTOR": 0.12,     # region_diag * this = label height
    "SURFACE_MIN_HEIGHT": 0.5,         # scaled from 6.0
    "SURFACE_MAX_HEIGHT": 2.0,         # scaled from 30.0
    "WALK_STEP": 3.0,
    "WALK_STEPS": 30,
    "WALK_NORMAL_COS_THRESHOLD": 0.5,
    "WALK_EXTRAPOLATE_STEPS": 10,
    "LOOP_THRESHOLD": 3.0,
    "SURFACE_MIN_WALK_FRACTION": 0.6,
    "CENTROID_BIAS": 0.7,
    "SURFACE_LABEL_COLOR": (1.0, 1.0, 1.0),
    "SURFACE_PROBE_DIRECTIONS": 6,
    "SURFACE_PROBE_STEPS": 8,
    # Minimum dot(surface_normal, to_camera) to attempt a surface label.
    # 1.0 = dead-on, 0.0 = edge-on, <0 = away. Below threshold → flagpole fallback.
    # 0.3 ≈ 72° off from facing camera.
    "SURFACE_MIN_FACING": 0.5,

    # =====================================================================
    # FLAGPOLE LABEL SETTINGS
    # =====================================================================
    "FLAGPOLE_HEIGHT": 3.5,            # scaled from 35.0
    "FLAGPOLE_TEXT_SCALE": 0.5,
    "FLAGPOLE_COLOR": (1.0, 1.0, 1.0),
    "FLAGPOLE_LINE_COLOR": (1.0, 1.0, 1.0),
    "FLAGPOLE_LINE_WIDTH": 1.5,
    "FLAGPOLE_DOT_RADIUS": 0.15,       # scaled from 1.2
    "FLAGPOLE_DOT_COLOR": (1.0, 1.0, 1.0),
    "FLAGPOLE_FAN_SPACING": 2.0,       # scaled from 15.0

    # =====================================================================
    # BILLBOARD LABEL SETTINGS
    # =====================================================================
    "BILLBOARD_OFFSET":20.0,           # scaled from 80.0
    "BILLBOARD_TEXT_SCALE": 0.5,
    "BILLBOARD_COLOR": (1.0, 1.0, 1.0),

    # =====================================================================
    # TEXT RENDERING
    # =====================================================================
    "TEXT_EXTRUSION_DEPTH": 0.2,      # Z-depth for vtkLinearExtrusionFilter
}