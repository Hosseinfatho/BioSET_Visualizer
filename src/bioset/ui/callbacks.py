from __future__ import annotations

from .state import get_channel_color

def register_callbacks(ctrl, state, view, streamer=None):
    """Register all controller methods."""

    _refs = {
        "streamer": None,
        "view": view,
    }

    def set_streamer(streamer):
        """Set the streamer reference """
        _refs["streamer"] = streamer
        print(f"[callbacks] Streamer set: {streamer}")
    
    def load_data():
        """Load data from zarr_url and metadata_url."""
        if state.data_loading:
            return
        state.data_loading = True
        print(f"[callbacks] Loading data...")
        print(f"[callbacks]   Zarr URL: {state.zarr_url}")
        print(f"[callbacks]   Metadata URL: {state.metadata_url}")
        
        try:
            # metadata parsing
            from bioset.metadata import parse_ome_metadata
            
            metadata = parse_ome_metadata(state.metadata_url)
            
            # voxel physical size
            state.physical_size_x = metadata.physical_size_x
            state.physical_size_y = metadata.physical_size_y
            state.physical_size_z = metadata.physical_size_z
            
            # channel list with color defaults
            channels = [
    {
        "id": ch.id,
        "name": ch.name,
        "color": get_channel_color(ch.id),
        "color_dialog": False,
        "range": [0, 100],
    }
    for ch in metadata.channels
]
            
            # state update
            state.channels = channels
            state.active_channels = []
            state.data_loaded = True
            
            print(f"[callbacks] Loaded {len(channels)} channels")
            print(f"[callbacks] Physical size: ({state.physical_size_x}, {state.physical_size_y}, {state.physical_size_z})")
            
        except Exception as e:
            print(f"[callbacks] Error loading data: {e}")
            import traceback
            traceback.print_exc()
            state.data_loaded = False
        finally:
            state.data_loading = False

    def toggle_channel(channel_id):
        """Toggle a channel's active state (add/remove from rendering)."""
        print(f"[callbacks] Toggle channel {channel_id}")
        
        active = list(state.active_channels)
        if channel_id in active:
            active.remove(channel_id)
            print(f"[callbacks] Deactivated channel {channel_id}")
        else:
            active.append(channel_id)
            print(f"[callbacks] Activated channel {channel_id}")
        
        state.active_channels = active
    
    def update_active_channels(active_channels):
        """Update which channels are being rendered."""
        print(f"[callbacks] Updating active channels: {active_channels}")
        
        streamer = _refs.get("streamer")
        if streamer is None:
            print(f"[callbacks] No streamer available yet")
            return
        
        # TODO: Tell streamer to update visible channels
        
        if _refs["view"]:
            _refs["view"].update()
    
    def reset_camera():
        """Reset the VTK camera to default view."""
        print(f"[callbacks] Resetting camera")
        streamer = _refs.get("streamer")
        if streamer and hasattr(streamer, 'renderer'):
            streamer.renderer.ResetCamera()
            streamer.renderer.ResetCameraClippingRange()
        if _refs["view"]:
            _refs["view"].update()
    
    def update_background_color(color_hex):
        """Update renderer background color."""
        print(f"[callbacks] Updating background color to {color_hex}")
        streamer = _refs.get("streamer")
        if streamer and hasattr(streamer, 'renderer'):
            color_hex = color_hex.lstrip('#')
            r = int(color_hex[0:2], 16) / 255.0
            g = int(color_hex[2:4], 16) / 255.0
            b = int(color_hex[4:6], 16) / 255.0
            streamer.renderer.SetBackground(r, g, b)
        if _refs["view"]:
            _refs["view"].update()
    
    def update_channel_color(channel_id, color_hex):
        """Update color of a channel."""
        print(f"[callbacks] Channel {channel_id} color: {color_hex}")
        
        channels = list(state.channels)
        for ch in channels:
            if ch["id"] == channel_id:
                ch["color"] = color_hex
                break
        state.channels = channels
    
    # def update_channel_range(channel_id, range_min, range_max):
    #     """Update intensity range of a channel."""
    #     print(f"[callbacks] Channel {channel_id} range: [{range_min}, {range_max}]")
    #     # TODO: Update transfer function in streamer
    
    # Bind to controller
    ctrl.set_streamer = set_streamer
    ctrl.load_data = load_data
    ctrl.toggle_channel = toggle_channel
    ctrl.update_active_channels = update_active_channels
    ctrl.reset_camera = reset_camera
    ctrl.update_background_color = update_background_color
    ctrl.update_channel_color = update_channel_color
    # ctrl.update_channel_range = update_channel_range