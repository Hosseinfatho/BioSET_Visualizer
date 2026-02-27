from __future__ import annotations

from bioset.llm import BiomniClient
from .state import get_channel_color


def register_callbacks(ctrl, state, view, streamer=None):
    """Register all controller methods."""
    
    def _hex_to_rgb_tuple(color_hex: str):
        """Convert '#RRGGBB' to (r, g, b) floats in [0,1]."""
        color_hex = color_hex.lstrip("#")
        r = int(color_hex[0:2], 16) / 255.0
        g = int(color_hex[2:4], 16) / 255.0
        b = int(color_hex[4:6], 16) / 255.0
        return (r, g, b)
    
    _refs = {
        "streamer": None,
        "view": view,
        "analysis_loader": None,  
        "heatmap": None,
        "mesh_manager": None,
    }

    def set_streamer(streamer):
        """Set the streamer reference."""
        _refs["streamer"] = streamer
        print(f"[callbacks] Streamer set: {streamer}")

    def set_heatmap(heatmap):
        """Set the heatmap renderer reference."""
        _refs["heatmap"] = heatmap
        print(f"[callbacks] Heatmap renderer set: {heatmap}")
        
    def set_mesh_manager(mesh_manager):
        """Set the mesh manager reference."""
        _refs["mesh_manager"] = mesh_manager
        print(f"[callbacks] Mesh manager set: {mesh_manager}"
              f" (available={mesh_manager.is_available if mesh_manager else False})")
    
    def load_data():
        """Load data from zarr_url and metadata_url."""
        if state.data_loading:
            return
        state.data_loading = True
        print(f"[callbacks] Loading data...")
        print(f"[callbacks]   Zarr URL: {state.zarr_url}")
        print(f"[callbacks]   Metadata URL: {state.metadata_url}")
        
        try:
            from bioset.metadata import parse_ome_metadata
            
            metadata = parse_ome_metadata(state.metadata_url)
            
            state.physical_size_x = metadata.physical_size_x
            state.physical_size_y = metadata.physical_size_y
            state.physical_size_z = metadata.physical_size_z
            
            streamer = _refs.get("streamer")
            if streamer:
                streamer.set_zarr_url(state.zarr_url)
                streamer.set_spacing(
                    metadata.physical_size_x,
                    metadata.physical_size_y,
                    metadata.physical_size_z
                )
                
            mesh_mgr = _refs.get("mesh_manager")
            if mesh_mgr:
                mesh_mgr.update_spacing(
                    metadata.physical_size_x,
                    metadata.physical_size_y,
                    metadata.physical_size_z
                )
            
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
            
            state.channels = channels
            state.active_channels = []
            initial_visible = channels if len(channels) < state.default_num_channels else [ch["id"] for ch in channels[:state.default_num_channels]]
            state.visible_channel_ids = initial_visible
            state.data_loaded = True
            
            print(f"[callbacks] Loaded {len(channels)} channels")
            print(f"[callbacks] Physical size: ({state.physical_size_x}, {state.physical_size_y}, {state.physical_size_z})")
            
            if streamer:
                streamer.renderer.ResetCamera()
                streamer.renderer.ResetCameraClippingRange()
            if _refs["view"]:
                _refs["view"].update()
            
        except Exception as e:
            print(f"[callbacks] Error loading data: {e}")
            import traceback
            traceback.print_exc()
            state.data_loaded = False
        finally:
            state.data_loading = False

    def clear_data():
        print(f"[callbacks] Clearing data...")
        
        streamer = _refs.get("streamer")
        heatmap = _refs.get("heatmap")
        
        if streamer:
            for channel_id in list(state.active_channels):
                streamer.deactivate_channel(channel_id)
            streamer._active_channels.clear()
            streamer._channel_colors.clear()
            streamer._channel_tfs.clear()
            streamer.volumes.clear()
            streamer.mappers.clear()
            streamer.state.clear()
            streamer._last_component = None
            streamer.renderer.ResetCamera()
            streamer.renderer.ResetCameraClippingRange() 
            
        if heatmap:
            heatmap.clear() 
            
        mesh_mgr = _refs.get("mesh_manager")
        if mesh_mgr:
            mesh_mgr.clear()  
            
        if _refs["analysis_loader"]:
            _refs["analysis_loader"].close()
            _refs["analysis_loader"] = None
        
        state.channels = []
        state.active_channels = []
        state.visible_channel_ids = []
        state.data_loaded = False
        
        state.analysis_loaded = False
        state.analysis_file_name = ""
        state.analysis_channels = []
        state.analysis_dilation_amounts = []
        state.analysis_hierarchy_levels = []
        state.analysis_volume_bounds = {}
        state.heatmap_tile_count = 0
        
        state.right_drawer_open = False
    
        if _refs["view"]:
            _refs["view"].update()
        
        print(f"[callbacks] Data cleared successfully")
        
    def add_channel_to_visible(channel_id):
        print(f"[callbacks] Adding channel {channel_id} to visible list")
        visible = list(state.visible_channel_ids)
        if channel_id not in visible:
            visible.append(channel_id)
            state.visible_channel_ids = visible

    def remove_channel_from_visible(channel_id):
        print(f"[callbacks] Removing channel {channel_id} from visible list")
        
        visible = list(state.visible_channel_ids)
        if channel_id in visible:
            visible.remove(channel_id)
            state.visible_channel_ids = visible
        
        if channel_id in state.active_channels:
            active = list(state.active_channels)
            active.remove(channel_id)
            state.active_channels = active

    def load_analysis_file(file_info):
        """
        Load analysis results from uploaded .bioset file.
        
        Args:
            file_info: File info dict from trame file upload containing 'content' (base64) and 'name'
        """
        if state.analysis_loading:
            return
        
        state.analysis_loading = True
        print(f"[callbacks] Loading analysis file...")
        
        try:
            import base64
            from bioset.analysis import AnalysisLoader
            
            content = file_info.get("content", "")
            
            
            # file_bytes = base64.b64decode(content)
            if(isinstance(content, bytes)):
                file_bytes = content
            else:
                if "," in content:
                    content = content.split(",", 1)[1]
                file_bytes = base64.b64decode(content)
                
            file_name = file_info.get("name", "unknown.bioset")
            
            print(f"[callbacks] File: {file_name}, size: {len(file_bytes)} bytes")
            
            if _refs["analysis_loader"] is None:
                _refs["analysis_loader"] = AnalysisLoader()
            
            loader = _refs["analysis_loader"]
            metadata = loader.load_from_bytes(file_bytes)
            
            state.analysis_file_name = file_name
            state.analysis_channels = metadata.channels
            state.analysis_dilation_amounts = metadata.dilation_amounts
            state.analysis_hierarchy_levels = [lvl["level"] for lvl in metadata.hierarchy_levels]
            state.analysis_volume_bounds = metadata.volume_bounds
            
            if metadata.dilation_amounts:
                state.current_dilation = metadata.dilation_amounts[0]
            
            if metadata.hierarchy_levels:
                state.current_hierarchy_level = metadata.hierarchy_levels[len(metadata.hierarchy_levels)-1]["level"]
            
            # Initialize plot channel selections with all channels
            state.upset_selected_channels = [ch for ch in state.analysis_channels]
            state.bar_selected_channels = [ch for ch in state.analysis_channels]
            
            state.analysis_loaded = True
            state.right_drawer_open = True  
            
            print(f"[callbacks] Analysis loaded: {len(metadata.channels)} channels, "
                  f"dilations={metadata.dilation_amounts}, levels={state.analysis_hierarchy_levels}")
            
            update_heatmap()
            update_upset_data()
            update_bar_data()
            
            if _refs["view"]:
                _refs["view"].update()
            
        except Exception as e:
            print(f"[callbacks] Error loading analysis: {e}")
            import traceback
            traceback.print_exc()
            state.analysis_loaded = False
        finally:
            state.analysis_loading = False

    def toggle_channel(channel_id):
        """Toggle a channel's active state (add/remove from rendering)."""
        print(f"[callbacks] Toggle channel {channel_id}")
        
        active = list(state.active_channels)
        if channel_id in active:
            active.remove(channel_id)
        else:
            active.append(channel_id)
        
        state.active_channels = active
    
    def update_active_channels(active_channels):
        """Sync streamer state with UI state when active_channels changes."""
        print(f"[callbacks] Syncing active channels: {active_channels}")
        
        streamer = _refs.get("streamer")
        mesh_mgr = _refs.get("mesh_manager")
        
        if streamer is None:
            print(f"[callbacks] No streamer available yet")
            return
        
        currently_active = streamer.get_active_channels()
        new_active = set(active_channels)
        
        to_deactivate = currently_active - new_active
        for channel_id in to_deactivate:
            print(f"[callbacks] Deactivating channel {channel_id}")
            streamer.deactivate_channel(channel_id)
            if mesh_mgr:
                mesh_mgr.deactivate_channel_mesh(channel_id)
        
        to_activate = new_active - currently_active
        for channel_id in to_activate:
            color_hex = "#FFFFFF"
            for ch in state.channels:
                if ch["id"] == channel_id:
                    color_hex = ch["color"]
                    break
            print(f"[callbacks] Activating channel {channel_id} with color {color_hex}")
            streamer.activate_channel(channel_id, color_hex)
            
            # Also load mesh overlay (tile 4_0 for now)
            if mesh_mgr and mesh_mgr.is_available:
                color_rgb = _hex_to_rgb_tuple(color_hex)
                mesh_mgr.activate_channel_mesh(
                    channel_idx=channel_id,
                    color_rgb=color_rgb,
                    tile_x=6,
                    tile_y=1,
                    opacity=1.0,
                )
        
        if _refs["view"]:
            _refs["view"].update()

    def update_heatmap_combinations():
        """Update available heatmap combinations based on active channels.
        """
        loader = _refs.get("analysis_loader")
        
        if not loader or not loader.is_loaded:
            state.heatmap_available_combinations = []
            state.heatmap_combination = []
            return
        
        active_channel_names = []
        for ch_id in (state.active_channels or []):
            for ch in (state.channels or []):
                if ch["id"] == ch_id:
                    active_channel_names.append(ch["name"])
                    break
        
        if not active_channel_names:
            state.heatmap_available_combinations = []
            state.heatmap_combination = []
            return
        
        print(f"[callbacks] Updating heatmap combinations for active channels: {active_channel_names}")
        
        available = []
        
        for name in active_channel_names:
            available.append({
                "channels": [name],
                "label": name,
                "iou": None,  
            })
        
        if len(active_channel_names) >= 2:
            try:
                combinations = loader.get_filtered_combinations(
                    channel_filter=active_channel_names,
                    dilation=state.current_dilation,
                    hierarchy_level=state.current_hierarchy_level,
                    limit=50,
                    exact_match=False,
                )
                
                active_set = set(active_channel_names)
                for combo in combinations:
                    if len(combo.channels) >= 2 and set(combo.channels).issubset(active_set):
                        available.append({
                            "channels": combo.channels,
                            "label": " + ".join(combo.channels),
                            "iou": round(combo.iou, 4),
                        })
            except Exception as e:
                print(f"[callbacks] Error querying heatmap combinations: {e}")
        
        state.heatmap_available_combinations = available
        
        print(f"[callbacks] Found {len(available)} heatmap combinations")
        
        # prefer selection of all active channels if available
        current = state.heatmap_combination or []
        current_set = set(current)

        active_set = set(active_channel_names)
        full_match = [c for c in available if set(c["channels"]) == active_set]
        if full_match:
            state.heatmap_combination = full_match[0]["channels"]
            update_heatmap()
            return
        
        if current and any(set(c["channels"]) == current_set for c in available):
            update_heatmap()
            return
        
        multi = [c for c in available if len(c["channels"]) >= 2]
        if multi:
            state.heatmap_combination = multi[0]["channels"]
            update_heatmap()
            return
        
        if available:
            state.heatmap_combination = available[0]["channels"]
        else:
            state.heatmap_combination = []
        update_heatmap()
            
    def update_heatmap():
        """Update heatmap visualization based on current state.
        
        Tiles use active_fraction (fraction of tile volume occupied by the
        channel/combination) to set the color-mapped opacity.
        """
        loader = _refs.get("analysis_loader")
        heatmap = _refs.get("heatmap")
        streamer = _refs.get("streamer")
        
        if not loader or not loader.is_loaded or not heatmap:
            print("[callbacks] Cannot update heatmap - loader or heatmap not ready")
            return
        
        if not state.heatmap_visible:
            print("[callbacks] Heatmap hidden")
            heatmap.clear()
            state.heatmap_tile_count = 0
            if _refs["view"]:
                _refs["view"].update()
            return
        
        selected_channel_names = state.heatmap_combination or []
        
        if not selected_channel_names:
            print("[callbacks] No heatmap combination selected - clearing heatmap")
            heatmap.clear()
            state.heatmap_tile_count = 0
            if _refs["view"]:
                _refs["view"].update()
            return
        
        print(f"[callbacks] Updating heatmap for combination: {selected_channel_names}")
        print(f"[callbacks]   dilation={state.current_dilation}, level={state.current_hierarchy_level}")
        
        tiles = loader.get_combination_tiles(
            channels=selected_channel_names,
            dilation=state.current_dilation,
            hierarchy_level=state.current_hierarchy_level,
        )
        
        if not tiles:
            print(f"[callbacks] No tiles found for this combination")
            heatmap.clear()
            state.heatmap_tile_count = 0
        else:
            print(f"[callbacks] Found {len(tiles)} tiles")
            
            spacing = (
                getattr(state, 'physical_size_x', 1.0),
                getattr(state, 'physical_size_y', 1.0),
                getattr(state, 'physical_size_z', 1.0),
            )
            
            from bioset.scene.heatmap import hex_to_rgb
            color = hex_to_rgb(state.heatmap_color)
            heatmap.update_tiles(tiles, spacing=spacing, color=color)
            state.heatmap_tile_count = len(tiles)
        
        if _refs["view"]:
            _refs["view"].update()
    
    def _filter_combinations_by_channel_selection(combinations, selected_channels):
        """
        Filter combinations to only include those whose channels are all
        within the selected set.
        """
        if len(selected_channels) == 0:
            return combinations

        filtered_combinations = []

        for combo in combinations:
            if any(ch in selected_channels for ch in combo.channels):
                filtered_combinations.append(combo)

        return filtered_combinations

    def update_upset_data():
        """Update UpSet plot data based on current analysis settings.
        
        Uses aggregated IoU across tiles, sorted descending.
        """
        loader = _refs.get("analysis_loader")
        
        if not loader or not loader.is_loaded:
            state.upset_data = []
            return

        print(f"[callbacks] Updating UpSet data: dilation={state.current_dilation}, level={state.current_hierarchy_level}")
        
        # Get all combinations from analysis (large limit), sorted by agg IoU desc
        combinations = loader.get_top_combinations(
            dilation=state.current_dilation,
            hierarchy_level=state.current_hierarchy_level,
            limit=1000,
            min_channels=2,
        )
        
        # Filter to selected channels
        #all_data = _filter_combinations_by_channel_selection(combinations, state.upset_selected_channels)

        mapped_combinations = []
        for combination in combinations:
            mapped_combinations.append({
                "channels": combination.channels,
                "iou": combination.iou,
            })

        state.upset_data = mapped_combinations
        
        print(f"[callbacks] UpSet data updated: {len(mapped_combinations)} total")

    def update_upset_data_local():
        """Update local UpSet data filtered by active channels."""
        loader = _refs.get("analysis_loader")
        
        if not loader or not loader.is_loaded:
            state.upset_data_local = []
            return
        
        # Get active channel names
        active_channel_ids = state.active_channels or []
        channels_list = state.channels or []
        active_channel_names = [
            ch["name"] for ch in channels_list if ch["id"] in active_channel_ids
        ]

        if not active_channel_names:
            state.upset_data_local = []
            print("[callbacks] UpSet local data cleared (no active channels)")
            return
        
        # Filter active channels by upset_selected_channels as well
        active_and_selected = [name for name in active_channel_names if name in state.upset_selected_channels]
        
        if not active_and_selected:
            state.upset_data_local = []
            print("[callbacks] UpSet local data cleared (no active channels in selected channels)")
            return

        print(f"[callbacks] Updating UpSet local data for channels: {active_channel_names}")
        
        try:
            combinations = loader.get_filtered_combinations(
                channel_filter=active_channel_names,
                dilation=state.current_dilation,
                hierarchy_level=state.current_hierarchy_level,
                limit=1000,
                exact_match=False,
            )
            
            # Post-filter by selected channels
            local_data = _filter_combinations_by_channel_selection(combinations, state.upset_selected_channels)

            mapped_combinations = []
            for combination in local_data:
                mapped_combinations.append({
                    "channels": combination.channels,
                    "iou": combination.iou,
                })

            state.upset_data_local = mapped_combinations

            print(f"[callbacks] UpSet local data updated: {len(mapped_combinations)} combinations")
        except Exception as e:
            print(f"[callbacks] Error updating local upset data: {e}")
            state.upset_data_local = []
    
    def update_bar_data():
        """Update bar chart data with coverage percentage per channel.
        
        Coverage % = (tiles with marker present) / (total tiles) * 100
        Sorted descending. Filtered to bar_selected_channels.
        """
        loader = _refs.get("analysis_loader")
        
        if not loader or not loader.is_loaded:
            print("[callbacks] Cannot update bar data - loader not ready")
            state.bar_data = []
            return
        
        print(f"[callbacks] Updating bar data: dilation={state.current_dilation}, level={state.current_hierarchy_level}")
        
        # Get coverage percentages from channel_stats table
        all_coverage = loader.get_channel_coverage(
            dilation=state.current_dilation,
            hierarchy_level=state.current_hierarchy_level,
        )
        
        # Filter to selected channels
        filtered = [
            (name, pct) for name, pct in all_coverage
            if name in state.bar_selected_channels
        ]
        
        state.bar_data = filtered
        
        print(f"[callbacks] Bar data updated: {len(filtered)} channels")
    
    def update_bar_data_local():
        """Update local bar data filtered to active channels only."""
        active_channel_ids = state.active_channels or []
        channels_list = state.channels or []
        active_channel_names = [
            ch["name"] for ch in channels_list if ch["id"] in active_channel_ids
        ]
        
        if not active_channel_names:
            state.bar_data_local = []
            print("[callbacks] Bar local data cleared (no active channels)")
            return
        
        # Filter bar_data to only include active AND selected channels
        local_bar_data = [
            (name, pct) for name, pct in state.bar_data
            if name in active_channel_names and name in state.bar_selected_channels
        ]
        
        state.bar_data_local = local_bar_data
        print(f"[callbacks] Bar local data updated: {len(local_bar_data)} channels")
    
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
            if len(color_hex) >= 6:
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
        
        streamer = _refs.get("streamer")
        if streamer and channel_id in state.active_channels:
            streamer.deactivate_channel(channel_id)
            streamer.activate_channel(channel_id, color_hex)
            
        mesh_mgr = _refs.get("mesh_manager")
        if mesh_mgr and channel_id in state.active_channels:
            mesh_mgr.update_channel_color(channel_id, _hex_to_rgb_tuple(color_hex))
            
    def on_channel_color_change(channel_id, color_value):
        """Handle color change from the color picker."""
        print(f"[callbacks] Raw color_value: {color_value}, type: {type(color_value)}")
        
        if isinstance(color_value, dict):
            if 'hexa' in color_value:
                color_hex = color_value['hexa']
            elif 'hex' in color_value:
                color_hex = color_value['hex']
            elif 'r' in color_value and 'g' in color_value and 'b' in color_value:
                r = int(color_value['r'])
                g = int(color_value['g'])
                b = int(color_value['b'])
                color_hex = f'#{r:02x}{g:02x}{b:02x}'
            elif 'rgba' in color_value:
                import re
                match = re.match(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', str(color_value['rgba']))
                if match:
                    r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
                    color_hex = f'#{r:02x}{g:02x}{b:02x}'
                else:
                    color_hex = '#FFFFFF'
            else:
                color_hex = '#FFFFFF'
        elif isinstance(color_value, str):
            color_hex = color_value
        else:
            color_hex = '#FFFFFF'
        
        if not color_hex.startswith('#'):
            color_hex = f'#{color_hex}'
        
        if len(color_hex) > 7:
            color_hex = color_hex[:7]
        
        color_hex = color_hex.upper()
        
        print(f"[callbacks] Channel {channel_id} color changed to: {color_hex}")

        mesh_mgr = _refs.get("mesh_manager")
        if mesh_mgr and channel_id in state.active_channels:
            mesh_mgr.update_channel_color(channel_id, _hex_to_rgb_tuple(color_hex))
        
        new_channels = []
        for ch in state.channels:
            if ch["id"] == channel_id:
                new_channels.append({**ch, "color": color_hex})
            else:
                new_channels.append({**ch})  
        state.channels = new_channels
        
        streamer = _refs.get("streamer")
        if streamer and channel_id in state.active_channels:
            streamer._channel_colors[channel_id] = streamer._hex_to_rgb(color_hex)
            
            if channel_id in streamer.volumes:
                from bioset.scene.volumes import build_histogram_tf
                
                vol = streamer.volumes[channel_id]
                mapper = streamer.mappers[channel_id]
                
                img = mapper.GetInput()
                if img:
                    tint_rgb = streamer._channel_colors[channel_id]
                    current_range = [0, 100]
                    for ch in state.channels:
                        if ch["id"] == channel_id:
                            current_range = ch.get("range", [0, 100])
                            break

                    if channel_id in streamer._channel_data_range:
                        from bioset.scene.volumes import build_tf_with_range
                        data_range = streamer._channel_data_range[channel_id]
                        color_tf, opacity_tf = build_tf_with_range(data_range, tuple(current_range), tint_rgb)
                    else:
                        from bioset.scene.volumes import build_histogram_tf
                        color_tf, opacity_tf = build_histogram_tf(img, tint_rgb=tint_rgb)
                    streamer._channel_tfs[channel_id] = (color_tf, opacity_tf)
                    
                    prop = vol.GetProperty()
                    prop.SetColor(color_tf)
                    prop.SetScalarOpacity(opacity_tf)
        
        if _refs["view"]:
            _refs["view"].update()
            
    def on_channel_range_change(channel_id, range_value):
        """Handle intensity range slider change."""
        print(f"[callbacks] Channel {channel_id} range changed to: {range_value}")
        
        new_channels = []
        for ch in state.channels:
            if ch["id"] == channel_id:
                new_channels.append({**ch, "range": range_value})
            else:
                new_channels.append({**ch})
        state.channels = new_channels
        
        streamer = _refs.get("streamer")
        if streamer and channel_id in state.active_channels:
            streamer.update_channel_intensity_range(channel_id, tuple(range_value))
        
        if _refs["view"]:
            _refs["view"].update()

    _refs["biomni_client"] = None
    
    def _get_biomni_client():
        """Get or create Biomni client."""
        if _refs["biomni_client"] is None:
            _refs["biomni_client"] = BiomniClient()
        return _refs["biomni_client"]
    
    def chatbot_login():
        """Handle chatbot login/authentication with Biomni."""
        print("[callbacks] Chatbot login requested")
        state.chatbot_loading = True
        
        try:
            client = _get_biomni_client()
            
            # Attempt login (credentials from environment variables)
            client.login()
            
            state.chatbot_authenticated = True
            state.chatbot_messages = []
            print("[callbacks] Chatbot authenticated successfully")
            
        except ValueError as e:
            # Missing credentials
            error_msg = str(e)
            print(f"[callbacks] Login error: {error_msg}")
            state.chatbot_authenticated = False
            state.chatbot_messages = [
                {"role": "error", "content": f"Login failed: {error_msg}"}
            ]
            
        except Exception as e:
            # API error
            error_msg = f"Authentication failed: {str(e)}"
            print(f"[callbacks] Login error: {error_msg}")
            state.chatbot_authenticated = False
            state.chatbot_messages = [
                {"role": "error", "content": error_msg}
            ]
        finally:
            state.chatbot_loading = False
    
    def chatbot_send_message():
        """Send a message to the Biomni chatbot and get response."""
        if not state.chatbot_input or not state.chatbot_input.strip():
            return
        
        if not state.chatbot_authenticated:
            print("[callbacks] Cannot send message - not authenticated")
            return
        
        user_message = state.chatbot_input.strip()
        print(f"[callbacks] Chatbot user message: {user_message}")
        
        # Add user message to chat
        state.chatbot_messages = state.chatbot_messages + [
            {"role": "user", "content": user_message}
        ]
        
        # Clear input
        state.chatbot_input = ""
        
        # Set loading state
        state.chatbot_loading = True
        
        try:
            client = _get_biomni_client()
            
            # Gather current visualization state
            state_info = {
                "data_loaded": state.data_loaded,
                "total_channels": len(state.channels) if state.channels else 0,
            }
            
            # ALL available channels
            if state.channels:
                all_channel_names = [ch["name"] for ch in state.channels]
                state_info["available_channels"] = all_channel_names
            
            # Active channels with names and colors
            if state.active_channels:
                active_channel_names = []
                channel_colors = {}
                
                for ch_id in state.active_channels:
                    for ch in state.channels:
                        if ch["id"] == ch_id:
                            active_channel_names.append(ch["name"])
                            channel_colors[ch["name"]] = ch.get("color", "#FFFFFF")
                            break
                
                state_info["active_channels"] = active_channel_names
                state_info["channel_colors"] = channel_colors
            
            # Analysis settings
            if state.analysis_loaded:
                state_info["dilation"] = state.current_dilation
                state_info["hierarchy_level"] = state.current_hierarchy_level
                state_info["heatmap_tile_count"] = state.heatmap_tile_count
                
                if state.analysis_channels:
                    state_info["analysis_channels"] = state.analysis_channels
            
            # Capture screenshot of current view
            screenshot_base64 = capture_screenshot()
            
            # Generate response from Biomni with state context and screenshot
            response_text = client.generate_response(
                user_message, 
                state_info=state_info,
                screenshot=screenshot_base64  # Pass screenshot
            )
            
            state.chatbot_messages = state.chatbot_messages + [
                {"role": "assistant", "content": response_text}
            ]
            
            print(f"[callbacks] Chatbot response received ({len(response_text)} chars)")
            
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            print(f"[callbacks] Chatbot error: {error_msg}")
            state.chatbot_messages = state.chatbot_messages + [
                {"role": "error", "content": error_msg}
            ]
        finally:
            state.chatbot_loading = False
    
    def chatbot_clear():
        """Clear the chatbot conversation history."""
        print("[callbacks] Clearing chatbot messages")
        state.chatbot_messages = []
        state.chatbot_input = ""
        
    def capture_screenshot():
        """Capture current VTK view as base64-encoded PNG."""
        try:
            import vtk
            import base64
            
            streamer = _refs.get("streamer")
            if not streamer or not hasattr(streamer, 'renderer'):
                print("[callbacks] No renderer available for screenshot")
                return None
            
            # Get render window
            render_window = streamer.renderer.GetRenderWindow()
            render_window.Render()
            
            # Create window to image filter
            window_to_image = vtk.vtkWindowToImageFilter()
            window_to_image.SetInput(render_window)
            window_to_image.SetScale(1)
            window_to_image.SetInputBufferTypeToRGB()
            window_to_image.ReadFrontBufferOff()
            window_to_image.Update()
            
            # Write to PNG in memory
            writer = vtk.vtkPNGWriter()
            writer.SetWriteToMemory(True)
            writer.SetInputConnection(window_to_image.GetOutputPort())
            writer.Write()
            
            # Get the vtkUnsignedCharArray result
            result = writer.GetResult()
            
            if result and result.GetNumberOfTuples() > 0:
                # Convert VTK array to bytes using numpy
                from vtk.util.numpy_support import vtk_to_numpy
                
                # Convert to numpy array, then to bytes
                numpy_array = vtk_to_numpy(result)
                png_bytes = numpy_array.tobytes()
                
                # Encode to base64
                base64_image = base64.b64encode(png_bytes).decode('utf-8')
                
                print(f"[callbacks] Screenshot captured ({len(png_bytes)} bytes)")
                return base64_image
            else:
                print("[callbacks] Failed to capture screenshot - no data in result")
                return None
                
        except Exception as e:
            print(f"[callbacks] Screenshot capture failed: {e}")
            import traceback
            traceback.print_exc()
            return None
        
    def _fly_camera_to_tile(renderer, center, tile_info):
        """Position camera looking at the tile center from above."""
        cam = renderer.GetActiveCamera()
        
        mesh_mgr = _refs.get("mesh_manager")
        sx = mesh_mgr.base_sx if mesh_mgr else 0.14
        
        tile_world_width = tile_info.tile_width * sx
        
        cam.SetFocalPoint(*center)
        
        distance = tile_world_width * 1.5
        cam.SetPosition(center[0], center[1], center[2] + distance)
        cam.SetViewUp(0, 1, 0)
        
        renderer.ResetCameraClippingRange()
        
        print(f"[callbacks] Camera moved to tile center "
            f"({center[0]:.1f}, {center[1]:.1f}, {center[2]:.1f}), distance={distance:.1f}")

    def setup_right_click_picker(interactor):
        """Register a VTK prop picker on right-click to select heatmap tiles."""
        from vtkmodules.vtkRenderingCore import vtkPropPicker
        
        picker = vtkPropPicker()
        
        def _on_right_button_press(obj, event):
            click_pos = obj.GetEventPosition()
            heatmap = _refs.get("heatmap")
            mesh_mgr = _refs.get("mesh_manager")
            streamer = _refs.get("streamer")
            
            if not heatmap or not streamer:
                return
            
            renderer = streamer.renderer
            
            # Hide volumes so they don't block the picker
            volumes = renderer.GetVolumes()
            volumes.InitTraversal()
            hidden_volumes = []
            vol = volumes.GetNextVolume()
            while vol:
                hidden_volumes.append((vol, vol.GetVisibility()))
                vol.SetVisibility(False)
                vol = volumes.GetNextVolume()
            
            try:
                picker.Pick(click_pos[0], click_pos[1], 0, renderer)
                picked_actor = picker.GetActor()
            finally:
                for vol, was_visible in hidden_volumes:
                    vol.SetVisibility(was_visible)
            
            if picked_actor is None:
                print(f"[picker] No actor at ({click_pos[0]}, {click_pos[1]})")
                return
            
            tile = heatmap.get_tile_for_actor(picked_actor)
            if tile is None:
                print(f"[picker] Picked actor is not a heatmap tile")
                return
            
            print(f"[picker] Picked heatmap tile: x0={tile.x0}, y0={tile.y0}, "
                f"x1={tile.x1}, y1={tile.y1}, frac={tile.active_fraction:.3f}")
            
            # --- 1. Zoom camera to this heatmap tile ---
            sx = getattr(state, 'physical_size_x', 0.14)
            sy = getattr(state, 'physical_size_y', 0.14)
            sz = getattr(state, 'physical_size_z', 0.28)
            
            # Heatmap cubes have SetScale(128, 128, 1.0), so world coords are:
            #   world_x = tile_coord * spacing * 128
            tile_center_x = (tile.x0 + tile.x1) / 2.0 * sx * 128
            tile_center_y = (tile.y0 + tile.y1) / 2.0 * sy * 128
            tile_center_z = 0.0
            
            tile_width_world = (tile.x1 - tile.x0) * sx * 128
            tile_height_world = (tile.y1 - tile.y0) * sy * 128
            tile_extent = max(tile_width_world, tile_height_world)
            
            cam = renderer.GetActiveCamera()
            cam.SetFocalPoint(tile_center_x, tile_center_y, tile_center_z)
            cam.SetPosition(tile_center_x, tile_center_y, tile_center_z + tile_extent * 1.5)
            cam.SetViewUp(0, 1, 0)
            renderer.ResetCameraClippingRange()
            
            print(f"[picker] Camera -> tile center ({tile_center_x:.1f}, {tile_center_y:.1f}), "
                f"extent={tile_extent:.1f}")
            
            # --- 2. Optionally load mesh at this location ---
            active_channels = list(state.active_channels)
            if mesh_mgr and mesh_mgr.is_available and active_channels:
                # Convert heatmap tile center to full-res voxel coords
                # World = voxel * spacing, so voxel = world / spacing
                # But heatmap world has the 128 scale factor baked in
                vox_x = (tile.x0 + tile.x1) / 2.0 * 128
                vox_y = (tile.y0 + tile.y1) / 2.0 * 128
                
                first_ch = active_channels[0]
                mesh_tile = mesh_mgr.find_tile_at_voxel(first_ch, vox_x, vox_y)
                
                if mesh_tile:
                    print(f"[picker] Found mesh tile: ({mesh_tile.tile_x}, {mesh_tile.tile_y})")
                    state.selected_tile = {"tile_x": mesh_tile.tile_x, "tile_y": mesh_tile.tile_y}
                    
                    for ch_id in active_channels:
                        color_hex = "#FFFFFF"
                        for ch in state.channels:
                            if ch["id"] == ch_id:
                                color_hex = ch["color"]
                                break
                        color_rgb = _hex_to_rgb_tuple(color_hex)
                        mesh_mgr.activate_channel_mesh(
                            channel_idx=ch_id,
                            color_rgb=color_rgb,
                            tile_x=mesh_tile.tile_x,
                            tile_y=mesh_tile.tile_y,
                            opacity=1.0,
                        )
                else:
                    print(f"[picker] No mesh tile at voxel ({vox_x:.0f}, {vox_y:.0f}) - skipping mesh")
            
            if _refs["view"]:
                _refs["view"].update()
        
        interactor.AddObserver("RightButtonPressEvent", _on_right_button_press)
        print("[callbacks] Right-click picker registered on interactor")

    # Bind to controller
    ctrl.set_streamer = set_streamer
    ctrl.set_heatmap = set_heatmap                
    ctrl.load_data = load_data
    ctrl.clear_data = clear_data
    ctrl.load_analysis_file = load_analysis_file  
    ctrl.update_heatmap = update_heatmap   
    ctrl.update_heatmap_combinations = update_heatmap_combinations
    ctrl.toggle_channel = toggle_channel
    ctrl.update_active_channels = update_active_channels
    ctrl.reset_camera = reset_camera
    ctrl.update_background_color = update_background_color
    ctrl.update_channel_color = update_channel_color
    ctrl.on_channel_color_change = on_channel_color_change
    ctrl.add_channel_to_visible = add_channel_to_visible
    ctrl.remove_channel_from_visible = remove_channel_from_visible
    ctrl.on_channel_range_change = on_channel_range_change
    ctrl.update_upset_data = update_upset_data
    ctrl.update_upset_data_local = update_upset_data_local
    ctrl.update_bar_data = update_bar_data
    ctrl.update_bar_data_local = update_bar_data_local
    ctrl.chatbot_login = chatbot_login
    ctrl.chatbot_send_message = chatbot_send_message
    ctrl.chatbot_clear = chatbot_clear
    ctrl.set_mesh_manager = set_mesh_manager
    ctrl.setup_right_click_picker = setup_right_click_picker

