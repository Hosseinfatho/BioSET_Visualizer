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
    
    # Bookmark (saved views / snapshots: name, open, new form)
    state.setdefault("bookmark_open", False)
    state.setdefault("bookmark_snapshot_names", [])
    state.setdefault("bookmark_selected_name", "Name")
    state.setdefault("bookmark_form_dialog", False)
    state.setdefault("bookmark_form_name", "")
    state.setdefault("bookmark_form_description", "")
    state.setdefault("bookmark_form_new_comment", "")
    state.setdefault("bookmark_display_snapshot", None)
    state.setdefault("bookmark_current_view_index", 0)
    state.setdefault("bookmark_form_minimized", False)
    state.setdefault("bookmark_dataset_id", "default")   # per-dataset folder under recordings
    state.setdefault("bookmark_edit_title", "")
    state.setdefault("bookmark_edit_description", "")
    state.setdefault("bookmark_edit_comment", "")
    state.setdefault("bookmark_export_screenshot_dialog", False)
    state.setdefault("bookmark_export_screenshot_name", "")
    state.setdefault("bookmark_export_screenshot_caption", "")
    state.setdefault("bookmark_capture_from_nov", False)  # True when saving from NOV popup

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
    state.setdefault("current_hierarchy_level", 2)  # Selected hierarchy (default coarse)

    # Heatmap state
    state.setdefault("heatmap_visible", True)
    state.setdefault("heatmap_color", "#FFFFFF")  # White
    state.setdefault("heatmap_tile_count", 0)

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
    
    # NOV (Next Best View) — 2D rectangle overlay (resize/pan); depth = 2× diagonal of rect in world
    state.setdefault("nov_show_rect", False)  # show 2D rectangle overlay on main view
    state.setdefault("nov_rect_x", 0.35)  # 0-1 left
    state.setdefault("nov_rect_y", 0.35)  # 0-1 bottom
    state.setdefault("nov_rect_w", 0.3)
    state.setdefault("nov_rect_h", 0.3)
    # Lens drag (Trame-only: client sets these via v_on; server commits on nov_dragging -> false)
    state.setdefault("nov_dragging", False)
    state.setdefault("nov_drag_start_rect_x", 0.35)
    state.setdefault("nov_drag_start_rect_y", 0.35)
    state.setdefault("nov_drag_start_client_x", 0)
    state.setdefault("nov_drag_start_client_y", 0)
    state.setdefault("nov_viewport_w", 1)
    state.setdefault("nov_viewport_h", 1)
    state.setdefault("nov_drag_delta_x", 0.0)
    state.setdefault("nov_drag_delta_y", 0.0)
    state.setdefault("nov_drag_end", "")  # "x,y,w,h" set on mouseup so server commits once
    state.setdefault("nov_drag_live_str", "")  # "x,y" during drag so lens position updates in UI
    state.setdefault("nov_drag_report", "")  # last few debug lines (server-side) to find drag problem
    state.setdefault("nov_open", False)
    state.setdefault("nov_drawing_box", False)
    state.setdefault("nov_lens_center", None)
    state.setdefault("nov_lens_length", 0.0)
    state.setdefault("nov_lens_width", 0.0)
    state.setdefault("nov_lens_depth", 0.0)
    state.setdefault("nov_dragging_corner", None)  # 0..7 when dragging a corner pin
    state.setdefault("nov_dragging_lens_center", False)  # True when right-drag inside lens to move it
    state.setdefault("nov_panel_visible", False)
    state.setdefault("nov_candidates", [])
    state.setdefault("nov_current_index", 0)
    state.setdefault("nov_view_index_display", "")  # e.g. "1/10"
    state.setdefault("nov_score_display", 0.0)
    state.setdefault("nov_sphere_xy", [])  # for SVG mini-map
    state.setdefault("nov_sphere_svg", "")
    state.setdefault("nov_popup_minimized", False)  # minimize NOV popup (header only)
    state.setdefault("nov_popup_open", False)  # True after "Set"; "Reset" only clears results inside, does not close
    state.setdefault("nov_selected_channels", [])  # Channel ids selected in NOV popup for top-10 entropy views
    state.setdefault("nov_has_results", False)  # True after Set computed candidates; drives single Set vs Reset button
    state.setdefault("nov_active_channel_items", [])  # [{id, name, color}] for active channels only (same as main scene)
    state.setdefault("nov_clicked_channel_id", None)  # set by client when ticking a channel checkbox; server reads to toggle
    state.setdefault("nov_scale_bar_label", "")  # e.g. "10 µm" for scale bar in NOV popup
    state.setdefault("nov_scale_bar_width_px", 0)  # pixel width of scale bar (updates with zoom)
    state.setdefault("nov_popup_width_px", 400)  # resizable NOV popup width (initial from box)
    state.setdefault("nov_popup_height_px", 300)  # resizable NOV popup height (initial from box)
    state.setdefault("nov_popup_size_str", "")  # "w,h" from client resize to update server

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
        if hasattr(ctrl, 'update_upset_data_local'):
            ctrl.update_upset_data_local()
        if hasattr(ctrl, 'update_bar_data_local'):
            ctrl.update_bar_data_local()
        if hasattr(ctrl, 'nov_recompute_scores_if_visible'):
            ctrl.nov_recompute_scores_if_visible()

    @state.change("channels")
    def on_channels_change(channels, **kwargs):
        """When channel list or per-channel range changes, update NOV scores if panel is open."""
        if hasattr(ctrl, 'nov_recompute_scores_if_visible'):
            ctrl.nov_recompute_scores_if_visible()

    @state.change("nov_selected_channels")
    def on_nov_selected_channels_change(nov_selected_channels, **kwargs):
        """When user checks/unchecks channels in NOV popup, show only selected channels in the NOV window."""
        if hasattr(ctrl, 'nov_update_visibility'):
            ctrl.nov_update_visibility()

    def _append_report(line: str):
        report = getattr(state, "nov_drag_report", "") or ""
        lines = (report + "\n" + line).strip().split("\n")
        state.nov_drag_report = "\n".join(lines[-5:])  # keep last 5 lines

    @state.change("nov_drag_live_str")
    def on_nov_drag_live_change(nov_drag_live_str, **kwargs):
        """During drag: update nov_rect_x/y from script so the lens moves in the UI (no 3D update)."""
        if not nov_drag_live_str:
            return
        print("[NOV] server received nov_drag_live_str:", nov_drag_live_str[:60])
        _append_report("live: " + nov_drag_live_str[:40])
        state.nov_drag_live_str = ""
        try:
            parts = nov_drag_live_str.strip().split(",")
            if len(parts) >= 2:
                side = float(getattr(state, "nov_rect_w", 0.3))
                side = max(0.05, min(0.9, side))
                x = max(0, min(1 - side, float(parts[0])))
                y = max(0, min(1 - side, float(parts[1])))
                state.nov_rect_x = x
                state.nov_rect_y = y
        except (TypeError, ValueError):
            pass

    @state.change("nov_drag_end")
    def on_nov_drag_end_change(nov_drag_end, **kwargs):
        """When client sets nov_drag_end to 'x,y,w,h' on mouseup, commit lens position and 3D."""
        if not nov_drag_end:
            return
        print("[NOV] server received nov_drag_end:", nov_drag_end)
        _append_report("end: " + nov_drag_end[:50])
        state.nov_drag_end = ""
        try:
            parts = nov_drag_end.strip().split(",")
            if len(parts) >= 4:
                x = max(0, min(1, float(parts[0])))
                y = max(0, min(1, float(parts[1])))
                w = max(0.05, min(0.9, float(parts[2])))
                h = max(0.05, min(0.9, float(parts[3])))
                if hasattr(ctrl, "nov_update_rect"):
                    ctrl.nov_update_rect(x, y, w, h)
                _append_report("ok x=%s y=%s" % (x, y))
        except (TypeError, ValueError) as ex:
            _append_report("err: " + str(ex))

    @state.change("nov_popup_size_str")
    def on_nov_popup_size_str_change(nov_popup_size_str, **kwargs):
        """When client sets nov_popup_size_str to 'w,h' after resizing the NOV popup, update state."""
        if not nov_popup_size_str:
            return
        try:
            parts = nov_popup_size_str.strip().split(",")
            if len(parts) >= 2:
                w = max(280, min(720, int(float(parts[0]))))
                h = max(200, min(420, int(float(parts[1]))))
                state.nov_popup_width_px = w
                state.nov_popup_height_px = h
            state.nov_popup_size_str = ""
        except (ValueError, IndexError):
            pass

    @state.change("current_dilation")
    def on_dilation_change(current_dilation, **kwargs):
        print(f"[state] Dilation changed: {current_dilation}")
        if hasattr(ctrl, 'update_heatmap'):
            ctrl.update_heatmap()
        if hasattr(ctrl, 'update_upset_data'):
            ctrl.update_upset_data()
        if hasattr(ctrl, 'update_bar_data'):
            ctrl.update_bar_data()

    @state.change("current_hierarchy_level")
    def on_hierarchy_change(current_hierarchy_level, **kwargs):
        print(f"[state] Hierarchy level changed: {current_hierarchy_level}")
        if hasattr(ctrl, 'update_heatmap'):
            ctrl.update_heatmap()
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

    @state.change("bookmark_open")
    def on_bookmark_open_change(bookmark_open, **kwargs):
        if bookmark_open and hasattr(ctrl, "bookmark_refresh_names"):
            ctrl.bookmark_refresh_names()

    @state.change("analysis_channels")
    def on_analysis_channels_change(analysis_channels, **kwargs):
        # Reset filtered lists when analysis changes
        state.upset_filtered_channels = list(analysis_channels)
        state.bar_filtered_channels = list(analysis_channels)
        state.upset_search = ""
        state.bar_search = ""
        