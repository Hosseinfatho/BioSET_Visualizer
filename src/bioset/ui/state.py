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
    state.trame__scripts = list(state.trame__scripts) + [
        "https://cdn.jsdelivr.net/npm/d3@7",
        "assets/upsetjs.umd.production.min.js",
    ]
    
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
    state.setdefault("upset_data", [])
    state.setdefault("upset_data_local", [])
    state.setdefault("upset_selection", None)
    state.setdefault("upset_offset", 0)
    state.setdefault("upset_limit", 7)

    # Bar chart
    state.setdefault("bar_data", [])
    state.setdefault("bar_data_local", [])
    state.setdefault("bar_offset", 0)
    state.setdefault("bar_limit", 10)

    # Expanded View States
    state.setdefault("upset_expanded_offset", 0)
    state.setdefault("upset_expanded_limit", 40)
    state.setdefault("bar_expanded_offset", 0)
    state.setdefault("bar_expanded_limit", 50)
    
    # View mode toggles
    state.setdefault("upset_view_mode", "global")  # "global" or "local"
    state.setdefault("bar_view_mode", "global")  # "global" or "local"
    
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
    state.setdefault("current_hierarchy_level", 3)  # Selected hierarchy (default overview)

    # Heatmap state
    state.setdefault("heatmap_visible", True)
    state.setdefault("heatmap_color", "#FFFFFF")  # White
    state.setdefault("heatmap_tile_count", 0)
    state.setdefault("heatmap_combination", [])  # Currently selected combination (list of channel names)
    state.setdefault("heatmap_available_combinations", [])  # Available combos for active channels
    state.setdefault("heatmap_combo_index", None)  # Selected index in combination list

    # UpSet Plot filtering
    state.setdefault("upset_selected_channels", [])  # Channels to include in UpSet
    state.setdefault("upset_search", "")
    state.setdefault("upset_filtered_channels", []) # Channels shown in filter list
    state.setdefault("upset_filter_dialog", False)
    state.setdefault("upset_expanded", False)

    # Bar Plot filtering
    state.setdefault("bar_selected_channels", [])  # Channels to include in Bar
    state.setdefault("bar_search", "")
    state.setdefault("bar_filtered_channels", []) # Channels shown in filter list
    state.setdefault("bar_filter_dialog", False)
    state.setdefault("bar_expanded", False)
    
    # Chatbot state
    state.setdefault("chatbot_panel_open", None)  # None = closed, 0 = open
    state.setdefault("chatbot_authenticated", False)
    state.setdefault("chatbot_messages", [])  # List of {role: str, content: str}
    state.setdefault("chatbot_input", "")
    state.setdefault("chatbot_loading", False)

def get_channel_color(index: int) -> str:
    """Get default color for a channel by index."""
    return DEFAULT_CHANNEL_COLORS[index % len(DEFAULT_CHANNEL_COLORS)]

def register_state_change_handlers(state, ctrl):
    """Register all @state.change handlers."""
    
    @state.change("upset_click")
    def on_upset_click(upset_click, **kwargs):
        if upset_click:
            print(f"[state] UpSet clicked: {upset_click}")
            set_names = upset_click.get("sets", [])

            name_to_id = {ch["name"]: ch["id"] for ch in state.channels}
            new_active = [name_to_id[name] for name in set_names if name in name_to_id]

            if not new_active:
                return

            state.active_channels = new_active

            visible = list(state.visible_channel_ids)
            changed = False
            for ch_id in new_active:
                if ch_id not in visible:
                    visible.append(ch_id)
                    changed = True
            if changed:
                state.visible_channel_ids = visible
    
    @state.change("bg_color")
    def on_bg_color_change(bg_color, **kwargs):
        if hasattr(ctrl, 'update_background_color'):
            ctrl.update_background_color(bg_color)
    

    @state.change("active_channels")
    def on_active_channels_change(active_channels, **kwargs):
        print(f"[state] Active channels changed: {active_channels}")
        if hasattr(ctrl, 'update_active_channels'):
            ctrl.update_active_channels(active_channels)
        if hasattr(ctrl, 'update_heatmap_combinations'):
            ctrl.update_heatmap_combinations()
        if hasattr(ctrl, 'update_upset_data_local'):
            ctrl.update_upset_data_local()
        if hasattr(ctrl, 'update_bar_data_local'):
            ctrl.update_bar_data_local()
            
    @state.change("heatmap_visible")
    def on_heatmap_visible_change(heatmap_visible, **kwargs):
        print(f"[state] Heatmap visible changed: {heatmap_visible}")
        if hasattr(ctrl, 'update_heatmap'):
            ctrl.update_heatmap()
            
    @state.change("heatmap_combination")
    def on_heatmap_combination_change(heatmap_combination, **kwargs):
        print(f"[state] Heatmap combination changed: {heatmap_combination}")
        if hasattr(ctrl, 'update_heatmap'):
            ctrl.update_heatmap()

    @state.change("current_dilation")
    def on_dilation_change(current_dilation, **kwargs):
        print(f"[state] Dilation changed: {current_dilation}")
        if hasattr(ctrl, 'update_heatmap_combinations'):
            ctrl.update_heatmap_combinations()
        if hasattr(ctrl, 'update_upset_data'):
            ctrl.update_upset_data()
        if hasattr(ctrl, 'update_bar_data'):
            ctrl.update_bar_data()

    @state.change("heatmap_auto_level")
    def on_heatmap_auto_level_change(heatmap_auto_level, **kwargs):
        if hasattr(ctrl, 'set_heatmap_lod_auto_mode'):
            ctrl.set_heatmap_lod_auto_mode(heatmap_auto_level)
        # Switching to manual: immediately re-query at the current level
        if not heatmap_auto_level and hasattr(ctrl, 'update_heatmap'):
            ctrl.update_heatmap()

    @state.change("current_hierarchy_level")
    def on_hierarchy_change(current_hierarchy_level, **kwargs):
        print(f"[state] Hierarchy level changed: {current_hierarchy_level}")
        if state.heatmap_auto_level:
            return
        if hasattr(ctrl, 'update_heatmap_combinations'):
            ctrl.update_heatmap_combinations()
        if hasattr(ctrl, 'update_upset_data'):
            ctrl.update_upset_data()
        if hasattr(ctrl, 'update_bar_data'):
            ctrl.update_bar_data()

    @state.change("upset_data")
    def on_upset_data_change(upset_data, **kwargs):
        state.upset_offset = 0
        state.upset_expanded_offset = 0
        if hasattr(ctrl, 'update_upset_data_local'):
            ctrl.update_upset_data_local()

    @state.change("upset_view_mode")
    def on_upset_view_mode_change(upset_view_mode, **kwargs):
        state.upset_offset = 0
        state.upset_expanded_offset = 0

    @state.change("bar_data")
    def on_bar_data_change(bar_data, **kwargs):
        state.bar_offset = 0
        state.bar_expanded_offset = 0
        if hasattr(ctrl, 'update_bar_data_local'):
            ctrl.update_bar_data_local()

    @state.change("bar_view_mode")
    def on_bar_view_mode_change(bar_view_mode, **kwargs):
        state.bar_offset = 0
        state.bar_expanded_offset = 0

    @state.change("upset_selected_channels")
    def on_upset_selected_channels_change(upset_selected_channels, **kwargs):
        print(f"[state] UpSet selected channels changed: {len(upset_selected_channels)} channels")
        if hasattr(ctrl, 'update_upset_data'):
            ctrl.update_upset_data()

    @state.change("bar_selected_channels")
    def on_bar_selected_channels_change(bar_selected_channels, **kwargs):
        print(f"[state] Bar selected channels changed: {len(bar_selected_channels)} channels")
        if hasattr(ctrl, 'update_bar_data'):
            ctrl.update_bar_data()

    def _filter_channels(channels, search_term):
        if not search_term:
            return channels
        search_term = search_term.lower()
        return [c for c in channels if search_term in c.lower()]

    @state.change("upset_search")
    def on_upset_search_change(upset_search, **kwargs):
        state.upset_filtered_channels = _filter_channels(state.analysis_channels, upset_search)

    @state.change("bar_search")
    def on_bar_search_change(bar_search, **kwargs):
        state.bar_filtered_channels = _filter_channels(state.analysis_channels, bar_search)

    @state.change("analysis_channels")
    def on_analysis_channels_change(analysis_channels, **kwargs):
        # Reset filtered lists when analysis changes
        state.upset_filtered_channels = list(analysis_channels)
        state.bar_filtered_channels = list(analysis_channels)
        state.upset_search = ""
        state.bar_search = ""
        