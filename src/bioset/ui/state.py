from __future__ import annotations

# TODO: import from config.py
DEFAULT_CHANNEL_COLORS = (
    "#FFFFFF", # White
)

# Swatches for VColorPicker 
DEFAULT_COLOR_SWATCHES = [
    ["#FF0000", "#00FFFF", "#FFFFFF"],
    ["#00FF00", "#FF00FF", "#808080"],
    ["#0000FF", "#FFFF00", "#000000"],
]

DEFAULT_NUM_CHANNELS = 3

def init_state(state):
    """Initialize all UI state with defaults."""
    
    # App metadata
    state.trame__title = "BioSET"
    state.trame__favicon = "assets/icon.jpg"
    
    # External scripts
    if not hasattr(state, 'trame__scripts') or state.trame__scripts is None:
        state.trame__scripts = []
    state.trame__scripts = list(state.trame__scripts) + ["https://unpkg.com/@upsetjs/bundle"]
    
    # Data sources
    state.setdefault("zarr_url", "https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/0")
    state.setdefault("metadata_url", "https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/OME/METADATA.ome.xml")
    state.setdefault("data_loading", False)
    state.setdefault("data_loaded", False)
    
    # Left drawer
    state.setdefault("drawer", True)
    state.setdefault("drawer_mini", False)
    
    # Section toggles
    state.setdefault("data_open", True)
    state.setdefault("settings_open", False)
    state.setdefault("channels_open", True)
    
    # Settings
    state.setdefault("bg_color", "#000000")
    state.setdefault("bg_color_dialog", False)
    
    # Right drawer
    state.setdefault("right_drawer_open", False)
    
    # UpSet plot
    state.setdefault("upset_click", None)
    
    # Channels - all channels
    # {id: int, name: str, color: str}
    state.setdefault("channels", [])
    
    # Active channels (ids)
    state.setdefault("active_channels", [])
    state.setdefault("visible_channel_ids", []) # channels shown in the list (first 3 and more if added)
    state.setdefault("default_num_channels", DEFAULT_NUM_CHANNELS)
    
    # default color picker swatches (nested array for VColorPicker)
    state.setdefault("color_swatches", DEFAULT_COLOR_SWATCHES)

    # bioset analysis file loading
    state.setdefault("analysis_loaded", False)
    state.setdefault("analysis_loading", False)
    state.setdefault("analysis_file_name", "")

    # Analysis metadata (from .bioset file)
    state.setdefault("analysis_channels", [])  # Channel names from analysis
    state.setdefault("analysis_dilation_amounts", [])  # Available dilations
    state.setdefault("analysis_hierarchy_levels", [])  # Available levels
    state.setdefault("analysis_volume_bounds", {})  # Spatial bounds

    # Current analysis settings
    state.setdefault("current_dilation", 0)  # Selected dilation amount
    state.setdefault("current_hierarchy_level", 2)  # Selected hierarchy (default coarse)

    # Heatmap state
    state.setdefault("heatmap_visible", True)
    state.setdefault("heatmap_color", "#FFFFFF")  # White
    state.setdefault("heatmap_tile_count", 0)

def get_channel_color(index: int) -> str:
    """Get default color for a channel by index."""
    return DEFAULT_CHANNEL_COLORS[index % len(DEFAULT_CHANNEL_COLORS)]

def register_state_change_handlers(state, ctrl):
    """Register all @state.change handlers."""
    
    @state.change("upset_click")
    def on_upset_click(upset_click, **kwargs):
        if upset_click:
            print(f"[state] UpSet clicked: {upset_click}")
            # TODO: Handle selection - highlight in VTK view
    
    @state.change("bg_color")
    def on_bg_color_change(bg_color, **kwargs):
        if hasattr(ctrl, 'update_background_color'):
            ctrl.update_background_color(bg_color)
    
    @state.change("active_channels")
    def on_active_channels_change(active_channels, **kwargs):
        print(f"[state] Active channels changed: {active_channels}")
        if hasattr(ctrl, 'update_active_channels'):
            ctrl.update_active_channels(active_channels)
        if hasattr(ctrl, 'update_heatmap'):
            ctrl.update_heatmap()

    @state.change("current_dilation")
    def on_dilation_change(current_dilation, **kwargs):
        print(f"[state] Dilation changed: {current_dilation}")
        if hasattr(ctrl, 'update_heatmap'):
            ctrl.update_heatmap()

    @state.change("current_hierarchy_level")
    def on_hierarchy_change(current_hierarchy_level, **kwargs):
        print(f"[state] Hierarchy level changed: {current_hierarchy_level}")
        if hasattr(ctrl, 'update_heatmap'):
            ctrl.update_heatmap()