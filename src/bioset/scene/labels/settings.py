"""
All tunable parameters for the BioSET label placement system.
"""

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
    "INTERACTION_DISTANCE": 10.0,
    "INTERACTION_BILLBOARD_MIN_GAP": 5.0,
    "INTERACTION_BILLBOARD_OFFSET": 5.0,
    "INTERACTION_BILLBOARD_SCALE": 3.0,
    "INTERACTION_BILLBOARD_COLOR": (1.0, 0.75, 0.55),
    "INTERACTION_FLAGPOLE_HEIGHT": 7.0,
    "INTERACTION_FLAGPOLE_SCALE": 1.0,
    "INTERACTION_FLAGPOLE_COLOR": (1.0, 0.75, 0.55),
    "INTERACTION_CLUSTER_RADIUS": 10.0,  # was 6 — merge nearby interactions more
    "MAX_INTERACTION_LABELS": 0,         # was 4 — at most 2 on screen

    # =====================================================================
    # LABEL DISPLAY
    # =====================================================================
    "SHOW_LABELS": True,

    # =====================================================================
    # HIERARCHICAL LABEL LEVELS
    # =====================================================================
    "HIERARCHY_OVERVIEW_DIST": 500.0,
    "HIERARCHY_REGION_DIST": 200.0,
    "CLUSTER_LABEL_BASE_SCALE": 1.0,
    "CLUSTER_EXTENT_SCALE_FACTOR": 50.0,
    "CLUSTER_LABEL_COLOR": (0.95, 0.92, 0.8),
    "CLUSTER_LABEL_OFFSET": 40.0,
    "MAX_CLUSTER_LABELS": 6,            # was 12

    # =====================================================================
    # ZOOM-BASED LABEL TYPE HEURISTIC
    # =====================================================================
    "FAR_THRESHOLD": 400.0,
    "MID_THRESHOLD": 160.0,
    "MAX_VISIBLE_LABELS": 4,            # was 10 — biggest declutter lever
    "LABEL_SCREEN_PADDING": 25,         # was 15 — more breathing room

    # =====================================================================
    # SURFACE LABEL SETTINGS
    # =====================================================================
    "DILATION_AMOUNT": 5.0,
    "SMOOTH_ITERATIONS": 100,
    "SURFACE_LABEL_HEIGHT": 1.8,
    "SURFACE_HEIGHT_FACTOR": 2.2,
    "SURFACE_MIN_HEIGHT": 1.8,
    "SURFACE_MAX_HEIGHT": 3.0,
    "WALK_STEP": 3.0,
    "WALK_STEPS": 30,
    "WALK_NORMAL_COS_THRESHOLD": 0.5,
    "WALK_EXTRAPOLATE_STEPS": 10,
    "LOOP_THRESHOLD": 3.0,
    "SURFACE_MIN_WALK_FRACTION": 0.6,
    "CENTROID_BIAS": 0.7,
    "SURFACE_LABEL_COLOR": (0.92, 0.9, 0.82),
    "SURFACE_PROBE_DIRECTIONS": 6,
    "SURFACE_PROBE_STEPS": 8,
    "SURFACE_MIN_FACING": 0.8,

    # =====================================================================
    # FLAGPOLE LABEL SETTINGS
    # =====================================================================
    "FLAGPOLE_HEIGHT": 6.0,
    "FLAGPOLE_TEXT_SCALE": 1.2,
    "FLAGPOLE_COLOR": (0.92, 0.9, 0.82),
    "FLAGPOLE_LINE_COLOR": (0.6, 0.58, 0.5),
    "FLAGPOLE_LINE_WIDTH": 1.5,
    "FLAGPOLE_DOT_RADIUS": 0.15,
    "FLAGPOLE_DOT_COLOR": (0.92, 0.9, 0.82),
    "FLAGPOLE_FAN_SPACING": 3.0,        # was 2 — more spread

    # =====================================================================
    # BILLBOARD LABEL SETTINGS
    # =====================================================================
    "BILLBOARD_OFFSET": 15.0,
    "BILLBOARD_TEXT_SCALE": 0.5,
    "BILLBOARD_COLOR": (0.92, 0.9, 0.82),

    # =====================================================================
    # CO-LOCALIZATION LABEL SIZE OVERRIDES
    # =====================================================================
    "COLOC_FLAGPOLE_HEIGHT": 8.0,
    "COLOC_FLAGPOLE_TEXT_SCALE": 1.4,
    "COLOC_BILLBOARD_TEXT_SCALE": 1.5,
    "COLOC_BILLBOARD_OFFSET": 40.0,
    "COLOC_SURFACE_HEIGHT_FACTOR": 0.35,
    "COLOC_SURFACE_MIN_HEIGHT": 1.2,
    "COLOC_SURFACE_MAX_HEIGHT": 4.5,
    "COLOC_LABEL_COLOR": (1.0, 0.92, 0.55),

    # =====================================================================
    # TEXT RENDERING
    # =====================================================================
    "TEXT_EXTRUSION_DEPTH": 0.1,
}