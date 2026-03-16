"""
All tunable parameters for the BioSET label placement system.

Distance and size values are scaled for BioSET world-space units
(physical_size * voxel_coords), which are typically ~10x smaller
than the cycif_mesh_labelling reference units.
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
    "INTERACTION_DISTANCE": 5.0,
    "INTERACTION_BILLBOARD_MIN_GAP": 2.0,
    "INTERACTION_BILLBOARD_OFFSET": 2.0,
    "INTERACTION_BILLBOARD_SCALE": 0.5,
    "INTERACTION_BILLBOARD_COLOR": (1.0, 0.8, 0.5),
    "INTERACTION_FLAGPOLE_HEIGHT": 3.0,
    "INTERACTION_FLAGPOLE_SCALE": 0.4,
    "INTERACTION_FLAGPOLE_COLOR": (1.0, 0.8, 0.5),

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
    # =====================================================================
    "HIERARCHY_OVERVIEW_DIST": 400.0,
    "HIERARCHY_REGION_DIST": 200.0,

    "CLUSTER_LABEL_BASE_SCALE": 1.0,
    "CLUSTER_EXTENT_SCALE_FACTOR": 50.0,
    "CLUSTER_LABEL_COLOR": (1.0, 1.0, 0.8),
    "CLUSTER_LABEL_OFFSET": 50.0,

    # =====================================================================
    # ZOOM-BASED LABEL TYPE HEURISTIC
    #
    #   dist > FAR_THRESHOLD   → BILLBOARD
    #   MID < dist <= FAR      → FLAGPOLE
    #   dist <= MID_THRESHOLD  → SURFACE
    # =====================================================================
    "FAR_THRESHOLD": 400.0,
    "MID_THRESHOLD": 200.0,

    "MAX_VISIBLE_LABELS": 50,
    "LABEL_SCREEN_PADDING": 15,

    # =====================================================================
    # SURFACE LABEL SETTINGS
    # =====================================================================
    "DILATION_AMOUNT": 5.0,
    "SMOOTH_ITERATIONS": 100,
    "SURFACE_LABEL_HEIGHT": 1.5,
    "WALK_STEP": 0.3,
    "WALK_STEPS": 30,
    "WALK_NORMAL_COS_THRESHOLD": 0.5,
    "LOOP_THRESHOLD": 0.3,
    "CENTROID_BIAS": 0.7,
    "SURFACE_LABEL_COLOR": (1.0, 1.0, 1.0),

    # =====================================================================
    # FLAGPOLE LABEL SETTINGS
    # =====================================================================
    "FLAGPOLE_HEIGHT": 5.0,
    "FLAGPOLE_TEXT_SCALE": 0.5,
    "FLAGPOLE_COLOR": (1.0, 1.0, 1.0),
    "FLAGPOLE_LINE_COLOR": (1.0, 1.0, 1.0),
    "FLAGPOLE_LINE_WIDTH": 1.5,
    "FLAGPOLE_DOT_RADIUS": 0.8,
    "FLAGPOLE_DOT_COLOR": (1.0, 1.0, 1.0),
    "FLAGPOLE_FAN_SPACING": 15.0,

    # =====================================================================
    # BILLBOARD LABEL SETTINGS
    # =====================================================================
    "BILLBOARD_OFFSET": 40.0,
    "BILLBOARD_TEXT_SCALE": 4.0,
    "BILLBOARD_COLOR": (1.0, 1.0, 1.0),
}
