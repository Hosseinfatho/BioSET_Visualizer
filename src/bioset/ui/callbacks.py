from __future__ import annotations

import datetime
import hashlib
import os
import tempfile

from bioset.NOV import register_nov_callbacks
from bioset.bookmark import register_bookmark_callbacks, capture_screenshot_png_bytes
from bioset.llm import BiomniLocalClient
from bioset.report import generate_report_bytes
from bioset.scene.volumes import build_tf_with_range
from .state import get_channel_color
from ..report.content_sections.AnalysisDataset import AnalysisDatasetContent, AnalysisDataset
from ..report.content_sections.Bookmarks import Bookmarks, load_all_bookmarks
from ..report.content_sections.Chat import ChatContent, Chat, LLMSettings
from ..report.content_sections.General import GeneralContent, General


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
        "streamer": streamer,
        "view": view,
        "analysis_loader": None,
        "heatmap": None,
        "mesh_manager": None,
        "heatmap_lod": None,
        "renderer": None,
        "label_manager": None,
        "biomni_client": None,
        "last_tile_channel_stats": None,
        "interactor": None,
    }

    def set_view(v):
        _refs["view"] = v

    ctrl.set_view = set_view

    def set_nov_view(v):
        _refs["nov_view"] = v

    ctrl.set_nov_view = set_nov_view

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

    def set_heatmap_lod(heatmap_lod):
        """Set the heatmap LOD renderer reference."""
        _refs["heatmap_lod"] = heatmap_lod
        print(f"[callbacks] Heatmap LOD set: {heatmap_lod}")

    def set_interactor(interactor):
        """Set the main VTK interactor for bookmark flag picking."""
        _refs["interactor"] = interactor

    ctrl.set_interactor = set_interactor

    def set_heatmap_lod_auto_mode(enabled: bool):
        """Set heatmap LOD auto mode (controlled by UI toggle)."""
        heatmap_lod = _refs.get("heatmap_lod")
        if heatmap_lod and hasattr(heatmap_lod, "set_auto_mode"):
            heatmap_lod.set_auto_mode(enabled)

    register_bookmark_callbacks(ctrl, state, _refs)
    register_nov_callbacks(ctrl, state, _refs)

    def set_renderer(renderer):
        """Set the VTK renderer reference (needed for label placement)."""
        _refs["renderer"] = renderer
        print(f"[callbacks] Renderer set for label system")

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
            # Per-dataset folder for bookmark recordings (one folder per dataset link)
            try:
                url = getattr(state, "zarr_url", "") or ""
                state.bookmark_dataset_id = hashlib.md5(url.encode()).hexdigest()[:12] if url else "default"
            except Exception:
                state.bookmark_dataset_id = "default"
            
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

        heatmap_lod = _refs.get("heatmap_lod")
        if heatmap_lod:
            heatmap_lod.clear_analysis()

        label_mgr = _refs.get("label_manager")
        if label_mgr:
            label_mgr.clear()
            _refs["label_manager"] = None

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

        # Close bookmark UI (column, forms, popups) when data is cleared
        # Hide the bookmark side panel
        state.bookmark_open = False
        # Close any open bookmark display / details panel
        state.bookmark_display_snapshot = None
        state.bookmark_form_minimized = False
        # Close new-bookmark form (bottom-left)
        state.bookmark_form_dialog = False
        # Close flag popups and hide bookmark flags
        state.bookmark_flag_popup = None
        state.bookmark_flag_popup_html = ""
        state.bookmark_flag_popup_screen = ""
        state.bookmark_flag_popup_left = 0
        state.bookmark_flag_popup_top = 0
        state.bookmark_flags_visible = False
        state.bookmark_flags_data = []
        # Close export-screenshot dialog if open
        state.bookmark_export_screenshot_dialog = False
        state.bookmark_export_screenshot_name = ""
        state.bookmark_export_screenshot_caption = ""

        # Reset NOV and hide 2D rect overlay
        state.nov_show_rect = False
        state.nov_drawing_box = False
        state.nov_dragging_corner = None
        state.nov_lens_center = None
        state.nov_lens_length = 0.0
        state.nov_lens_width = 0.0
        state.nov_lens_depth = 0.0
        state.nov_panel_visible = False
        state.nov_candidates = []
        state.nov_current_index = 0
        state.nov_view_index_display = ""
        state.nov_score_display = 0.0
        state.nov_sphere_svg = ""
        state.nov_sphere_xy = []
        if hasattr(ctrl, "nov_hide_lens"):
            ctrl.nov_hide_lens()
    
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

    def toggle_channel_surface(channel_id):
        """Toggle mesh surface visibility for a channel."""
        mesh_mgr = _refs.get("mesh_manager")
        hidden = list(state.surface_hidden_channels)

        if channel_id in hidden:
            hidden.remove(channel_id)
            state.surface_hidden_channels = hidden
            if (channel_id in state.active_channels
                    and state.selected_tile and mesh_mgr and mesh_mgr.is_available):
                color_hex = "#FFFFFF"
                for ch in state.channels:
                    if ch["id"] == channel_id:
                        color_hex = ch["color"]
                        break
                color_rgb = _hex_to_rgb_tuple(color_hex)
                mesh_mgr.activate_channel_mesh(
                    channel_idx=channel_id,
                    color_rgb=color_rgb,
                    tile_x=state.selected_tile["tile_x"],
                    tile_y=state.selected_tile["tile_y"],
                    opacity=1.0,
                )
        else:
            hidden.append(channel_id)
            state.surface_hidden_channels = hidden
            if mesh_mgr:
                mesh_mgr.deactivate_channel_mesh(channel_id)

        if _refs["view"]:
            _refs["view"].update()

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

            # Notify HeatmapLOD of new analysis context
            heatmap_lod = _refs.get("heatmap_lod")
            if heatmap_lod and loader.db_path:
                z_depth = 1
                bounds = metadata.volume_bounds
                if bounds and "z" in bounds:
                    z_depth = max(1, bounds["z"][1] - bounds["z"][0])
                heatmap_lod.set_analysis(
                    db_path=loader.db_path,
                    channel_order=list(metadata.channels),
                    z_depth=z_depth,
                )

            state.analysis_file_name = file_name
            state.analysis_channels = metadata.channels
            state.analysis_dilation_amounts = metadata.dilation_amounts
            state.analysis_hierarchy_levels = [lvl["level"] for lvl in metadata.hierarchy_levels]
            state.analysis_volume_bounds = metadata.volume_bounds
            
            if metadata.dilation_amounts:
                state.current_dilation = metadata.dilation_amounts[0]
            
            if metadata.hierarchy_levels:
                state.current_hierarchy_level = metadata.hierarchy_levels[len(metadata.hierarchy_levels)-1]["level"]
                state.current_hierarchy_level = metadata.hierarchy_levels[len(metadata.hierarchy_levels)-1]["level"]
            
            # Initialize plot channel selections with all channels
            state.upset_selected_channels = [ch for ch in state.analysis_channels]
            state.bar_selected_channels = [ch for ch in state.analysis_channels]
            
            state.analysis_loaded = True
            state.right_drawer_open = True  
            
            print(f"[callbacks] Analysis loaded: {len(metadata.channels)} channels, "
                  f"dilations={metadata.dilation_amounts}, levels={state.analysis_hierarchy_levels}")
            
            update_heatmap()
            update_heatmap_combinations()
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
            if (state.selected_tile and mesh_mgr and mesh_mgr.is_available
                    and channel_id not in state.surface_hidden_channels):
                tile_x = state.selected_tile["tile_x"]
                tile_y = state.selected_tile["tile_y"]
                color_rgb = _hex_to_rgb_tuple(color_hex)
                mesh_mgr.activate_channel_mesh(
                    channel_idx=channel_id,
                    color_rgb=color_rgb,
                    tile_x=tile_x,
                    tile_y=tile_y,
                    opacity=1.0,
                )
                print(f"[callbacks] Added mesh for ch {channel_id} at tile ({tile_x}, {tile_y})")

        if streamer._channel_histograms:
            state.channel_histograms = {
                str(ch_id): streamer._channel_histograms[ch_id] for ch_id in new_active if ch_id in streamer._channel_histograms
            }
            
        if to_activate and not currently_active:
            heatmap_lod = _refs.get("heatmap_lod")
            if heatmap_lod:
                from bioset.streaming.lod import camera_distance_to_focal
                renderer = streamer.renderer
                dist = camera_distance_to_focal(renderer.GetActiveCamera())
                heatmap_lod.on_camera_moved(dist)

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
        # Keep HeatmapLOD in sync with the current combination and dilation
        heatmap_lod = _refs.get("heatmap_lod")
        streamer = _refs.get("streamer")
        if heatmap_lod:
            heatmap_lod.update_dilation(state.current_dilation)
            heatmap_lod.update_channels(state.heatmap_combination or [])
            # Correct stale LOD level: channels are now set, so check the
            # actual camera distance and override current_hierarchy_level if
            # it no longer matches (e.g. after all channels were deactivated
            # and camera reset to far-out position).
            if heatmap_lod._auto_mode and streamer:
                from bioset.streaming.lod import camera_distance_to_focal, choose_heatmap_level
                dist = camera_distance_to_focal(streamer.renderer.GetActiveCamera())
                correct_level = choose_heatmap_level(dist, heatmap_lod._distance_rules)
                if correct_level != heatmap_lod._current_level:
                    print(f"[callbacks] Correcting stale LOD level: "
                          f"{heatmap_lod._current_level} -> {correct_level} (dist={dist:.1f})")
                    heatmap_lod._current_level = correct_level
                    state.current_hierarchy_level = correct_level

        loader = _refs.get("analysis_loader")
        heatmap = _refs.get("heatmap")

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
            outline_only = getattr(state, "heatmap_outline_only", "filled") == "outline"
            heatmap.update_tiles(tiles, spacing=spacing, color=color, outline_only=outline_only)
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

        min_number_channels = int(getattr(state, "upset_min_channels", 2))

        # Get all combinations from analysis (large limit), sorted by agg IoU desc
        combinations = loader.get_top_combinations(
            dilation=state.current_dilation,
            hierarchy_level=state.current_hierarchy_level,
            limit=1000,
            min_channels=min_number_channels,
        )
        
        # Filter to selected channels
        filtered_data = _filter_combinations_by_channel_selection(combinations, state.upset_selected_channels)

        mapped_combinations = []
        for combination in filtered_data:
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
        """Reset camera to initial position (from when data was first loaded). Use after opening a Bookmark to return to default view."""
        streamer = _refs.get("streamer")
        if streamer and getattr(streamer, "renderer", None):
            if hasattr(streamer, "reset_camera_to_initial"):
                streamer.reset_camera_to_initial()
            else:
                streamer.renderer.ResetCamera()
                streamer.renderer.ResetCameraClippingRange()
        if _refs.get("view"):
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
                        data_range = streamer._channel_data_range[channel_id]
                        pct_range = streamer._channel_percentile_bounds.get(channel_id, (data_range[0], data_range[1], data_range[1]))
                        color_tf, opacity_tf = build_tf_with_range(data_range, pct_range, tuple(current_range), tint_rgb)
                    else:
                        color_tf, opacity_tf, pct_bounds = build_histogram_tf(img, tint_rgb=tint_rgb)
                        streamer._channel_percentile_bounds[channel_id] = pct_bounds
                    streamer._channel_tfs[channel_id] = (color_tf, opacity_tf)
                    
                    prop = vol.GetProperty()
                    prop.SetColor(color_tf)
                    prop.SetScalarOpacity(opacity_tf)
            if hasattr(streamer, "apply_main_channel_to_nov"):
                streamer.apply_main_channel_to_nov(channel_id)
        if _refs["view"]:
            _refs["view"].update()
        nov_view = _refs.get("nov_view")
        if nov_view and hasattr(nov_view, "update"):
            nov_view.update()

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
            if hasattr(streamer, "apply_main_channel_to_nov"):
                streamer.apply_main_channel_to_nov(channel_id)
        if _refs["view"]:
            _refs["view"].update()
        nov_view = _refs.get("nov_view")
        if nov_view and hasattr(nov_view, "update"):
            nov_view.update()

    _refs["biomni_client"] = None
    _refs["last_tile_channel_stats"] = None  # populated on right-click tile selection

    def _get_biomni_client():
        """Get or create the local Biomni client."""
        url = f"http://localhost:{state.biomni_port}"

        if _refs["biomni_client"] is None:
            _refs["biomni_client"] = BiomniLocalClient(base_url=url)

        if _refs["biomni_client"].base_url != url:
            _refs["biomni_client"] = BiomniLocalClient(base_url=url)

        return _refs["biomni_client"]

    def chatbot_login():
        """Initialise the Biomni agent on the local server."""
        print(f"[callbacks] Biomni init requested with llm={state.biomni_model}, db_llm={state.biomni_db_model}, mode={state.biomni_mode}")
        state.chatbot_loading = True

        try:
            client = _get_biomni_client()
            client.init(
                llm=state.biomni_model,
                db_llm=state.biomni_db_model,
                mode=state.biomni_mode,
                dataset=state.biomni_dataset,
                api_key=os.getenv("ANTHROPIC_API_KEY"),
            )
            state.chatbot_authenticated = True
            state.chatbot_messages = []
            print("[callbacks] Biomni initialised successfully")

        except Exception as e:
            error_msg = f"Initialisation failed: {e}"
            print(f"[callbacks] {error_msg}")
            state.chatbot_authenticated = False
            state.chatbot_messages = [{"role": "error", "content": error_msg}]
        finally:
            state.chatbot_loading = False

    _refs["biomni_pending_file"] = None  # temp path of file waiting to be uploaded

    def biomni_add_data(file_info):
        """Stage a file for upload (saves to temp, waits for Upload button)."""
        state.biomni_upload_success = False
        try:
            file_name = file_info.get("name", "upload")
            file_content = file_info.get("content")
            _, ext = os.path.splitext(file_name)

            fd, temp_path = tempfile.mkstemp(prefix="biomni_file_upload_", suffix=ext)
            with os.fdopen(fd, 'wb') as f:
                f.write(file_content)

            _refs["biomni_pending_file"] = temp_path
            print(f"[callbacks] Staged file '{file_name}' at {temp_path}")
        except Exception as e:
            print(f"[callbacks] Error staging file: {e}")

    def biomni_upload_file():
        """Upload the staged file to Biomni with its description."""
        state.biomni_upload_success = False
        temp_path = _refs.get("biomni_pending_file")
        description = (state.biomni_file_description or "").strip()

        if not temp_path:
            print("[callbacks] No file selected")
            return
        if not description:
            print("[callbacks] No description provided")
            return

        try:
            client = _get_biomni_client()
            client.upload(file_path=temp_path, description=description)
            state.biomni_upload_success = True
            _refs["biomni_pending_file"] = None
            print(f"[callbacks] File uploaded to Biomni successfully")
        except Exception as e:
            state.biomni_upload_success = False
            print(f"[callbacks] Error uploading file: {e}")


    def _build_markers() -> list[str]:
        """Build the markers list from active channels and their colors."""
        markers = []
        for ch_id in (state.active_channels or []):
            for ch in (state.channels or []):
                if ch["id"] == ch_id:
                    color = ch.get("color", "#FFFFFF").upper()
                    markers.append(f"{ch['name']}:{color}")
                    break
        return markers

    def _require_tile_stats() -> dict | None:
        """Return cached tile channel_stats, or add an error message and return None."""
        cs = _refs.get("last_tile_channel_stats")
        if not cs:
            state.chatbot_messages = state.chatbot_messages + [{
                "role": "error",
                "content": "No tile selected. Right-click a heatmap tile first.",
            }]
        return cs

    def chatbot_send_message():
        """Send a free-form /query using the active markers + tile stats + screenshot."""
        if not state.chatbot_input or not state.chatbot_input.strip():
            return

        if not state.chatbot_authenticated:
            print("[callbacks] Cannot send message - Biomni not initialised")
            return

        channel_stats = _refs.get("last_tile_channel_stats")  # Optional — None if no tile selected

        user_text = state.chatbot_input.strip()
        print(f"[callbacks] Biomni query: {user_text}")

        state.chatbot_messages = state.chatbot_messages + [
            {"role": "user", "content": user_text}
        ]
        state.chatbot_input = ""
        state.chatbot_loading = True

        try:
            client = _get_biomni_client()
            markers = _build_markers()
            screenshot_base64 = capture_screenshot()

            result = client.query(markers, user_text, channel_stats, image=screenshot_base64)
            response_text = result.get("answer", str(result))

            state.chatbot_messages = state.chatbot_messages + [
                {"role": "assistant", "content": response_text}
            ]
            print("[callbacks] Biomni query response received")

        except Exception as e:
            error_msg = f"Error: {e}"
            print(f"[callbacks] Biomni query error: {error_msg}")
            state.chatbot_messages = state.chatbot_messages + [
                {"role": "error", "content": error_msg}
            ]
        finally:
            state.chatbot_loading = False

    def chatbot_label():
        """Run a /label call using active markers + tile stats + screenshot."""
        if not state.chatbot_authenticated:
            print("[callbacks] Cannot label - Biomni not initialised")
            return

        channel_stats = _require_tile_stats()
        if channel_stats is None:
            return

        markers = _build_markers()
        if not markers:
            state.chatbot_messages = state.chatbot_messages + [
                {"role": "error", "content": "No active channels to label."}
            ]
            return

        label_display = ", ".join(m.split(":")[0] for m in markers)
        print(f"[callbacks] Biomni label: {label_display}")
        state.chatbot_messages = state.chatbot_messages + [
            {"role": "user", "content": f"Label: {label_display}"}
        ]
        state.chatbot_loading = True

        try:
            client = _get_biomni_client()
            screenshot_base64 = capture_screenshot()

            result = client.label(markers, channel_stats, image=screenshot_base64)

            import json as _json
            raw_labels = result.get("labels", {})
            overall = result.get("overall", [])
            response_json = _json.dumps(result, indent=2)

            state.chatbot_messages = state.chatbot_messages + [
                {"role": "assistant", "content": response_json, "format": "json"}
            ]
            print("[callbacks] Biomni label response received")

            if raw_labels:
                _apply_mesh_labels(raw_labels, overall)
                state.chatbot_labels_generated = True

        except Exception as e:
            error_msg = f"Error: {e}"
            print(f"[callbacks] Biomni label error: {error_msg}")
            state.chatbot_messages = state.chatbot_messages + [
                {"role": "error", "content": error_msg}
            ]
        finally:
            state.chatbot_loading = False

    def chatbot_suggest():
        """Run a /suggest call using active markers + tile stats + screenshot."""
        if not state.chatbot_authenticated:
            print("[callbacks] Cannot suggest - Biomni not initialised")
            return

        channel_stats = _require_tile_stats()
        if channel_stats is None:
            return

        markers = _build_markers()
        marker_display = ", ".join(m.split(":")[0] for m in markers) if markers else "(none)"
        print(f"[callbacks] Biomni suggest for: {marker_display}")
        state.chatbot_messages = state.chatbot_messages + [
            {"role": "user", "content": f"Suggest channels to add alongside: {marker_display}"}
        ]
        state.chatbot_loading = True

        try:
            client = _get_biomni_client()
            screenshot_base64 = capture_screenshot()

            result = client.suggest(markers, channel_stats, image=screenshot_base64)

            import json as _json
            suggestions = result.get("suggestions", [])
            priority_map = {"high": 3, "medium": 2, "low": 1}
            suggestion_items = []
            for s in suggestions:
                raw_priority = str(s.get("priority", "medium")).lower()
                dots = priority_map.get(raw_priority, 2)
                suggestion_items.append({
                    "channel": s.get("channel", ""),
                    "reason": s.get("reason", ""),
                    "dots": dots,
                })

            state.chatbot_messages = state.chatbot_messages + [{
                "role": "assistant",
                "content": _json.dumps(result, indent=2),
                "format": "suggest",
                "suggestions": suggestion_items,
            }]
            print("[callbacks] Biomni suggest response received")

        except Exception as e:
            error_msg = f"Error: {e}"
            print(f"[callbacks] Biomni suggest error: {error_msg}")
            state.chatbot_messages = state.chatbot_messages + [
                {"role": "error", "content": error_msg}
            ]
        finally:
            state.chatbot_loading = False


    def chatbot_explain_upset():
        """Explain the currently displayed UpSet plot via /plot."""
        if not state.chatbot_authenticated:
            print("[callbacks] Cannot explain plot - Biomni not initialised")
            return

        source_data = list(state.upset_data_local if state.upset_view_mode == "local" else state.upset_data)
        offset = state.upset_offset
        limit = state.upset_limit
        visible_data = source_data[offset:offset + limit]

        active_channel_names = [
            ch["name"] for ch in (state.channels or [])
            if ch["id"] in (state.active_channels or [])
        ]

        plot_payload = {
            "type": "upset",
            "view_mode": state.upset_view_mode,
            "selected_channels": list(state.upset_selected_channels or []),
            "active_channels": active_channel_names,
            "filters": {
                "offset": offset,
                "limit": limit,
                "selected_only": list(state.upset_selected_channels or []),
                "min_channels": state.upset_min_channels,
            },
            "data": source_data,
            "visible_data": visible_data,
        }

        state.chatbot_panel_open = True
        state.chatbot_messages = list(state.chatbot_messages) + [
            {"role": "user", "content": "Explain the UpSet plot"}
        ]
        state.chatbot_loading = True

        try:
            client = _get_biomni_client()
            markers = _build_markers()
            result = client.plot(plot_payload, markers=markers, mode=state.biomni_mode)
            response_text = result.get("answer", str(result))
            state.chatbot_messages = list(state.chatbot_messages) + [
                {"role": "assistant", "content": response_text}
            ]
            print("[callbacks] Biomni explain upset response received")
        except Exception as e:
            error_msg = f"Error: {e}"
            print(f"[callbacks] Biomni explain upset error: {error_msg}")
            state.chatbot_messages = list(state.chatbot_messages) + [
                {"role": "error", "content": error_msg}
            ]
        finally:
            state.chatbot_loading = False

    def chatbot_explain_bar():
        """Explain the currently displayed bar chart via /plot."""
        if not state.chatbot_authenticated:
            print("[callbacks] Cannot explain plot - Biomni not initialised")
            return

        raw_data = list(state.bar_data_local if state.bar_view_mode == "local" else state.bar_data)
        offset = state.bar_offset
        limit = state.bar_limit
        # normalise tuples/lists → dicts for the LLM
        source_data = [
            {"channel": e[0], "coverage_pct": e[1]} if isinstance(e, (list, tuple)) else e
            for e in raw_data
        ]
        visible_data = source_data[offset:offset + limit]

        active_channel_names = [
            ch["name"] for ch in (state.channels or [])
            if ch["id"] in (state.active_channels or [])
        ]

        plot_payload = {
            "type": "bar",
            "view_mode": state.bar_view_mode,
            "selected_channels": list(state.bar_selected_channels or []),
            "active_channels": active_channel_names,
            "filters": {
                "offset": offset,
                "limit": limit,
                "selected_only": list(state.bar_selected_channels or []),
            },
            "data": source_data,
            "visible_data": visible_data,
        }

        state.chatbot_panel_open = True
        state.chatbot_messages = list(state.chatbot_messages) + [
            {"role": "user", "content": "Explain the bar chart"}
        ]
        state.chatbot_loading = True

        try:
            client = _get_biomni_client()
            markers = _build_markers()
            result = client.plot(plot_payload, markers=markers, mode=state.biomni_mode)
            response_text = result.get("answer", str(result))
            state.chatbot_messages = list(state.chatbot_messages) + [
                {"role": "assistant", "content": response_text}
            ]
            print("[callbacks] Biomni explain bar response received")
        except Exception as e:
            error_msg = f"Error: {e}"
            print(f"[callbacks] Biomni explain bar error: {error_msg}")
            state.chatbot_messages = list(state.chatbot_messages) + [
                {"role": "error", "content": error_msg}
            ]
        finally:
            state.chatbot_loading = False

    def chatbot_clear():
        """Clear the chatbot conversation history."""
        print("[callbacks] Clearing chatbot messages")
        state.chatbot_messages = []
        state.chatbot_input = ""

    def toggle_labels():
        """Show or hide label actors in the scene."""
        state.show_labels = not state.show_labels
        label_mgr = _refs.get("label_manager")
        if not state.show_labels:
            if label_mgr:
                label_mgr.clear()
            v = _refs.get("view")
            if v:
                v.update()
        else:
            if label_mgr:
                refresh_labels()

    def deselect_tile():
        """Deselect the current tile: remove surface meshes, clear labels, reset state."""
        print("[callbacks] Deselecting tile")
        mesh_mgr = _refs.get("mesh_manager")
        if mesh_mgr:
            for ch_id in list(state.active_channels):
                mesh_mgr.deactivate_channel_mesh(ch_id)
        label_mgr = _refs.get("label_manager")
        if label_mgr:
            label_mgr.clear()
            _refs["label_manager"] = None
        state.selected_tile = None
        _refs.pop("last_tile_channel_stats", None)
        state.chatbot_labels_generated = False
        state.anchor_labels = False
        v = _refs.get("view")
        if v:
            v.update()

    def _apply_mesh_labels(raw_labels: dict, overall: list):
        """Create/restart LabelSceneManager with the new labels from Biomni /label."""
        from bioset.scene.labels import LabelSceneManager
        renderer = _refs.get("renderer")
        mesh_mgr = _refs.get("mesh_manager")
        if renderer is None or mesh_mgr is None or not mesh_mgr.is_available:
            print("[callbacks] Cannot apply labels: missing renderer or mesh_manager")
            return

        polydata_by_name = {}
        for ch_id in (state.active_channels or []):
            ch_name = next((ch["name"] for ch in state.channels if ch["id"] == ch_id), None)
            if ch_name is None:
                continue
            manifest_idx = mesh_mgr.channel_idx_for_name(ch_name)
            if manifest_idx is None:
                continue
            pd = mesh_mgr.get_channel_polydata(manifest_idx)
            if pd is not None:
                polydata_by_name[ch_name] = pd

        if not polydata_by_name:
            print("[callbacks] No mesh polydata available for label placement")
            return

        label_mgr = _refs.get("label_manager")
        if label_mgr is None:
            label_mgr = LabelSceneManager(renderer)
            _refs["label_manager"] = label_mgr
        else:
            label_mgr.clear()

        label_mgr.start_preprocessing(polydata_by_name, raw_labels, overall)
        print(f"[callbacks] Label preprocessing started for {list(polydata_by_name.keys())}")

    def check_label_setup():
        """Poll for completed label preprocessing; call from the app poll loop."""
        label_mgr = _refs.get("label_manager")
        if label_mgr is None:
            return
        if label_mgr.check_and_apply_setup():
            # Preprocessing just finished — do an initial placement pass
            if label_mgr.update():
                view = _refs.get("view")
                if view:
                    view.update()

    def refresh_labels():
        """Force label recompute and redraw for the current camera position."""
        label_mgr = _refs.get("label_manager")
        if label_mgr and label_mgr.update():
            v = _refs.get("view")
            if v:
                v.update()

    def setup_label_interaction_observer(interactor):
        """Register EndInteractionEvent observer to refresh labels on camera move."""
        def _on_end_interaction(obj, event):
            if state.anchor_labels:
                return  # labels are pinned — skip recompute
            if not state.show_labels:
                return  # labels are hidden — skip recompute
            refresh_labels()

        interactor.AddObserver("EndInteractionEvent", _on_end_interaction)
        print("[callbacks] Label EndInteractionEvent observer registered")

    def capture_screenshot():
        """Capture current VTK view as base64-encoded PNG."""
        import base64
        png_bytes = capture_screenshot_png_bytes(_refs.get("streamer"))
        return base64.b64encode(png_bytes).decode("utf-8") if png_bytes else None

    def _print_tile_channel_stats(tile, level, dilation):
        """Print per-channel stats and build the channel_stats dict stored in _refs."""
        loader = _refs.get("analysis_loader")
        if not loader or not loader.is_loaded:
            return
        stats = loader.get_tile_channel_stats(tile.x0, tile.y0, level, dilation)
        print(f"[picker] Channel stats — tile ({tile.x0},{tile.y0}) "
              f"level={level} dilation={dilation}:")
        if stats:
            print(f"  {'Channel':<20} {'Voxels':>10} {'MeanInt':>10} {'SumInt':>14}")
            print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*14}")
            for row in stats:
                print(f"  {row['channel']:<20} {row['voxel_count']:>10} "
                      f"{row['mean_intensity']:>10.3f} {row['sum_intensity']:>14.1f}")
        else:
            print("  (no data for this tile / dilation)")

        # total voxels in this tile region (width × height in base voxels × z depth)
        base_tile_px = 128
        width_vox = (tile.x1 - tile.x0) * base_tile_px
        height_vox = (tile.y1 - tile.y0) * base_tile_px
        bounds = loader.metadata.volume_bounds if loader.metadata else {}
        z_depth = max(1, bounds["z"][1] - bounds["z"][0]) if bounds and "z" in bounds else 1
        total_voxels = width_vox * height_vox * z_depth

        dtype_max = loader.metadata.dtype_max if loader.metadata else 65535

        _refs["last_tile_channel_stats"] = {
            "dtype_max": dtype_max,
            "total_voxels": total_voxels,
            "channels": {
                row["channel"]: {
                    "mean_intensity": row["mean_intensity"],
                    "segmented_voxels": row["voxel_count"],
                }
                for row in stats
            },
        }
        print(f"[picker] channel_stats cached: {len(stats)} channels, "
              f"total_voxels={total_voxels}, dtype_max={dtype_max}")

    def setup_right_click_picker(interactor):
        """Register a VTK prop picker on right-click to select heatmap tiles."""
        from vtkmodules.vtkRenderingCore import vtkPropPicker
        
        # Avoid registering the same observer multiple times on the same interactor.
        if getattr(interactor, "_bioset_right_click_picker_registered", False):
            return
        setattr(interactor, "_bioset_right_click_picker_registered", True)

        picker = vtkPropPicker()

        def _on_right_button_press(obj, event):
            click_pos = obj.GetEventPosition()
            heatmap = _refs.get("heatmap")
            mesh_mgr = _refs.get("mesh_manager")
            streamer = _refs.get("streamer")

            if not heatmap or not streamer:
                return

            # Pick from the renderer that contains heatmap tile actors
            outline_only = getattr(state, 'heatmap_outline_only', 'filled') == 'outline'
            if outline_only and heatmap.outline_renderer is not None:
                pick_renderer = heatmap.outline_renderer
            else:
                pick_renderer = heatmap.renderer

            picker.Pick(click_pos[0], click_pos[1], 0, pick_renderer)
            picked_actor = picker.GetActor()

            if picked_actor is None:
                print(f"[picker] No actor at ({vtk_x}, {vtk_y})")
                return

            tile = heatmap.get_tile_for_actor(picked_actor)
            if tile is None:
                print(f"[picker] Picked actor is not a heatmap tile")
                return

            print(f"[picker] Picked heatmap tile: x0={tile.x0}, y0={tile.y0}, "
                f"x1={tile.x1}, y1={tile.y1}, frac={tile.active_fraction:.3f}")
            _print_tile_channel_stats(tile, state.current_hierarchy_level, state.current_dilation)

            sx = getattr(state, 'physical_size_x', 0.14)
            sy = getattr(state, 'physical_size_y', 0.14)
            sz = getattr(state, 'physical_size_z', 0.28)

            #   world_x = tile_coord * spacing * 128
            tile_center_x = (tile.x0 + tile.x1) / 2.0 * sx * 128
            tile_center_y = (tile.y0 + tile.y1) / 2.0 * sy * 128
            tile_center_z = 0.0

            tile_width_world = (tile.x1 - tile.x0) * sx * 128
            tile_height_world = (tile.y1 - tile.y0) * sy * 128
            tile_extent = max(tile_width_world, tile_height_world)

            cam = streamer.renderer.GetActiveCamera()
            cam.SetFocalPoint(tile_center_x, tile_center_y, tile_center_z)
            cam.SetPosition(tile_center_x, tile_center_y, tile_center_z + tile_extent * 6.0)
            cam.SetViewUp(0, 1, 0)
            streamer.renderer.ResetCameraClippingRange()
            
            print(f"[picker] Camera -> tile center ({tile_center_x:.1f}, {tile_center_y:.1f}), "
                f"extent={tile_extent:.1f}")
            
            active_channels = list(state.active_channels)
            if mesh_mgr and mesh_mgr.is_available and active_channels:
                vox_x = (tile.x0 + tile.x1) / 2.0 * 128
                vox_y = (tile.y0 + tile.y1) / 2.0 * 128
                state.selected_tile = None

                for ch_id in active_channels:
                    mesh_tile = mesh_mgr.find_tile_at_voxel(ch_id, vox_x, vox_y)
                    if not mesh_tile:
                        print(f"[picker] No mesh tile for ch {ch_id} at voxel ({vox_x:.0f}, {vox_y:.0f})")
                        continue
                    if state.selected_tile is None:
                        state.selected_tile = {"tile_x": mesh_tile.tile_x, "tile_y": mesh_tile.tile_y}
                        print(f"[picker] Found mesh tile: ({mesh_tile.tile_x}, {mesh_tile.tile_y})")
                    if ch_id not in state.surface_hidden_channels:
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

            if _refs["view"]:
                _refs["view"].update()


        interactor.AddObserver("RightButtonPressEvent", _on_right_button_press)
        print("[callbacks] Right-click picker registered on interactor")

    def on_right_click(px, py):
        """Handle right-click from client JS (contextmenu) on the VTK canvas."""
        heatmap = _refs.get("heatmap")
        mesh_mgr = _refs.get("mesh_manager")
        streamer = _refs.get("streamer")
        if not heatmap or not streamer:
            return

        renderer_main = streamer.renderer
        render_window = renderer_main.GetRenderWindow()
        win_size = render_window.GetSize()

        vtk_x = int(px)
        vtk_y = int(win_size[1] - int(py))

        # Pick against heatmap tile actors (tiles live in heatmap renderers, not in the main volume renderer).
        from vtkmodules.vtkRenderingCore import vtkPropPicker
        picker = vtkPropPicker()

        candidate_renderers = []
        if getattr(heatmap, "outline_renderer", None) is not None:
            candidate_renderers.append(heatmap.outline_renderer)
        if getattr(heatmap, "renderer", None) is not None:
            candidate_renderers.append(heatmap.renderer)
        candidate_renderers.append(renderer_main)

        picked_actor = None
        for ren in candidate_renderers:
            try:
                picker.Pick(vtk_x, vtk_y, 0, ren)
                picked_actor = picker.GetActor()
            except Exception:
                picked_actor = None
            if picked_actor is not None:
                break

        if picked_actor is None:
            return
        tile = heatmap.get_tile_for_actor(picked_actor)
        if tile is None:
            return

        sx = getattr(state, "physical_size_x", 0.14)
        sy = getattr(state, "physical_size_y", 0.14)
        tile_center_x = (tile.x0 + tile.x1) / 2.0 * sx * 128
        tile_center_y = (tile.y0 + tile.y1) / 2.0 * sy * 128
        tile_center_z = 0.0
        tile_width_world = (tile.x1 - tile.x0) * sx * 128
        tile_height_world = (tile.y1 - tile.y0) * sy * 128
        tile_extent = max(tile_width_world, tile_height_world)

        cam = renderer_main.GetActiveCamera()
        cam.SetFocalPoint(tile_center_x, tile_center_y, tile_center_z)
        cam.SetPosition(tile_center_x, tile_center_y, tile_center_z + tile_extent * 4.0)
        cam.SetViewUp(0, 1, 0)
        renderer_main.ResetCameraClippingRange()

        active_channels = list(getattr(state, "active_channels", []) or [])
        if mesh_mgr and mesh_mgr.is_available and active_channels:
            vox_x = (tile.x0 + tile.x1) / 2.0 * 128
            vox_y = (tile.y0 + tile.y1) / 2.0 * 128
            state.selected_tile = None
            for ch_id in active_channels:
                mesh_tile = mesh_mgr.find_tile_at_voxel(ch_id, vox_x, vox_y)
                if not mesh_tile:
                    continue
                if state.selected_tile is None:
                    state.selected_tile = {"tile_x": mesh_tile.tile_x, "tile_y": mesh_tile.tile_y}
                if ch_id not in state.surface_hidden_channels:
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

        if _refs.get("view"):
            _refs["view"].update()

    _hover_last_actor = [None]
    def on_hover(px, py):
        """Handle throttled mousemove from client JS"""
        heatmap = _refs.get("heatmap")
        streamer = _refs.get("streamer")
        if not heatmap or not streamer:
            return

        # Pick from the renderer that actually contains the heatmap tile actors:
        # outline_renderer (layer 2) when in outline mode, fill renderer (layer 0) otherwise.
        outline_only = getattr(state, 'heatmap_outline_only', 'filled') == 'outline'
        if outline_only and heatmap.outline_renderer is not None:
            pick_renderer = heatmap.outline_renderer
        else:
            pick_renderer = heatmap.renderer

        render_window = streamer.renderer.GetRenderWindow()
        win_size = render_window.GetSize()

        vtk_y = win_size[1] - int(py)
        vtk_x = int(px)

        from vtkmodules.vtkRenderingCore import vtkPropPicker
        hover_picker = vtkPropPicker()

        hover_picker.Pick(vtk_x, vtk_y, 0, pick_renderer)
        picked_actor = hover_picker.GetActor()

        prev = _hover_last_actor[0]

        if picked_actor is prev:
            return

        if prev is not None:
            prev.GetProperty().EdgeVisibilityOff()

        if picked_actor is not None and heatmap.get_tile_for_actor(picked_actor) is not None:
            picked_actor.GetProperty().EdgeVisibilityOn()
            picked_actor.GetProperty().SetEdgeColor(0.0, 0.0, 0.0)
            picked_actor.GetProperty().SetLineWidth(5.0)
            _hover_last_actor[0] = picked_actor
        else:
            _hover_last_actor[0] = None

        if _refs["view"]:
            _refs["view"].update()

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

            # Pick from the renderer that contains heatmap tile actors
            outline_only = getattr(state, 'heatmap_outline_only', 'filled') == 'outline'
            if outline_only and heatmap.outline_renderer is not None:
                pick_renderer = heatmap.outline_renderer
            else:
                pick_renderer = heatmap.renderer

            picker.Pick(click_pos[0], click_pos[1], 0, pick_renderer)
            picked_actor = picker.GetActor()

            if picked_actor is None:
                print(f"[picker] No actor at ({click_pos[0]}, {click_pos[1]})")
                return

            tile = heatmap.get_tile_for_actor(picked_actor)
            if tile is None:
                print(f"[picker] Picked actor is not a heatmap tile")
                return

            print(f"[picker] Picked heatmap tile: x0={tile.x0}, y0={tile.y0}, "
                f"x1={tile.x1}, y1={tile.y1}, frac={tile.active_fraction:.3f}")
            _print_tile_channel_stats(tile, state.current_hierarchy_level, state.current_dilation)

            sx = getattr(state, 'physical_size_x', 0.14)
            sy = getattr(state, 'physical_size_y', 0.14)
            sz = getattr(state, 'physical_size_z', 0.28)

            #   world_x = tile_coord * spacing * 128
            tile_center_x = (tile.x0 + tile.x1) / 2.0 * sx * 128
            tile_center_y = (tile.y0 + tile.y1) / 2.0 * sy * 128
            tile_center_z = 0.0

            tile_width_world = (tile.x1 - tile.x0) * sx * 128
            tile_height_world = (tile.y1 - tile.y0) * sy * 128
            tile_extent = max(tile_width_world, tile_height_world)

            cam = streamer.renderer.GetActiveCamera()
            cam.SetFocalPoint(tile_center_x, tile_center_y, tile_center_z)
            cam.SetPosition(tile_center_x, tile_center_y, tile_center_z + tile_extent * 4.0)
            cam.SetViewUp(0, 1, 0)
            streamer.renderer.ResetCameraClippingRange()
            
            print(f"[picker] Camera -> tile center ({tile_center_x:.1f}, {tile_center_y:.1f}), "
                f"extent={tile_extent:.1f}")
            
            active_channels = list(state.active_channels)
            if mesh_mgr and mesh_mgr.is_available and active_channels:
                vox_x = (tile.x0 + tile.x1) / 2.0 * 128
                vox_y = (tile.y0 + tile.y1) / 2.0 * 128
                state.selected_tile = None

                for ch_id in active_channels:
                    mesh_tile = mesh_mgr.find_tile_at_voxel(ch_id, vox_x, vox_y)
                    if not mesh_tile:
                        print(f"[picker] No mesh tile for ch {ch_id} at voxel ({vox_x:.0f}, {vox_y:.0f})")
                        continue
                    if state.selected_tile is None:
                        state.selected_tile = {"tile_x": mesh_tile.tile_x, "tile_y": mesh_tile.tile_y}
                        print(f"[picker] Found mesh tile: ({mesh_tile.tile_x}, {mesh_tile.tile_y})")
                    if ch_id not in state.surface_hidden_channels:
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

            if _refs["view"]:
                _refs["view"].update()


        interactor.AddObserver("RightButtonPressEvent", _on_right_button_press)
        print("[callbacks] Right-click picker registered on interactor")

    _hover_last_actor = [None]
    def on_hover(px, py):
        """Handle throttled mousemove from client JS"""
        heatmap = _refs.get("heatmap")
        streamer = _refs.get("streamer")
        if not heatmap or not streamer:
            return

        # Pick from the renderer that actually contains the heatmap tile actors:
        # outline_renderer (layer 2) when in outline mode, fill renderer (layer 0) otherwise.
        outline_only = getattr(state, 'heatmap_outline_only', 'filled') == 'outline'
        if outline_only and heatmap.outline_renderer is not None:
            pick_renderer = heatmap.outline_renderer
        else:
            pick_renderer = heatmap.renderer

        render_window = streamer.renderer.GetRenderWindow()
        win_size = render_window.GetSize()

        vtk_y = win_size[1] - int(py)
        vtk_x = int(px)

        from vtkmodules.vtkRenderingCore import vtkPropPicker
        hover_picker = vtkPropPicker()

        hover_picker.Pick(vtk_x, vtk_y, 0, pick_renderer)
        picked_actor = hover_picker.GetActor()

        prev = _hover_last_actor[0]

        if picked_actor is prev:
            return

        if prev is not None:
            prev.GetProperty().EdgeVisibilityOff()

        if picked_actor is not None and heatmap.get_tile_for_actor(picked_actor) is not None:
            picked_actor.GetProperty().EdgeVisibilityOn()
            picked_actor.GetProperty().SetEdgeColor(0.0, 0.0, 0.0)
            picked_actor.GetProperty().SetLineWidth(5.0)
            _hover_last_actor[0] = picked_actor
        else:
            _hover_last_actor[0] = None

        if _refs["view"]:
            _refs["view"].update()

    def generate_pdf_report(report_data=None):
        """
        API endpoint to generate and download a PDF report.
        report_data: dict with 'title', 'params', 'channels', etc.
        """
        report_data = []

        if state.export_general:
            general_content = GeneralContent(state.zarr_url, state.metadata_url, datetime.datetime.now())
            general = General(general_content)
            report_data.append(general)

        if state.export_analysis:
            dataset_content = AnalysisDatasetContent(state.analysis_file_name, state.analysis_channels)
            dataset = AnalysisDataset(dataset_content)
            report_data.append(dataset)

        if state.export_chat:
            chat_contents = []
            for message in state.chatbot_messages:
                chat_contents.append(ChatContent(message["content"], message["role"] == "user"))

            llm_settings = LLMSettings(state.biomni_model, state.biomni_mode)
            chat = Chat(chat_contents, llm_settings)
            report_data.append(chat)

        if state.export_bookmarks:
            bookmark_content = load_all_bookmarks("src/bioset/bookmark/recordings")
            bookmarks = Bookmarks(bookmark_content)
            report_data.append(bookmarks)

        try:
            pdf_bytes = generate_report_bytes(report_data)
            filename = f"BioSET_Report_{datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')}.pdf"

            with open(filename, "wb") as f:
                f.write(pdf_bytes)

            print(f"[callbacks] Saved PDF to: {os.path.abspath(filename)}")

        except Exception as e:
            print(f"[callbacks] Error generating report: {e}")

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
    ctrl.toggle_channel_surface = toggle_channel_surface
    ctrl.on_channel_range_change = on_channel_range_change
    ctrl.update_upset_data = update_upset_data
    ctrl.update_upset_data_local = update_upset_data_local
    ctrl.update_bar_data = update_bar_data
    ctrl.update_bar_data_local = update_bar_data_local
    ctrl.chatbot_login = chatbot_login
    ctrl.biomni_add_data = biomni_add_data
    ctrl.biomni_upload_file = biomni_upload_file
    ctrl.chatbot_send_message = chatbot_send_message
    ctrl.chatbot_label = chatbot_label
    ctrl.chatbot_suggest = chatbot_suggest
    ctrl.chatbot_explain_upset = chatbot_explain_upset
    ctrl.chatbot_explain_bar = chatbot_explain_bar
    ctrl.chatbot_clear = chatbot_clear
    ctrl.toggle_labels = toggle_labels
    ctrl.deselect_tile = deselect_tile
    ctrl.set_mesh_manager = set_mesh_manager
    ctrl.setup_right_click_picker = setup_right_click_picker
    ctrl.set_heatmap_lod = set_heatmap_lod
    ctrl.set_heatmap_lod_auto_mode = set_heatmap_lod_auto_mode
    ctrl.trigger("on_hover")(on_hover)
    ctrl.generate_pdf_report = generate_pdf_report
    ctrl.set_renderer = set_renderer
    ctrl.refresh_labels = refresh_labels
    ctrl.check_label_setup = check_label_setup
    ctrl.setup_label_interaction_observer = setup_label_interaction_observer

