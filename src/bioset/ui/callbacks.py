from __future__ import annotations

def register_callbacks(ctrl, state, view, streamer=None):
    """Register all controller methods."""
    
    def load_data():
        """Load data from zarr_url and metadata_url."""
        if state.data_loading:
            return
        
        state.data_loading = True
        print(f"[callbacks] Loading data...")
        print(f"[callbacks]   Zarr URL: {state.zarr_url}")
        print(f"[callbacks]   Metadata URL: {state.metadata_url}")
        
        try:
            # TODO: Actually load data
            # 1. Parse metadata from state.metadata_url to get channel names
            # 2. Update state.channels with discovered channels
            # 3. Initialize/update streamer with state.zarr_url
            
            # For now, just simulate success
            state.data_loaded = True
            print(f"[callbacks] Data loaded successfully")
            
        except Exception as e:
            print(f"[callbacks] Error loading data: {e}")
            state.data_loaded = False
        finally:
            state.data_loading = False
    
    def reset_camera():
        """Reset the VTK camera to default view."""
        print(f"[callbacks] Resetting camera")
        if streamer and hasattr(streamer, 'renderer'):
            streamer.renderer.ResetCamera()
            streamer.renderer.ResetCameraClippingRange()
        if view:
            view.update()
    
    def update_background_color(color_hex):
        """Update renderer background color."""
        print(f"[callbacks] Updating background color to {color_hex}")
        if streamer and hasattr(streamer, 'renderer'):
            # Convert hex to RGB (0-1 range)
            color_hex = color_hex.lstrip('#')
            r = int(color_hex[0:2], 16) / 255.0
            g = int(color_hex[2:4], 16) / 255.0
            b = int(color_hex[4:6], 16) / 255.0
            streamer.renderer.SetBackground(r, g, b)
        if view:
            view.update()
    
    def toggle_channel_visibility(channel_id, visible):
        """Toggle visibility of a channel."""
        print(f"[callbacks] Channel {channel_id} visibility: {visible}")
        # TODO: Update volume visibility in streamer
    
    def update_channel_color(channel_id, color_hex):
        """Update color of a channel."""
        print(f"[callbacks] Channel {channel_id} color: {color_hex}")
        # TODO: Update volume color/transfer function in streamer
    
    def update_channel_range(channel_id, range_min, range_max):
        """Update intensity range of a channel."""
        print(f"[callbacks] Channel {channel_id} range: [{range_min}, {range_max}]")
        # TODO: Update transfer function in streamer
    
    # Bind to controller
    ctrl.load_data = load_data
    ctrl.reset_camera = reset_camera
    ctrl.update_background_color = update_background_color
    ctrl.toggle_channel_visibility = toggle_channel_visibility
    ctrl.update_channel_color = update_channel_color
    ctrl.update_channel_range = update_channel_range