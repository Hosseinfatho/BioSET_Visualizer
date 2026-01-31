from __future__ import annotations

# TODO: import from config.py
DEFAULT_CHANNEL_COLORS = (
    "#00FFFF",  # Cyan
    "#FF00FF",  # Magenta
    "#FFFF00",  # Yellow
    "#FF0000",  # Red
    "#00FF00",  # Green
    "#0000FF",  # Blue
)

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
    state.setdefault("right_drawer_open", True)
    
    # UpSet plot
    state.setdefault("upset_click", None)
    
    # Channels - all channels
    # {id: int, name: str, color: str}
    state.setdefault("channels", [])
    
    # Active channels (ids)
    state.setdefault("active_channels", [])

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