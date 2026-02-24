from __future__ import annotations

import hashlib
import math
import time
import uuid
from datetime import datetime

from bioset.llm import BiomniClient
from bioset.lineage.snapshot_io import load_snapshots, load_snapshot_by_name, save_snapshot, snapshot_names, delete_snapshot_by_name, save_screenshot
from bioset.NOV import get_nov_sphere_points, camera_position_from_sphere, view_up_for_sphere_point
from bioset.NOV.scoring import compute_view_score_fraction, normalize_scores
from bioset.streaming.lod import compute_visible_xy_roi_vox, camera_distance_to_focal, choose_component
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
            # Per-dataset folder for lineage recordings (one folder per dataset link)
            try:
                url = getattr(state, "zarr_url", "") or ""
                state.lineage_dataset_id = hashlib.md5(url.encode()).hexdigest()[:12] if url else "default"
            except Exception:
                state.lineage_dataset_id = "default"
            
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

        # Reset NOV so only "NOV" is shown (no arrows / <n/8>)
        state.nov_panel_visible = False
        state.nov_candidates = []
        state.nov_current_index = 0
        state.nov_view_index_display = "0/10"
        state.nov_score_display = 0.0
    
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
                mid_idx = len(metadata.hierarchy_levels) // 2
                state.current_hierarchy_level = metadata.hierarchy_levels[mid_idx]["level"]
            
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
        
        selected_channel_names = []
        for ch_id in state.active_channels:
            for ch in state.channels:
                if ch["id"] == ch_id:
                    selected_channel_names.append(ch["name"])
                    break
        
        if not selected_channel_names:
            print("[callbacks] No channels selected - clearing heatmap")
            heatmap.clear()
            state.heatmap_tile_count = 0
            if _refs["view"]:
                _refs["view"].update()
            return
        
        print(f"[callbacks] Updating heatmap for channels: {selected_channel_names}")
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
            if all(ch in selected_channels for ch in combo.channels):
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
        all_data = _filter_combinations_by_channel_selection(combinations, state.upset_selected_channels)

        mapped_combinations = []
        for combination in all_data:
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

    # ---- Lineage (view snapshots, per-dataset recordings) ----
    def _lineage_dataset_id():
        return getattr(state, "lineage_dataset_id", None) or "default"

    def _lineage_merge_channels(restored, all_channels_list):
        """Merge snapshot channels with all dataset channels so user can add new channels in lineage view.
        all_channels_list: full list from state.channels (before opening lineage) so every dataset channel is available."""
        if not all_channels_list:
            return restored
        by_id = {ch.get("id"): dict(ch) for ch in restored if ch.get("id") is not None}
        for ch in all_channels_list:
            ch = dict(ch) if isinstance(ch, dict) else {}
            cid = ch.get("id")
            if cid is not None and cid not in by_id:
                by_id[cid] = {
                    "id": cid,
                    "name": ch.get("name") or f"Channel {cid}",
                    "color": ch.get("color") or get_channel_color(cid),
                    "range": ch.get("range") if ch.get("range") and len(ch.get("range", [])) >= 2 else [0, 100],
                }
        return sorted(by_id.values(), key=lambda c: (0 if c.get("id") is not None else 1, c.get("id") or 0))

    def lineage_refresh_names():
        """Load snapshot names for current dataset into dropdown."""
        dataset_id = _lineage_dataset_id()
        names = snapshot_names(dataset_id)
        state.lineage_snapshot_names = names

    def lineage_open_snapshot():
        """Open selected snapshot: restore camera, channels, colors, LOD, TF; show description/comment."""
        name = getattr(state, "lineage_selected_name", None) or "Name"
        if not name or not str(name).strip():
            print("[callbacks] Lineage: no name selected")
            return
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(name, dataset_id)
        if not snap:
            print(f"[callbacks] Lineage: snapshot not found: {name}")
            return
        streamer = _refs.get("streamer")
        all_channels_before = list(state.channels or [])
        # Build views first (single-view from snap if no views)
        views = snap.get("views")
        if not views or not isinstance(views, list):
            views = [{
                "camera": snap.get("camera") or {},
                "notes": snap.get("notes") or snap.get("description") or "",
                "comments": snap.get("comments") or (snap.get("meta") or {}).get("comments", []),
                "channels": snap.get("channels") or [],
                "active_channels": snap.get("active_channels") or [],
                "background": snap.get("background") or getattr(state, "bg_color", "#000000"),
                "viewport": snap.get("viewport") or {},
                "optional_LOD": snap.get("optional_LOD"),
            }]
        state.lineage_current_view_index = 0
        v0 = views[0]
        # Restore from first view
        restored = []
        for c in v0.get("channels") or []:
            r = c.get("range") or [0, 100]
            if not isinstance(r, (list, tuple)) or len(r) < 2:
                r = [0, 100]
            restored.append({
                "id": c.get("id"),
                "name": c.get("name") or f"Channel {c.get('id')}",
                "color": c.get("color", "#FFFFFF"),
                "range": [float(r[0]), float(r[1])],
            })
        state.channels = _lineage_merge_channels(restored, all_channels_before)
        state.active_channels = list(v0.get("active_channels") or [])
        visible = list(getattr(state, "visible_channel_ids", []) or [])
        for ch_id in state.active_channels:
            if ch_id not in visible:
                visible.append(ch_id)
        state.visible_channel_ids = visible
        if v0.get("background"):
            state.bg_color = v0["background"]
            if hasattr(ctrl, "update_background_color"):
                ctrl.update_background_color(v0["background"])
        lod = snap.get("optional_LOD") or {}
        comp_target = lod.get("component")
        roi = lod.get("roi")
        has_lod = comp_target is not None and isinstance(roi, dict)
        if streamer and has_lod and state.active_channels:
            max_comp = getattr(streamer.cfg, "max_component", 6)
            min_comp = getattr(streamer.cfg, "min_component", 0)
            comp_target = max(min_comp, min(max_comp, int(comp_target)))
            for ch_id in list(streamer.get_active_channels()):
                streamer.deactivate_channel(ch_id)
            for ch_id in state.active_channels:
                ch_id = int(ch_id)
                color_hex = "#FFFFFF"
                for ch in state.channels:
                    if ch.get("id") == ch_id:
                        color_hex = ch.get("color") or color_hex
                        break
                streamer.load_channel_at_lod(ch_id, color_hex, comp_target, roi, reset_camera=False)
            from bioset.scene.volumes import build_tf_with_range
            for ch in state.channels:
                ch_id = ch.get("id")
                if ch_id is None or ch_id not in state.active_channels:
                    continue
                rng = ch.get("range")
                if rng is not None and len(rng) >= 2 and ch_id in getattr(streamer, "_channel_data_range", {}):
                    data_range = streamer._channel_data_range[ch_id]
                    tint = streamer._channel_colors.get(ch_id, (1, 1, 1))
                    color_tf, opacity_tf = build_tf_with_range(data_range, (float(rng[0]), float(rng[1])), tint)
                    streamer._channel_tfs[ch_id] = (color_tf, opacity_tf)
                    if ch_id in streamer.volumes:
                        prop = streamer.volumes[ch_id].GetProperty()
                        prop.SetColor(color_tf)
                        prop.SetScalarOpacity(opacity_tf)
        else:
            if hasattr(ctrl, "update_active_channels"):
                ctrl.update_active_channels(state.active_channels)
            if streamer:
                for ch in state.channels:
                    ch_id = ch.get("id")
                    if ch_id is None:
                        continue
                    if ch_id in state.active_channels and ch.get("color"):
                        streamer._channel_colors[ch_id] = streamer._hex_to_rgb(ch["color"])
                    if ch_id in state.active_channels and ch.get("range") is not None and len(ch.get("range", [])) >= 2 and ch_id in getattr(streamer, "_channel_data_range", {}):
                        from bioset.scene.volumes import build_tf_with_range
                        rng = (float(ch["range"][0]), float(ch["range"][1]))
                        color_tf, opacity_tf = build_tf_with_range(
                            streamer._channel_data_range[ch_id], rng, streamer._channel_colors.get(ch_id, (1, 1, 1))
                        )
                        streamer._channel_tfs[ch_id] = (color_tf, opacity_tf)
                        if ch_id in streamer.volumes:
                            prop = streamer.volumes[ch_id].GetProperty()
                            prop.SetColor(color_tf)
                            prop.SetScalarOpacity(opacity_tf)
        c = v0.get("camera") or {}
        if streamer and hasattr(streamer, "renderer") and streamer.renderer and c:
            cam = streamer.renderer.GetActiveCamera()
            if c and "position" in c and len(c.get("position", [])) >= 3:
                cam.SetPosition(c["position"][:3])
            if c and "focalPoint" in c and len(c.get("focalPoint", [])) >= 3:
                cam.SetFocalPoint(c["focalPoint"][:3])
            if c and "viewUp" in c and len(c.get("viewUp", [])) >= 3:
                cam.SetViewUp(c["viewUp"][:3])
            streamer.renderer.ResetCameraClippingRange()
        if _refs.get("view"):
            _refs["view"].update()
        state.lineage_edit_title = snap.get("title") or ""
        state.lineage_edit_description = v0.get("notes") or ""
        state.lineage_edit_comment = ""
        state.lineage_form_minimized = False
        state.lineage_display_snapshot = {
            "title": snap.get("title"),
            "description": v0.get("notes") or "",
            "comments": v0.get("comments", []),
            "created": snap.get("created"),
            "updated": snap.get("updated"),
            "agreements": v0.get("agreements", snap.get("agreements", 0)),
            "disagreements": v0.get("disagreements", snap.get("disagreements", 0)),
            "views": views,
        }

    def _lineage_apply_view(v):
        """Apply view to scene: camera, channels, active_channels, background, TF. Updates state and streamer."""
        streamer = _refs.get("streamer")
        c = v.get("camera") or {}
        if streamer and hasattr(streamer, "renderer") and streamer.renderer and c:
            cam = streamer.renderer.GetActiveCamera()
            if c.get("position") and len(c["position"]) >= 3:
                cam.SetPosition(c["position"][:3])
            if c.get("focalPoint") and len(c["focalPoint"]) >= 3:
                cam.SetFocalPoint(c["focalPoint"][:3])
            if c.get("viewUp") and len(c["viewUp"]) >= 3:
                cam.SetViewUp(c["viewUp"][:3])
            streamer.renderer.ResetCameraClippingRange()
        if v.get("channels") is not None and v.get("active_channels") is not None:
            restored = []
            for ch in v.get("channels") or []:
                r = ch.get("range") or [0, 100]
                if not isinstance(r, (list, tuple)) or len(r) < 2:
                    r = [0, 100]
                restored.append({
                    "id": ch.get("id"),
                    "name": ch.get("name") or f"Channel {ch.get('id')}",
                    "color": ch.get("color", "#FFFFFF"),
                    "range": [float(r[0]), float(r[1])],
                })
            state.channels = _lineage_merge_channels(restored, list(state.channels or []))
            state.active_channels = list(v.get("active_channels") or [])
            visible = list(getattr(state, "visible_channel_ids", []) or [])
            for ch_id in state.active_channels:
                if ch_id not in visible:
                    visible.append(ch_id)
            state.visible_channel_ids = visible
            if hasattr(ctrl, "update_active_channels"):
                ctrl.update_active_channels(state.active_channels)
            if streamer:
                from bioset.scene.volumes import build_tf_with_range
                for ch in state.channels:
                    ch_id = ch.get("id")
                    if ch_id is None or ch_id not in state.active_channels:
                        continue
                    if ch.get("color"):
                        streamer._channel_colors[ch_id] = streamer._hex_to_rgb(ch["color"])
                    rng = ch.get("range")
                    if rng and len(rng) >= 2 and ch_id in getattr(streamer, "_channel_data_range", {}):
                        color_tf, opacity_tf = build_tf_with_range(
                            streamer._channel_data_range[ch_id], (float(rng[0]), float(rng[1])),
                            streamer._channel_colors.get(ch_id, (1, 1, 1)))
                        streamer._channel_tfs[ch_id] = (color_tf, opacity_tf)
                        if ch_id in streamer.volumes:
                            prop = streamer.volumes[ch_id].GetProperty()
                            prop.SetColor(color_tf)
                            prop.SetScalarOpacity(opacity_tf)
        if v.get("background") and hasattr(ctrl, "update_background_color"):
            state.bg_color = v["background"]
            ctrl.update_background_color(v["background"])
        if _refs.get("view"):
            _refs["view"].update()

    def _lineage_switch_view(new_idx):
        """Switch to view at new_idx: apply that view to scene (camera, channels, ranges, background) and update form."""
        disp = getattr(state, "lineage_display_snapshot", None)
        views = (disp.get("views") or []) if disp else []
        if new_idx < 0 or new_idx >= len(views) or not views:
            return
        v = views[new_idx]
        _lineage_apply_view(v)
        state.lineage_current_view_index = new_idx
        state.lineage_edit_description = v.get("notes") or ""
        state.lineage_edit_comment = ""
        state.lineage_display_snapshot = {
            **disp,
            "description": v.get("notes") or "",
            "comments": v.get("comments", []),
            "agreements": v.get("agreements", 0),
            "disagreements": v.get("disagreements", 0),
        }

    def lineage_apply_current_view():
        """Apply current view (camera, channels, TF, background) to the scene."""
        disp = getattr(state, "lineage_display_snapshot", None)
        views = (disp.get("views") or []) if disp else []
        idx = getattr(state, "lineage_current_view_index", 0)
        if not views or idx < 0 or idx >= len(views):
            return
        _lineage_apply_view(views[idx])

    def lineage_close_display():
        state.lineage_display_snapshot = None
        state.lineage_form_minimized = False

    def lineage_view_prev():
        idx = getattr(state, "lineage_current_view_index", 0)
        _lineage_switch_view(idx - 1)

    def lineage_view_next():
        _lineage_switch_view(getattr(state, "lineage_current_view_index", 0) + 1)

    def lineage_add_view():
        """Add a new view with current camera, channels, TF, background; save to JSON and switch to it."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            return
        cap = _lineage_capture_view()
        views = list(disp.get("views") or [])
        new_view = {
            "camera": cap["camera"],
            "notes": "",
            "comments": [],
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap.get("viewport") or {},
            "optional_LOD": cap.get("optional_lod"),
            "agreements": 0,
            "disagreements": 0,
        }
        views.append(new_view)
        state.lineage_current_view_index = len(views) - 1
        state.lineage_edit_description = ""
        state.lineage_edit_comment = ""
        state.lineage_display_snapshot = {
            **disp,
            "views": views,
            "description": "",
            "comments": [],
            "agreements": 0,
            "disagreements": 0,
        }
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(disp["title"], dataset_id)
        if snap:
            snap["views"] = views
            snap["updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            save_snapshot(snap, dataset_id)
        if _refs.get("view"):
            _refs["view"].update()

    def lineage_open_new_form():
        """Open the new-snapshot form (bottom-left). Optionally refresh names."""
        lineage_refresh_names()
        state.lineage_form_name = getattr(state, "lineage_selected_name", "Name") or "Name"
        state.lineage_form_description = ""
        state.lineage_form_new_comment = ""
        state.lineage_form_dialog = True

    def _lineage_capture_view():
        """Capture camera, LOD, channels (active only), viewport, background from current view."""
        streamer = _refs.get("streamer")
        camera = {}
        if streamer and hasattr(streamer, "renderer") and streamer.renderer:
            cam = streamer.renderer.GetActiveCamera()
            camera["position"] = list(cam.GetPosition())
            camera["focalPoint"] = list(cam.GetFocalPoint())
            camera["viewUp"] = list(cam.GetViewUp())
        optional_lod = None
        if streamer and getattr(streamer, "state", None):
            for ch_id in state.active_channels:
                if ch_id in streamer.state:
                    st = streamer.state[ch_id]
                    optional_lod = {
                        "component": st.component,
                        "roi": {"x0": st.roi.x0, "x1": st.roi.x1, "y0": st.roi.y0, "y1": st.roi.y1},
                    }
                    break
        active = list(state.active_channels) if state.active_channels else []
        channels_data = []
        for ch in (state.channels or []):
            c = dict(ch) if isinstance(ch, dict) else {}
            if c.get("id") not in active:
                continue
            r = c.get("range") or [0, 100]
            if not isinstance(r, (list, tuple)) or len(r) < 2:
                r = [0, 100]
            channels_data.append({
                "id": c.get("id"),
                "name": c.get("name") or f"Channel {c.get('id', '')}",
                "color": c.get("color", "#FFFFFF"),
                "range": [float(r[0]), float(r[1])],
            })
        viewport = {}
        if streamer and hasattr(streamer, "renderer") and streamer.renderer:
            rw = streamer.renderer.GetRenderWindow()
            if rw:
                w, h = rw.GetSize()
                viewport = {"width": w, "height": h}
        bg = getattr(state, "bg_color", "#000000") or "#000000"
        return {"camera": camera, "optional_lod": optional_lod, "channels": channels_data, "active_channels": active, "viewport": viewport, "background": bg}

    def lineage_save_snapshot():
        """Capture current view + form fields; save to lineage JSON; close form."""
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cap = _lineage_capture_view()
        form_name = (getattr(state, "lineage_form_name", None) or getattr(state, "lineage_selected_name", None) or "").strip()
        title = form_name or "Unnamed"
        comments = []
        if getattr(state, "lineage_form_new_comment", "").strip():
            comments.append({"date": now, "text": state.lineage_form_new_comment})
        notes = getattr(state, "lineage_form_description", "") or ""
        view0 = {
            "camera": cap["camera"],
            "notes": notes,
            "comments": comments,
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap.get("viewport") or {},
            "optional_LOD": cap.get("optional_lod"),
        }
        snapshot = {
            "id": str(uuid.uuid4()),
            "title": title,
            "created": now,
            "updated": now,
            "notes": notes,
            "camera": cap["camera"],
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap["viewport"],
            "agreements": 0,
            "disagreements": 0,
            "comments": comments,
            "views": [view0],
        }
        if cap["optional_lod"]:
            snapshot["optional_LOD"] = cap["optional_lod"]
        dataset_id = _lineage_dataset_id()
        save_snapshot(snapshot, dataset_id)
        lineage_refresh_names()
        state.lineage_selected_name = title
        state.lineage_form_dialog = False
        state.lineage_edit_title = title
        state.lineage_edit_description = notes
        state.lineage_edit_comment = ""
        state.lineage_current_view_index = 0
        state.lineage_display_snapshot = {
            "title": title,
            "description": notes,
            "comments": comments,
            "created": now,
            "updated": now,
            "agreements": 0,
            "disagreements": 0,
            "views": [view0],
        }
        if _refs.get("view"):
            _refs["view"].update()

    def lineage_update_snapshot():
        """Save current view into views list (use state's views so Add-view entries persist), then write JSON."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            print("[callbacks] Lineage: no snapshot displayed to update")
            return
        old_title = disp["title"]
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(old_title, dataset_id)
        if not snap:
            print(f"[callbacks] Lineage: snapshot not found: {old_title}")
            return
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cap = _lineage_capture_view()
        snap["updated"] = now
        snap["title"] = (getattr(state, "lineage_edit_title", "") or "").strip() or old_title
        # Use state's views so any views added with "Add view" are kept and saved
        views = list(disp.get("views") or [])
        if not views and snap.get("views"):
            views = list(snap.get("views") or [])
        if not views or not isinstance(views, list):
            views = [{
                "camera": snap.get("camera") or {},
                "notes": snap.get("notes") or "",
                "comments": snap.get("comments") or [],
                "channels": snap.get("channels") or [],
                "active_channels": snap.get("active_channels") or [],
                "background": snap.get("background") or "",
                "viewport": snap.get("viewport") or {},
                "optional_LOD": snap.get("optional_LOD"),
                "agreements": snap.get("agreements", 0),
                "disagreements": snap.get("disagreements", 0),
            }]
        idx = getattr(state, "lineage_current_view_index", 0)
        idx = max(0, min(idx, len(views) - 1))
        notes = getattr(state, "lineage_edit_description", "") or ""
        new_comment = (getattr(state, "lineage_edit_comment", "") or "").strip()
        view_comments = list(views[idx].get("comments") or []) if idx < len(views) else []
        if new_comment:
            view_comments.append({"date": now, "text": new_comment})
        current_view_data = views[idx] if idx < len(views) else {}
        def _view_agreements():
            if "agreements" in current_view_data:
                return current_view_data["agreements"]
            return snap.get("agreements", 0) if idx == 0 else 0
        def _view_disagreements():
            if "disagreements" in current_view_data:
                return current_view_data["disagreements"]
            return snap.get("disagreements", 0) if idx == 0 else 0
        view_payload = {
            "camera": cap["camera"],
            "notes": notes,
            "comments": view_comments,
            "channels": cap["channels"],
            "active_channels": cap["active_channels"],
            "background": cap["background"],
            "viewport": cap.get("viewport") or {},
            "optional_LOD": cap.get("optional_lod"),
            "agreements": _view_agreements(),
            "disagreements": _view_disagreements(),
        }
        if idx < len(views):
            views[idx] = view_payload
        else:
            views.append(view_payload)
        snap["views"] = views
        current_view = views[idx]
        snap["channels"] = current_view.get("channels") or []
        snap["active_channels"] = current_view.get("active_channels") or []
        snap["background"] = current_view.get("background") or ""
        snap["viewport"] = current_view.get("viewport") or {}
        if current_view.get("optional_LOD"):
            snap["optional_LOD"] = current_view["optional_LOD"]
        else:
            snap.pop("optional_LOD", None)
        snap["camera"] = current_view.get("camera") or {}
        snap["notes"] = current_view.get("notes") or ""
        snap["description"] = snap["notes"]
        snap["comments"] = current_view.get("comments") or []
        if snap["title"] != old_title:
            delete_snapshot_by_name(old_title, dataset_id)
        save_snapshot(snap, dataset_id)
        lineage_refresh_names()
        state.lineage_selected_name = snap["title"]
        state.lineage_display_snapshot = {
            "title": snap["title"],
            "description": notes,
            "comments": view_comments,
            "created": snap.get("created"),
            "updated": snap["updated"],
            "agreements": current_view.get("agreements", 0),
            "disagreements": current_view.get("disagreements", 0),
            "views": views,
        }
        state.lineage_edit_title = snap["title"]
        state.lineage_edit_comment = ""

    def lineage_agree():
        """Increment agreements for the current view and save."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            print("[callbacks] Lineage: no snapshot displayed to agree")
            return
        idx = max(0, min(getattr(state, "lineage_current_view_index", 0), len(disp.get("views") or []) - 1))
        title = disp["title"]
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(title, dataset_id)
        if not snap:
            print(f"[callbacks] Lineage: snapshot not found: {title}")
            return
        views = list(snap.get("views") or [])
        if idx < len(views):
            views[idx]["agreements"] = views[idx].get("agreements", 0) + 1
            snap["views"] = views
            save_snapshot(snap, dataset_id)
            state.lineage_display_snapshot = {
                **disp,
                "views": views,
                "agreements": views[idx]["agreements"],
            }

    def lineage_disagree():
        """Increment disagreements for the current view and save."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            return
        idx = max(0, min(getattr(state, "lineage_current_view_index", 0), len(disp.get("views") or []) - 1))
        title = disp["title"]
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(title, dataset_id)
        if not snap:
            return
        views = list(snap.get("views") or [])
        if idx < len(views):
            views[idx]["disagreements"] = views[idx].get("disagreements", 0) + 1
            snap["views"] = views
            save_snapshot(snap, dataset_id)
            state.lineage_display_snapshot = {
                **disp,
                "views": views,
                "disagreements": views[idx]["disagreements"],
            }

    def lineage_comment():
        """Append current comment to the current view and save."""
        disp = getattr(state, "lineage_display_snapshot", None)
        if not disp or not disp.get("title"):
            return
        new_comment = (getattr(state, "lineage_edit_comment", "") or "").strip()
        if not new_comment:
            return
        title = disp["title"]
        dataset_id = _lineage_dataset_id()
        snap = load_snapshot_by_name(title, dataset_id)
        if not snap:
            return
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        snap["updated"] = now
        views = snap.get("views") or [{"camera": snap.get("camera"), "notes": snap.get("notes") or "", "comments": snap.get("comments") or []}]
        idx = max(0, min(getattr(state, "lineage_current_view_index", 0), len(views) - 1))
        views[idx].setdefault("comments", []).append({"date": now, "text": new_comment})
        snap["views"] = views
        snap["comments"] = views[0].get("comments", [])
        save_snapshot(snap, dataset_id)
        state.lineage_display_snapshot = {**disp, "comments": views[idx].get("comments", []), "updated": now, "views": views}
        state.lineage_edit_comment = ""

    def _capture_screenshot_png_bytes():
        """Capture VTK view as PNG bytes (scene only, no UI). Returns bytes or None."""
        try:
            import vtk
            from vtk.util.numpy_support import vtk_to_numpy
            streamer = _refs.get("streamer")
            if not streamer or not hasattr(streamer, "renderer"):
                return None
            rw = streamer.renderer.GetRenderWindow()
            rw.Render()
            w2i = vtk.vtkWindowToImageFilter()
            w2i.SetInput(rw)
            w2i.SetScale(1)
            w2i.SetInputBufferTypeToRGB()
            w2i.ReadFrontBufferOff()
            w2i.Update()
            writer = vtk.vtkPNGWriter()
            writer.SetWriteToMemory(True)
            writer.SetInputConnection(w2i.GetOutputPort())
            writer.Write()
            result = writer.GetResult()
            if result and result.GetNumberOfTuples() > 0:
                return vtk_to_numpy(result).tobytes()
            return None
        except Exception:
            return None

    def capture_screenshot():
        """Capture current VTK view as base64-encoded PNG."""
        import base64
        png_bytes = _capture_screenshot_png_bytes()
        return base64.b64encode(png_bytes).decode("utf-8") if png_bytes else None

    def lineage_open_export_screenshot():
        """Open export screenshot popup: empty name and caption boxes (placeholders only)."""
        state.lineage_export_screenshot_name = ""
        state.lineage_export_screenshot_caption = ""
        state.lineage_export_screenshot_dialog = True

    def _add_caption_to_png(png_bytes: bytes, caption: str) -> bytes:
        """Add white caption at bottom of image. Returns PNG bytes. Falls back to original if Pillow missing."""
        if not (caption or "").strip():
            return png_bytes
        try:
            import io
            from PIL import Image, ImageDraw, ImageFont
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            w, h = img.size
            pad = 14
            font = ImageFont.load_default()
            for path in ("arial.ttf", "Arial.ttf", "C:/Windows/Fonts/arial.ttf"):
                try:
                    font = ImageFont.truetype(path, 20)
                    break
                except Exception:
                    continue
            lines = (caption or "").strip().replace("\r", "").split("\n")[:10]
            line_h = 26
            cap_h = min(len(lines) * line_h + pad * 2, 280)
            out = Image.new("RGB", (w, h + cap_h), (0, 0, 0))
            out.paste(img, (0, 0))
            draw = ImageDraw.Draw(out)
            y = h + pad
            for line in lines[:8]:
                if line.strip():
                    draw.text((pad, y), line.strip()[:120], fill=(255, 255, 255), font=font)
                y += line_h
            buf = io.BytesIO()
            out.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return png_bytes

    def lineage_export_screenshot_save():
        """Save screenshot to dataset Screenshot folder with chosen name; add description as caption; close dialog."""
        name = (getattr(state, "lineage_export_screenshot_name", "") or "").strip()
        if not name:
            return
        png_bytes = _capture_screenshot_png_bytes()
        if not png_bytes:
            return
        caption = getattr(state, "lineage_export_screenshot_caption", "") or ""
        png_bytes = _add_caption_to_png(png_bytes, caption)
        dataset_id = _lineage_dataset_id()
        path = save_screenshot(png_bytes, name, dataset_id)
        state.lineage_export_screenshot_dialog = False
        state.lineage_export_screenshot_name = ""
        state.lineage_export_screenshot_caption = ""
        if _refs.get("view"):
            _refs["view"].update()

    def _nov_apply_camera(camera_dict):
        """Apply camera dict (position, focalPoint, viewUp) to streamer and refresh view."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None) or not streamer.renderer:
            return
        c = camera_dict.get("camera") or camera_dict
        cam = streamer.renderer.GetActiveCamera()
        if c.get("position") and len(c["position"]) >= 3:
            cam.SetPosition(c["position"][:3])
        if c.get("focalPoint") and len(c.get("focalPoint", [])) >= 3:
            cam.SetFocalPoint(c["focalPoint"][:3])
        if c.get("viewUp") and len(c.get("viewUp", [])) >= 3:
            cam.SetViewUp(c["viewUp"][:3])
        streamer.renderer.ResetCameraClippingRange()
        if _refs.get("view"):
            _refs["view"].update()

    def _build_nov_sphere_svg(sphere_xy, current_index):
        """Build SVG string for 18 positions on sphere; current_index is highlighted."""
        if not sphere_xy:
            return ""
        parts = [
            '<svg width="28" height="28" viewBox="0 0 56 56" style="display:block">',
            '<circle cx="28" cy="28" r="22" fill="none" stroke="rgba(255,255,255,0.4)" stroke-width="1.5"/>',
        ]
        for i, (x, y) in enumerate(sphere_xy):
            active = i == current_index
            r = 4 if active else 2.5
            fill = "#fff" if active else "rgba(255,255,255,0.5)"
            stroke = "#1976d2" if active else "transparent"
            sw = 1.5 if active else 0
            parts.append(
                f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
            )
        parts.append("</svg>")
        return "".join(parts)

    def nov_toggle():
        """Compute 8 NOV candidates (sphere points), score by visible ROI, show panel and apply best view.
        Uses current scene state: active channels, LOD component, and camera from streamer."""
        streamer = _refs.get("streamer")
        if not streamer or not getattr(streamer, "renderer", None) or not streamer.renderer:
            state.nov_panel_visible = False
            print("[NOV] Skipped: no streamer or renderer")
            return
        t0 = time.perf_counter()
        cam = streamer.renderer.GetActiveCamera()
        focal = list(cam.GetFocalPoint())
        radius = camera_distance_to_focal(cam)
        if radius < 1e-6:
            radius = 1.0
        points = get_nov_sphere_points()
        n_points = len(points)
        # Read LOD/component from current scene state (what is actually displayed)
        active_channel_ids = list(streamer.get_active_channels()) if streamer else []
        desired_comp = None
        if active_channel_ids:
            for ch_id in active_channel_ids:
                st = getattr(streamer, "state", None) and streamer.state.get(ch_id)
                if st is not None:
                    desired_comp = st.component
                    break
        if desired_comp is None:
            dist = radius
            desired_comp = choose_component(
                dist,
                streamer.cfg.distance_rules,
                min_component=streamer.cfg.min_component,
                max_component=streamer.cfg.max_component,
            )
            print(f"[NOV] Start: focal={focal}, radius={radius:.1f}, {n_points} candidates (no scene state -> LOD from distance)")
        else:
            print(f"[NOV] Start: focal={focal}, radius={radius:.1f}, {n_points} candidates (scene state: comp={desired_comp}, active_channels={active_channel_ids})")
        spacing = streamer._spacing_for_component(desired_comp)
        zdim, ydim, xdim = streamer._dims_for_component(desired_comp)
        bounds = streamer._volume_bounds_world(desired_comp)
        margin = getattr(streamer.cfg, "roi_margin_vox", 16)
        print(f"[NOV] LOD component={desired_comp}, dims=({xdim}, {ydim}, {zdim})")

        raw_scores = []
        candidates = []
        for i, (theta_deg, phi_deg) in enumerate(points):
            pos = camera_position_from_sphere(focal, radius, theta_deg, phi_deg)
            view_up = view_up_for_sphere_point(theta_deg, phi_deg)
            cam.SetPosition(pos)
            cam.SetFocalPoint(focal)
            cam.SetViewUp(view_up)
            streamer.renderer.ResetCameraClippingRange()
            # No Render() here: DisplayToWorld uses camera matrices only -> much faster
            roi = compute_visible_xy_roi_vox(
                streamer.renderer,
                bounds_world=bounds,
                sx=spacing.sx, sy=spacing.sy,
                x_dim=xdim, y_dim=ydim,
                margin_vox=margin,
                display_samples=5,
            )
            area = (roi.x1 - roi.x0) * (roi.y1 - roi.y0)
            total_xy = xdim * ydim
            occlusion = max(0.0, float(total_xy) - area)
            score_raw = compute_view_score_fraction(area, total_xy)
            raw_scores.append(score_raw)
            candidates.append({
                "camera": {
                    "position": pos,
                    "focalPoint": focal,
                    "viewUp": view_up,
                },
                "score_raw": score_raw,
                "theta_deg": theta_deg,
                "phi_deg": phi_deg,
                "fixed_index": i,
            })
            print(f"[NOV]   candidate {i+1}/{n_points} theta={theta_deg} phi={phi_deg} -> roi=({roi.x0}:{roi.x1},{roi.y0}:{roi.y1}) area={area} occ={occlusion:.0f} score={score_raw:.3f} (visible frac)")
        normed = normalize_scores(raw_scores)
        for i, c in enumerate(candidates):
            c["score_normalized"] = normed[i] if i < len(normed) else 0.0
        # Sort high to low: rank 1 = best, 10 = worst
        candidates.sort(key=lambda x: x["score_normalized"], reverse=True)
        best_score = candidates[0]["score_normalized"] if candidates else 0.0
        state.nov_candidates = candidates
        state.nov_current_index = 0
        # Sphere positions in fixed geometric order (theta 0-180, phi 0-360) for consistent layout
        r_svg, cx_svg, cy_svg = 22, 28, 28
        sphere_xy = []
        for theta_deg, phi_deg in points:
            th = math.radians(theta_deg)
            ph = math.radians(phi_deg)
            x = math.sin(th) * math.cos(ph)
            y = math.sin(th) * math.sin(ph)
            sphere_xy.append([round(cx_svg + r_svg * x, 1), round(cy_svg - r_svg * y, 1)])
        state.nov_sphere_xy = sphere_xy
        active_fixed = candidates[0]["fixed_index"] if candidates else 0
        state.nov_sphere_svg = _build_nov_sphere_svg(sphere_xy, active_fixed)
        state.nov_is_front = active_fixed < 5
        state.nov_score_display = candidates[0]["score_normalized"] if candidates else 0.0
        state.nov_view_index_display = f"1/{len(candidates)}" if candidates else "0/10"
        state.nov_panel_visible = True
        print("[NOV] Scores (rank 1=best .. 10=worst):")
        for rank, c in enumerate(candidates, 1):
            print(f"[NOV]   #{rank}  score_raw={c['score_raw']:.2f}  score_norm={c['score_normalized']:.2f}")
        if candidates:
            _nov_apply_camera(candidates[0])
        elapsed = time.perf_counter() - t0
        print(f"[NOV] Done: best score={best_score:.2f}, applied view 1/{len(candidates)}, elapsed={elapsed:.2f}s")
        if _refs.get("view"):
            _refs["view"].update()

    def nov_prev():
        """Switch to previous NOV candidate and update score display."""
        candidates = getattr(state, "nov_candidates", []) or []
        if not candidates:
            print("[NOV] Prev: no candidates")
            return
        idx = getattr(state, "nov_current_index", 0)
        idx = (idx - 1) % len(candidates)
        state.nov_current_index = idx
        active_fixed = candidates[idx]["fixed_index"]
        state.nov_sphere_svg = _build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), active_fixed)
        state.nov_is_front = active_fixed < 5
        _nov_apply_camera(candidates[idx])
        state.nov_score_display = candidates[idx]["score_normalized"]
        state.nov_view_index_display = f"{idx + 1}/{len(candidates)}"
        print(f"[NOV] Prev -> view {idx + 1}/{len(candidates)} score={candidates[idx]['score_normalized']:.2f}")
        if _refs.get("view"):
            _refs["view"].update()

    def nov_next():
        """Switch to next NOV candidate and update score display."""
        candidates = getattr(state, "nov_candidates", []) or []
        if not candidates:
            print("[NOV] Next: no candidates")
            return
        idx = getattr(state, "nov_current_index", 0)
        idx = (idx + 1) % len(candidates)
        state.nov_current_index = idx
        active_fixed = candidates[idx]["fixed_index"]
        state.nov_sphere_svg = _build_nov_sphere_svg(getattr(state, "nov_sphere_xy", []), active_fixed)
        state.nov_is_front = active_fixed < 5
        _nov_apply_camera(candidates[idx])
        state.nov_score_display = candidates[idx]["score_normalized"]
        state.nov_view_index_display = f"{idx + 1}/{len(candidates)}"
        print(f"[NOV] Next -> view {idx + 1}/{len(candidates)} score={candidates[idx]['score_normalized']:.2f}")
        if _refs.get("view"):
            _refs["view"].update()

    # Bind to controller
    ctrl.set_streamer = set_streamer
    ctrl.set_heatmap = set_heatmap                
    ctrl.load_data = load_data
    ctrl.clear_data = clear_data
    ctrl.load_analysis_file = load_analysis_file  
    ctrl.update_heatmap = update_heatmap         
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
    ctrl.lineage_refresh_names = lineage_refresh_names
    ctrl.lineage_open_snapshot = lineage_open_snapshot
    ctrl.lineage_open_new_form = lineage_open_new_form
    ctrl.lineage_save_snapshot = lineage_save_snapshot
    ctrl.lineage_view_prev = lineage_view_prev
    ctrl.lineage_view_next = lineage_view_next
    ctrl.lineage_add_view = lineage_add_view
    ctrl.lineage_close_display = lineage_close_display
    ctrl.lineage_apply_current_view = lineage_apply_current_view
    ctrl.lineage_open_export_screenshot = lineage_open_export_screenshot
    ctrl.lineage_export_screenshot_save = lineage_export_screenshot_save
    ctrl.lineage_agree = lineage_agree
    ctrl.lineage_disagree = lineage_disagree
    ctrl.lineage_comment = lineage_comment
    ctrl.lineage_update_snapshot = lineage_update_snapshot
    ctrl.nov_toggle = nov_toggle
    ctrl.nov_prev = nov_prev
    ctrl.nov_next = nov_next

