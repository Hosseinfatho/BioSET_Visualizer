from __future__ import annotations

import datetime
import hashlib
import os
import tempfile
import time
from pathlib import Path

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


# Rows sent to the browser per UpSet refresh.
#
# This is a DISPLAY cap, not a correctness one: the channel filter is applied
# inside the query now, so the rows that come back are already the relevant
# ones and truncating them only limits how deep you can page. It has to stay
# modest because every refresh serialises the whole list over the same
# websocket that carries the rendered frames — at 20k rows that was 2.4 MB per
# refresh against 0.1 MB here, which starved the volume's high-res resolve.
# 1000 rows is ~16 pages at the largest page size.
UPSET_ROW_CAP = 1000


def register_callbacks(ctrl, state, view, streamer=None):
    """Register all controller methods."""
    from bioset.ui.utils.scale_bar import compute_scale_bar

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
        "mesh_streamer": None,
        "contours": None,
        "heatmap_lod": None,
        "viewport_plots": None,
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
        _attach_main_scale_bar_observer(streamer)
        update_main_scale_bar()
        if hasattr(ctrl, "nov_push_view") and getattr(streamer, "nov_render_callback", None) is None:
            def _on_nov_rendered():
                if hasattr(ctrl, "update_nov_scale_bar"):
                    try:
                        ctrl.update_nov_scale_bar()
                    except Exception:
                        pass
                ctrl.nov_push_view()
            streamer.nov_render_callback = _on_nov_rendered

    def set_heatmap(heatmap):
        """Set the heatmap renderer reference."""
        _refs["heatmap"] = heatmap
        print(f"[callbacks] Heatmap renderer set: {heatmap}")

    def set_integrated_heatmap(manager):
        """Set the integrated-heatmap shader manager reference."""
        _refs["integrated_heatmap"] = manager
        print(f"[callbacks] Integrated heatmap manager set: {manager}")

    def set_mesh_manager(mesh_manager):
        """Set the mesh manager reference."""
        _refs["mesh_manager"] = mesh_manager
        print(f"[callbacks] Mesh manager set: {mesh_manager}"
              f" (available={mesh_manager.is_available if mesh_manager else False})")

    def set_contours(contours):
        """Set the iso-contour renderer used by the integrated heatmap mode."""
        _refs["contours"] = contours
        print(f"[callbacks] Contour renderer set: {contours}")

    def set_mesh_streamer(mesh_streamer):
        """Set the viewport-driven mesh tile streamer."""
        _refs["mesh_streamer"] = mesh_streamer
        print(f"[callbacks] Mesh streamer set: {mesh_streamer}")

    def set_heatmap_lod(heatmap_lod):
        """Set the heatmap LOD renderer reference."""
        _refs["heatmap_lod"] = heatmap_lod
        # Re-upload the shader maps whenever the worker lands a new level, so
        # gain and sampling resolve with zoom the way the grid heatmap does.
        heatmap_lod.set_shader_maps_hook(_reupload_shader_maps)
        print(f"[callbacks] Heatmap LOD set: {heatmap_lod}")

    def _reupload_shader_maps(level: int) -> bool:
        """LOD worker landed `level` while in integrated mode.

        Returns True when the maps changed, so the poll loop redraws.
        """
        mgr = _refs.get("integrated_heatmap")
        loader = _refs.get("analysis_loader")
        combo = state.heatmap_combination or []
        if mgr is None or loader is None or not combo:
            return False
        name_to_id = {ch["name"]: ch["id"] for ch in (state.channels or [])}
        inter, members = mgr.compute_maps(
            loader, combo, state.current_dilation, name_to_id,
            list(state.active_channels or []), hierarchy_level=level)
        mgr.set_maps(inter, members)
        if inter is None:
            return False
        print(f"[callbacks] Shader maps re-uploaded at level {level}: "
              f"{inter.shape[1]}x{inter.shape[0]}")
        return True

    def set_viewport_plots(viewport_plots):
        """Set the viewport plot computer reference."""
        _refs["viewport_plots"] = viewport_plots
        print(f"[callbacks] Viewport plots set: {viewport_plots}")

    def set_interactor(interactor):
        """Set the main VTK interactor for bookmark flag picking."""
        _refs["interactor"] = interactor

    ctrl.set_interactor = set_interactor

    def set_renderer(renderer):
        """Set the main VTK renderer reference (used by label scene manager)."""
        _refs["renderer"] = renderer

    def set_heatmap_lod_auto_mode(enabled: bool):
        """Set heatmap LOD auto mode (controlled by UI toggle)."""
        heatmap_lod = _refs.get("heatmap_lod")
        if heatmap_lod and hasattr(heatmap_lod, "set_auto_mode"):
            heatmap_lod.set_auto_mode(enabled)

    register_bookmark_callbacks(ctrl, state, _refs)
    register_nov_callbacks(ctrl, state, _refs)

    def update_main_scale_bar():
        """Compute/update scale bar for the main (non-NOV) view. Uses current LOD (comp) from streamer for accurate µm per voxel."""
        s = _refs.get("streamer")
        if not s or not getattr(s, "renderer", None) or not getattr(s, "render_window", None):
            state.main_scale_bar_label = ""
            state.main_scale_bar_width_px = 0
            return
        # Current LOD: same comp that is printed in terminal when zoom/load updates
        comp = getattr(s, "_last_component", None)
        if comp is None and getattr(s, "state", None):
            for ch in s.get_active_channels() if hasattr(s, "get_active_channels") else []:
                st = s.state.get(ch)
                if st is not None and hasattr(st, "component"):
                    comp = st.component
                    break
        base_xy = getattr(state, "physical_size_x", None) or getattr(state, "physical_size_y", 0.14)
        label, width_px = compute_scale_bar(
            renderer=s.renderer,
            render_window=s.render_window,
            unit=getattr(state, "size_unit", None) or "µm",
            component=comp,
            base_spacing_xy=base_xy,
        )
        state.main_scale_bar_label = label
        state.main_scale_bar_width_px = int(width_px or 0)

    def _attach_main_scale_bar_observer(s):
        """Attach a render observer once so main scale bar updates with zoom/render."""
        if not s or not getattr(s, "render_window", None):
            return
        rw = s.render_window
        if getattr(rw, "_bioset_main_scale_bar_observer", False):
            return

        # RenderEvent fires on every frame, including every interactive drag
        # frame — throttle so drags don't pay a scale-bar recompute + two
        # trame state writes per frame.
        _last_update = [0.0]

        def _on_render(_obj=None, _evt=None):
            now = time.monotonic()
            if now - _last_update[0] < 0.10:
                return
            _last_update[0] = now
            try:
                update_main_scale_bar()
            except Exception:
                pass

        try:
            rw.AddObserver("RenderEvent", _on_render)
            setattr(rw, "_bioset_main_scale_bar_observer", True)
        except Exception:
            pass

    ctrl.update_main_scale_bar = update_main_scale_bar

    def load_data():
        """Load data from zarr_url and metadata_url."""
        if state.data_loading:
            return
        state.data_loading = True
        print(f"[callbacks] Loading data...")
        print(f"[callbacks]   Zarr URL: {state.zarr_url}")
        print(f"[callbacks]   Metadata URL: {state.metadata_url}")
        
        try:
            from bioset.metadata import parse_ome_metadata, parse_zarr_attrs_metadata

            streamer = _refs.get("streamer")
            if streamer:
                streamer.set_zarr_url(state.zarr_url)

            # Metadata precedence:
            #  - Dimensions / units / channel COUNT: the ZARR store is the single
            #    source of truth. Read them from its embedded metadata; fall back
            #    to a separate OME-XML only when the store carries none.
            #  - Channel NAMES: come from the separate metadata ONLY when the
            #    "Separate metadata" panel is open. Otherwise use the store's own
            #    names (omero labels) or a generic default.
            embedded = None
            channel_count = None
            if streamer:
                try:
                    attrs = streamer.zsrc.root_attrs()
                    try:
                        # Channel count from the store's axis layout — 1 for a
                        # bare 3D (z,y,x) volume with no channel axis.
                        channel_count = streamer.zsrc.num_channels()
                    except Exception:
                        channel_count = None
                    embedded = parse_zarr_attrs_metadata(attrs, channel_count)
                except Exception as e:
                    print(f"[callbacks] Could not read embedded zarr metadata: {e}")

            # Consult the separate OME-XML when the panel is open (for channel
            # names) or when the store has no embedded metadata at all (then it is
            # the only available source, including for dimensions).
            external = None
            if (state.metadata_open or embedded is None) and state.metadata_url:
                try:
                    external = parse_ome_metadata(state.metadata_url)
                except Exception as e:
                    print(f"[callbacks] Could not read separate metadata: {e}")

            if embedded is None and external is None:
                raise ValueError(
                    "The zarr store has no embedded metadata and no separate "
                    "metadata is available (open the Separate metadata panel and "
                    "provide a URL)."
                )

            # Dimensions/units: the zarr wins; the separate file only fills in when
            # the store carries nothing.
            dims_src = embedded if embedded is not None else external
            phys_x = dims_src.physical_size_x
            phys_y = dims_src.physical_size_y
            phys_z = dims_src.physical_size_z
            size_unit = dims_src.size_unit or "µm"

            # Channel names from the separate file only when it is open (or is the
            # sole source); otherwise from the store. Channel COUNT is the zarr's.
            use_external_names = external is not None and (state.metadata_open or embedded is None)
            channel_count = channel_count or len(dims_src.channels)

            def _channel_name(i):
                if use_external_names and i < len(external.channels):
                    return external.channels[i].name
                if embedded is not None and i < len(embedded.channels):
                    return embedded.channels[i].name
                return f"Channel {i}"

            state.metadata_source = "external" if (
                external is not None and (use_external_names or embedded is None)
            ) else "embedded"

            state.physical_size_x = phys_x
            state.physical_size_y = phys_y
            state.physical_size_z = phys_z
            state.size_unit = size_unit

            if streamer:
                streamer.set_spacing(phys_x, phys_y, phys_z)
                # Pyramid depth and zoom thresholds come from the store itself,
                # so they must be derived after the URL and spacing are set.
                streamer.configure_lod_from_source()

            mesh_mgr = _refs.get("mesh_manager")
            if mesh_mgr:
                mesh_mgr.update_spacing(phys_x, phys_y, phys_z)

            channels = [
                {
                    "id": i,
                    "name": _channel_name(i),
                    "color": get_channel_color(i),
                    "color_dialog": False,
                    "range": [0, 100],
                }
                for i in range(channel_count)
            ]
            
            state.channels = channels
            state.active_channels = []
            # visible_channel_ids must be a list of channel IDs (the list UI does
            # visible_channel_ids.includes(ch.id)). Slicing covers both cases:
            # fewer channels than the default shows them all. (The old code
            # assigned the channel *dicts* when there were fewer than the default,
            # so a single-channel store rendered an empty list.)
            state.visible_channel_ids = [
                ch["id"] for ch in channels[:state.default_num_channels]]
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
                update_main_scale_bar()
            if _refs["view"]:
                # Resync the server render-window size/aspect to the client on
                # first load (a remote client can otherwise render stretched
                # until it resizes the browser); then push the frame.
                try:
                    _refs["view"].resize()
                except Exception:
                    pass
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
        # Surfaces are opt-in per channel: enabling one starts streaming the
        # tiles the viewport covers, disabling drops its actors at once. With
        # 7.5k tiles / 157M triangles in the manifest, showing everything is
        # not an option, so the cost stays under the user's control.
        enabled = list(state.surface_enabled_channels)
        mesh_streamer = _refs.get("mesh_streamer")

        if channel_id in enabled:
            enabled.remove(channel_id)
            state.surface_enabled_channels = enabled
            if mesh_mgr:
                mesh_mgr.disable_channel(channel_id)
        else:
            if not (mesh_mgr and mesh_mgr.is_available):
                print("[callbacks] No mesh manifest loaded — surfaces unavailable")
                return
            enabled.append(channel_id)
            state.surface_enabled_channels = enabled
            mesh_idx = _mesh_channel_index(mesh_mgr, channel_id)
            if mesh_idx is None:
                print(f"[callbacks] Channel {channel_id} has no surfaces in the manifest")
                return
            color_hex = "#FFFFFF"
            for ch in state.channels:
                if ch["id"] == channel_id:
                    color_hex = ch["color"]
                    break
            mesh_mgr.enable_channel(mesh_idx, _hex_to_rgb_tuple(color_hex))
            if mesh_streamer:
                mesh_streamer.refresh_now()

        if _refs["view"]:
            _refs["view"].update()

    def _mesh_channel_index(mesh_mgr, channel_id):
        """Map a UI channel id to the manifest's channel_idx.

        The manifest indexes by acquisition channel, which is not a dense
        0..N-1 counter, so the two only coincide by luck. Resolve by name.
        """
        name = next((ch["name"] for ch in state.channels if ch["id"] == channel_id), None)
        if name is not None:
            idx = mesh_mgr.channel_idx_for_name(name)
            if idx is not None:
                return idx
        # Fall back to treating the id as a manifest index if it names real tiles.
        return channel_id if mesh_mgr.get_tiles_for_channel(channel_id) else None

    def clear_analysis():
        """Clear only analysis data (not zarr/volume data)."""
        if _refs["analysis_loader"]:
            _refs["analysis_loader"].close()
            _refs["analysis_loader"] = None

        mgr = _refs.get("integrated_heatmap")
        if mgr is not None:
            mgr.set_active(False)

        heatmap_lod = _refs.get("heatmap_lod")
        if heatmap_lod:
            heatmap_lod.suspend(False)
            heatmap_lod.clear_analysis()

        vp = _refs.get("viewport_plots")
        if vp:
            vp.clear_analysis()

        state.analysis_loaded = False
        state.analysis_file_name = ""
        state.analysis_channels = []
        state.analysis_dilation_amounts = []
        state.analysis_dilation_labels = []
        state.analysis_hierarchy_levels = []
        state.analysis_volume_bounds = {}
        state.analysis_radius_max = 0.0
        state.analysis_detent_snap = 0.08
        state.bar_metric_label = ""
        state.heatmap_tile_count = 0
        print("[callbacks] Analysis cleared")

    def load_analysis_path():
        """Load analysis results from a server-side directory path
        (`state.analysis_dir`, containing colocalization.zarr + tally/)."""
        if state.analysis_loading:
            return
        results_dir = (state.analysis_dir or "").strip()
        if not results_dir:
            print("[callbacks] No analysis path given")
            return

        state.analysis_loading = True
        state.flush()
        try:
            from bioset.analysis import AnalysisLoader

            if _refs["analysis_loader"] is None:
                streamer = _refs.get("streamer")
                cell_sizes = getattr(getattr(streamer, "cfg", None),
                                     "analysis_cell_sizes", None)
                _refs["analysis_loader"] = AnalysisLoader(cell_sizes_vox=cell_sizes)

            loader = _refs["analysis_loader"]
            metadata = loader.load(results_dir)

            # The meshes now ship inside the results directory, so one path
            # drives both. cfg.mesh_dir stays an override for standalone use.
            mesh_mgr = _refs.get("mesh_manager")
            mesh_path = Path(results_dir) / "meshes"
            if mesh_mgr is not None and mesh_path.exists():
                mesh_mgr.set_mesh_dir(mesh_path)
                state.surface_enabled_channels = []
                ms = _refs.get("mesh_streamer")
                if ms:
                    ms.clear()
                print(f"[callbacks] Mesh manifest: {mesh_path} "
                      f"({len(mesh_mgr.manifest_channels)} channels)")

            z_depth = 1
            bounds = metadata.volume_bounds
            if bounds and "z" in bounds:
                z_depth = max(1, bounds["z"][1] - bounds["z"][0])

            heatmap_lod = _refs.get("heatmap_lod")
            if heatmap_lod:
                heatmap_lod.set_analysis(
                    loader=loader,
                    channel_order=list(metadata.channels),
                    z_depth=z_depth,
                )

            vp = _refs.get("viewport_plots")
            if vp:
                vp.set_analysis(
                    loader=loader,
                    channel_order=list(metadata.channels),
                    z_depth=z_depth,
                )

            state.analysis_file_name = Path(results_dir).name
            state.analysis_channels = metadata.channels
            state.analysis_dilation_amounts = metadata.dilation_amounts
            state.analysis_hierarchy_levels = [lvl["level"] for lvl in metadata.hierarchy_levels]
            state.analysis_volume_bounds = metadata.volume_bounds
            state.analysis_radius_max = metadata.radius_max_um
            state.analysis_detent_snap = metadata.detent_snap_um
            # Tick labels carry the effective radius (what the numbers describe),
            # rounded — the raw values run to 16 significant figures.
            state.analysis_dilation_labels = [
                round(r, 2) for r in (metadata.dilation_amounts_effective
                                      or metadata.dilation_amounts)
            ]

            if metadata.dilation_amounts:
                # Start at the smallest non-zero radius. The old "middle detent"
                # default lands at ~6.7 um on an 8-radius run, which is a large
                # dilation to open on.
                start = 1 if len(metadata.dilation_amounts) > 1 else 0
                state.current_dilation = metadata.dilation_amounts[start]
                state.radius_slider = state.current_dilation

            if metadata.hierarchy_levels:
                state.current_hierarchy_level = metadata.hierarchy_levels[-1]["level"]

            state.upset_selected_channels = [ch for ch in state.analysis_channels]
            state.bar_selected_channels = [ch for ch in state.analysis_channels]
            state.dilation_selected_channels = [ch for ch in state.analysis_channels]

            state.analysis_loaded = True
            state.right_drawer_open = True

            print(f"[callbacks] Analysis loaded: {len(metadata.channels)} channels, "
                  f"detents={metadata.dilation_amounts}, levels={state.analysis_hierarchy_levels}")

            update_heatmap()
            update_heatmap_combinations()
            update_upset_data()
            update_bar_data()
            update_dilation_data()

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

            # The first activated channel frames the canonical default view
            # (top-down, fit to the live volume bounds) inside activate_channel ->
            # frame_default_view; no dataset-specific camera distance here.
            # Surfaces are opt-in: activating a channel no longer pulls its
            # meshes in. If the user had already enabled them, keep the colour
            # in step and let the streamer fill the viewport.
            if mesh_mgr and mesh_mgr.is_available and channel_id in (
                    state.surface_enabled_channels or []):
                mesh_idx = _mesh_channel_index(mesh_mgr, channel_id)
                if mesh_idx is not None:
                    mesh_mgr.enable_channel(mesh_idx, _hex_to_rgb_tuple(color_hex))
                    ms = _refs.get("mesh_streamer")
                    if ms:
                        ms.refresh_now()

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

        # Channel membership changes the integrated heatmap's member-map set
        # (the streamer hook alone reinstalls with the previous members).
        if ((to_activate or to_deactivate)
                and state.heatmap_mode == "integrated"):
            update_heatmap()

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
                    limit=100000,
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

    def _camera_level_and_roi(streamer, heatmap_lod):
        """(hierarchy level, viewport ROI) implied by where the camera is NOW.

        The contour's iso-value is scoped to the viewport and its resolution
        to the level; the integrated mode's maps are built at the level. Both
        have to describe the live camera. Reads the same
        helpers the LOD worker does (`choose_heatmap_level` on the camera
        distance, `compute_visible_xy_roi_vox` for the rect) so the synchronous
        entry and the worker cannot disagree.
        """
        level = state.current_hierarchy_level
        roi_vox = heatmap_lod.viewport_roi if heatmap_lod else None
        if streamer is None:
            return level, roi_vox
        try:
            from bioset.streaming.lod import (
                camera_distance_to_focal, choose_heatmap_level,
                compute_visible_xy_roi_vox)
            camera = streamer.renderer.GetActiveCamera()
            if heatmap_lod is not None and heatmap_lod._auto_mode:
                level = choose_heatmap_level(
                    camera_distance_to_focal(camera),
                    heatmap_lod._distance_rules)
            sp = streamer._spacing_for_component(0)
            _, ydim, xdim = streamer._dims_for_component(0)
            roi = compute_visible_xy_roi_vox(
                streamer.renderer, bounds_world=streamer._volume_bounds_world(0),
                sx=sp.sx, sy=sp.sy, x_dim=xdim, y_dim=ydim, margin_vox=0,
            )
            if roi is not None:
                roi_vox = (roi.x0, roi.x1, roi.y0, roi.y1)
        except Exception as e:
            print(f"[callbacks] Camera view for contours unavailable: {e}")
        return level, roi_vox

    def _finish_heatmap_update(streamer):
        """Shared tail for the non-glyph modes: settle the volume and push."""
        if streamer is not None:
            try:
                streamer._render_still()
            except Exception:
                pass
        if _refs["view"]:
            _refs["view"].update()

    def _update_contour_heatmap(loader, heatmap, heatmap_lod, streamer):
        """Drive the Contour heatmap mode.

        Its own mode now, independent of the shader effects: the glyph squares
        are cleared and iso-contour geometry takes their place. Nothing here
        touches the volume rendering.
        """
        heatmap.clear()
        state.heatmap_tile_count = 0
        contours = _refs.get("contours")
        if contours is None:
            return
        sz = float(getattr(state, "physical_size_z", None) or 1.0)
        zb = (getattr(state, "analysis_volume_bounds", {}) or {}).get("z")
        if isinstance(zb, (list, tuple)) and len(zb) >= 2:
            contours.set_volume_z(float(zb[0]) * sz, float(zb[1]) * sz)
        contours.set_visible(True)

        combo = state.heatmap_combination or []
        if not state.heatmap_visible or not combo or streamer is None:
            print(f"[callbacks] Contour heatmap idle "
                  f"(visible={state.heatmap_visible}, combo={combo})")
            contours.clear()
            return
        try:
            # Draw NOW rather than waiting for the LOD worker, which only fires
            # on a level CHANGE — otherwise the mode comes up empty until the
            # user happens to zoom across a threshold. Level and viewport come
            # from the CAMERA: state.current_hierarchy_level lags it, and a
            # stale coarse level with a tight viewport leaves too few cells to
            # contour.
            # Level 0 ALWAYS. The contours must come from one field at every
            # zoom or separate boundaries merge when the LOD level switches —
            # different aggregations are different functions, and nesting says
            # nothing across that boundary. The camera still supplies the
            # viewport, which is what selects the iso and crops the geometry.
            _, roi = _camera_level_and_roi(streamer, heatmap_lod)
            field = loader.get_heatmap_field(
                channels=combo,
                dilation=state.current_dilation,
                hierarchy_level=0,
            )
            spacing = (
                getattr(state, "physical_size_x", None) or 1.0,
                getattr(state, "physical_size_y", None) or 1.0,
                getattr(state, "physical_size_z", None) or 1.0,
            )
            contours.update_field(field, spacing=spacing, roi_vox=roi)
            state.heatmap_tile_count = contours.line_count
            print(f"[callbacks] Contours from level 0: {contours.line_count} "
                  f"polylines at iso {contours.iso_value:.3f}")
        except Exception as e:
            print(f"[callbacks] Contour heatmap update failed: {e}")
            import traceback
            traceback.print_exc()

    def _update_integrated_heatmap(mgr, loader, heatmap, streamer,
                                   heatmap_lod=None):
        """Drive the shader-injected Integrated Heatmap mode.

        Gain and importance sampling only — the contours are their own mode
        now. The glyph heatmap is cleared because the effects modulate the
        volume rendering itself rather than adding geometry.
        """
        heatmap.clear()
        state.heatmap_tile_count = 0
        combo = state.heatmap_combination or []
        if not state.heatmap_visible or not combo or streamer is None:
            print("[callbacks] Integrated heatmap idle "
                  f"(visible={state.heatmap_visible}, combo={combo})")
            mgr.set_active(False)
            return
        try:
            bounds = streamer._volume_bounds_world(0)
            mgr.set_world_extent(bounds[1], bounds[3])
            name_to_id = {ch["name"]: ch["id"] for ch in (state.channels or [])}
            active_ids = list(state.active_channels or [])
            # Camera-derived level, so the maps enter at the resolution the
            # camera is already at instead of waiting for the next LOD land.
            level, _ = _camera_level_and_roi(streamer, heatmap_lod)
            inter, members = mgr.compute_maps(
                loader, combo, state.current_dilation, name_to_id, active_ids,
                hierarchy_level=level)
            mgr.set_effects(
                gain=bool(state.ihm_gain_enabled),
                sampling=bool(state.ihm_sampling_enabled),
            )
            mgr.set_maps(inter, members)
            mgr.set_active(True)
            shape = inter.shape if inter is not None else None
            print(f"[callbacks] Integrated heatmap: combo={combo}, "
                  f"members={list(members)}, radius={state.current_dilation}, "
                  f"level={level}, map={shape}")
        except Exception as e:
            print(f"[callbacks] Integrated heatmap update failed: {e}")
            import traceback
            traceback.print_exc()
            mgr.set_active(False)

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

        # ── Integrated (shader) mode: effects replace the glyph heatmap ──
        mgr = _refs.get("integrated_heatmap")
        contours = _refs.get("contours")
        mode = state.heatmap_mode

        if mode == "integrated" and mgr is not None:
            # Shader effects only; contours belong to their own mode.
            if contours is not None:
                contours.clear()
            if heatmap_lod is not None:
                heatmap_lod.set_mode("integrated")
            _update_integrated_heatmap(mgr, loader, heatmap, streamer, heatmap_lod)
            _finish_heatmap_update(streamer)
            return

        if mode == "contour":
            # Contour geometry only; the shader stays out of it.
            if mgr is not None:
                mgr.set_active(False)
            if heatmap_lod is not None:
                heatmap_lod.set_mode("contour")
            _update_contour_heatmap(loader, heatmap, heatmap_lod, streamer)
            _finish_heatmap_update(streamer)
            return

        # Grid: glyph squares own the heatmap, everything else off.
        if mgr is not None:
            mgr.set_active(False)
        if contours is not None:
            contours.clear()
        if heatmap_lod:
            heatmap_lod.set_mode("grid")
            heatmap_lod.suspend(False)

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
        print(f"[callbacks]   radius={state.current_dilation}, level={state.current_hierarchy_level}")

        field = loader.get_heatmap_field(
            channels=selected_channel_names,
            dilation=state.current_dilation,
            hierarchy_level=state.current_hierarchy_level,
        )

        # At fine levels, drop off-screen cells (same crop policy as the LOD
        # worker) — they are pure render cost while zoomed in.
        crop_levels = getattr(heatmap_lod, "CROP_LEVELS", (0, 1)) if heatmap_lod else (0, 1)
        margin = getattr(heatmap_lod, "CROP_MARGIN_FRAC", 1.0) if heatmap_lod else 1.0
        if field is not None and state.current_hierarchy_level in crop_levels:
            renderer = _refs.get("renderer")
            if streamer and renderer:
                try:
                    from bioset.streaming.lod import compute_visible_xy_roi_vox
                    from bioset.analysis import crop_field_to_roi
                    bounds = streamer._volume_bounds_world(0)
                    sp = streamer._spacing_for_component(0)
                    _, ydim, xdim = streamer._dims_for_component(0)
                    roi = compute_visible_xy_roi_vox(
                        renderer, bounds_world=bounds, sx=sp.sx, sy=sp.sy,
                        x_dim=xdim, y_dim=ydim, margin_vox=0, display_samples=5,
                    )
                    roi_vox = (roi.x0, roi.x1, roi.y0, roi.y1)
                    sub = crop_field_to_roi(field, roi_vox, margin)
                    if heatmap_lod:
                        heatmap_lod.note_applied_crop(roi_vox, sub is not field)
                    field = sub
                except Exception as e:
                    print(f"[callbacks] Heatmap crop skipped: {e}")

        if field is None or field.counts.size == 0:
            print(f"[callbacks] No cells found for this combination")
            heatmap.clear()
            state.heatmap_tile_count = 0
        else:
            spacing = (
                getattr(state, 'physical_size_x', None) or 1.0,
                getattr(state, 'physical_size_y', None) or 1.0,
                getattr(state, 'physical_size_z', None) or 1.0,
            )

            from bioset.scene.heatmap import hex_to_rgb
            color = hex_to_rgb(state.heatmap_color)
            # The two volume faces the grid brackets, from the analysis volume Z
            # bounds (voxels) converted to world units via physical_size_z.
            # These are the faces themselves — the old code offset the near one
            # 10 units toward the camera, which put it in front of the near
            # clipping plane (derived from the volume bounds and shared across
            # all three layers), so it vanished as soon as you zoomed in.
            bounds = getattr(state, "analysis_volume_bounds", {}) or {}
            z0z1 = bounds.get("z", None)
            if isinstance(z0z1, (list, tuple)) and len(z0z1) >= 2:
                sz = float(spacing[2]) if spacing and len(spacing) >= 3 else 1.0
                heatmap.config.volume_z_lo = float(z0z1[0]) * sz
                heatmap.config.volume_z_hi = float(z0z1[1]) * sz
            else:
                heatmap.config.volume_z_lo = 0.0
                heatmap.config.volume_z_hi = 0.0

            heatmap.update_field(field, spacing=spacing, color=color)
            state.heatmap_tile_count = heatmap.tile_count
            print(f"[callbacks] Heatmap: {heatmap.tile_count} cells "
                  f"({field.cell_size_vox}-voxel, level {field.level})")

        if _refs["view"]:
            _refs["view"].update()
    
    def print_dilation_curve():
        """Print dilation curves for all subcombinations of the selected channels."""
        loader = _refs.get("analysis_loader")
        if not loader or not loader.is_loaded:
            return

        channels = state.heatmap_combination or []
        if not channels:
            return

        level = state.current_hierarchy_level
        curves = loader.get_subcombination_dilation_curves(channels, level)
        if not curves:
            print(f"[dilation-curve] No data for {channels}")
            return

        print(f"[dilation-curve] Subcombination curves for {' | '.join(channels)}  (hierarchy_level={level})")
        for combo_key, curve in curves.items():
            is_single = "|" not in combo_key
            print(f"\n  --- {combo_key} ---")
            if is_single:
                print(f"  {'Dilation':>10}  {'Voxels':>12}  {'Density':>10}")
                print(f"  {'-'*10}  {'-'*12}  {'-'*10}")
                for pt in curve:
                    print(f"  {pt['dilation']:>10.1f}  {pt['count']:>12}  {pt.get('density', 0):>10.6f}")
            else:
                print(f"  {'Dilation':>10}  {'Intersection':>12}  {'IoU':>10}  {'Overlap':>10}")
                print(f"  {'-'*10}  {'-'*12}  {'-'*10}  {'-'*10}")
                for pt in curve:
                    print(f"  {pt['dilation']:>10.1f}  {pt['count']:>12}  {pt['iou']:>10.6f}  {pt.get('overlap_coeff', 0):>10.6f}")

    def _upset_selection(loader):
        """Channels ticked in the UpSet dialog, restricted to ones that exist.

        Returns None when everything is selected (no restriction worth pushing
        into the query) and [] when nothing is — which must render an empty
        plot, not the unfiltered one. The old post-filter treated an empty
        selection as "no filter", so Deselect All showed everything.
        """
        sel = list(getattr(state, "upset_selected_channels", None) or [])
        known = set(loader.metadata.channels if loader.metadata else [])
        sel = [c for c in sel if c in known]
        if not sel:
            return []
        if len(sel) >= len(known):
            return None
        return sel

    def _combo_size(loader) -> int:
        """Requested combination size: an exact degree, or ALL_DEGREES for every size.

        Clamps a stale or bookmarked value above what the tables cover. Note
        the sentinel is 0, so this must not coerce falsy values to a default.
        """
        from bioset.analysis import ALL_DEGREES
        raw = getattr(state, "upset_min_channels", 2)
        try:
            want = int(raw)
        except (TypeError, ValueError):
            want = ALL_DEGREES
        if want == ALL_DEGREES:
            return ALL_DEGREES
        cap = int(getattr(loader, "max_combo_degree", 0) or 0)
        if want < 2:
            # Size 1 is meaningless: a set's IoU against itself is always 1.0,
            # so it drew a row of identical full-height bars.
            return ALL_DEGREES
        if cap and want > cap:
            print(f"[callbacks] combination size {want} exceeds the ranked "
                  f"tables (max {cap}); using {cap}")
            state.upset_min_channels = cap
            return cap
        return want

    def _set_upset_label(combinations):
        """Say which unit the UpSet counts are in, and which radius they describe.

        Ranked counts come from the combination tables in raw voxels; the
        viewport scope computes bins from the fields. The bar heights are
        ratios and so comparable either way, but the counts are not.
        """
        if not combinations:
            state.upset_metric_label = ""
            return
        first = combinations[0]
        unit = getattr(first, "count_unit", "bins")
        eff = getattr(first, "radius_um_effective", 0.0)
        state.upset_metric_label = (
            f"counts in {unit}" + (f" @ {eff:.2f} µm" if eff else "")
        )

    def update_upset_data():
        """Global-scope UpSet rows for the current size, selection and metric."""
        loader = _refs.get("analysis_loader")
        if not loader or not loader.is_loaded:
            state.upset_data = []
            return

        size = _combo_size(loader)
        selection = _upset_selection(loader)
        if selection == []:
            # Nothing ticked -> nothing to show. Previously an empty selection
            # meant "no filter", so Deselect All displayed the whole plot.
            # Clear the local array here too: the cascade that normally
            # refreshes it only fires when `upset_data` actually changes, and
            # it may already be empty.
            state.upset_data = []
            state.upset_data_local = []
            state.upset_metric_label = ""
            print("[callbacks] UpSet data cleared (no channels selected)")
            return

        print(f"[callbacks] Updating UpSet data: dilation={state.current_dilation}, "
              f"size={size or 'all'}, metric={state.upset_metric}")

        # Size, selection and metric all go INTO the query: filtering a
        # truncated top-N afterwards drops combinations that rank below the
        # cutoff globally, which for a few low-abundance channels is all of them.
        combinations = loader.get_top_combinations(
            dilation=state.current_dilation,
            hierarchy_level=state.current_hierarchy_level,
            limit=UPSET_ROW_CAP,
            min_channels=size,
            channel_filter=selection,
            metric=state.upset_metric,
        )

        state.upset_data = [
            {"channels": c.channels, "iou": c.iou, "overlap_coeff": c.overlap_coeff}
            for c in combinations
        ]
        _set_upset_label(combinations)
        print(f"[callbacks] UpSet data updated: {len(combinations)} rows")

    def update_upset_data_local():
        """UpSet rows restricted to the channels active in the 3D view."""
        loader = _refs.get("analysis_loader")
        if not loader or not loader.is_loaded:
            state.upset_data_local = []
            return

        active_ids = state.active_channels or []
        active_names = [ch["name"] for ch in (state.channels or [])
                        if ch["id"] in active_ids]
        if not active_names:
            state.upset_data_local = []
            print("[callbacks] UpSet local data cleared (no active channels)")
            return

        selection = _upset_selection(loader)
        if selection == []:
            state.upset_data_local = []
            print("[callbacks] UpSet local data cleared (no channels selected)")
            return
        # Two different filters, deliberately:
        #   channel_filter -> exclusion. A combination naming a channel the user
        #                     unticked is never shown, whatever else it contains.
        #   require_any    -> inclusion (OR). Show combinations involving AT
        #                     LEAST ONE active channel, not only those made
        #                     entirely of them.
        # Requiring every member to be active (the old behaviour) hid pairings
        # between something you are looking at and something you are not.
        scope = active_names if selection is None else             [n for n in active_names if n in set(selection)]
        if not scope:
            state.upset_data_local = []
            print("[callbacks] UpSet local data cleared (no active channel is selected)")
            return

        try:
            combinations = loader.get_top_combinations(
                dilation=state.current_dilation,
                hierarchy_level=state.current_hierarchy_level,
                limit=UPSET_ROW_CAP,
                min_channels=_combo_size(loader),   # was ignored entirely
                channel_filter=selection,
                require_any=scope,
                metric=state.upset_metric,
            )
            state.upset_data_local = [
                {"channels": c.channels, "iou": c.iou, "overlap_coeff": c.overlap_coeff}
                for c in combinations
            ]
            print(f"[callbacks] UpSet local data updated: {len(combinations)} rows")
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
        
        all_coverage = loader.get_channel_coverage(
            dilation=state.current_dilation,
            hierarchy_level=state.current_hierarchy_level,
        )
        # One unit at every radius. The series used to switch to voxel-exact
        # percentages on a tallied radius, so the axis silently changed meaning
        # mid-drag between two numbers that differ by up to 256x.
        state.bar_metric_label = "% of analysis bins (1.12 µm)"

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

    def update_dilation_data():
        """Update dilation curve data for the line plot."""
        loader = _refs.get("analysis_loader")
        if not loader or not loader.is_loaded:
            print("[callbacks] Cannot update dilation data - loader not ready")
            state.dilation_data = {}
            state.dilation_filter_options = []
            return

        print(
            f"[callbacks] Updating dilation data: mode={state.dilation_view_mode}, level={state.current_hierarchy_level}")

        active_ids = state.active_channels or []
        if not active_ids:
            state.dilation_data = {}
            state.dilation_filter_options = []
            print("[callbacks] Dilation data cleared (no channels selected)")
            return

        channels_list = state.channels or []
        id_to_name = {ch["id"]: ch["name"] for ch in channels_list}
        selected = [id_to_name[ch_id] for ch_id in active_ids if ch_id in id_to_name]

        if not selected:
            state.dilation_data = {}
            state.dilation_filter_options = []
            return

        view_mode = getattr(state, "dilation_view_mode", "single")
        level = getattr(state, "current_hierarchy_level", 0)

        dilation_curves = loader.get_subcombination_dilation_curves(selected, hierarchy_level=level)

        # Collect all available keys for this mode
        if view_mode == "single":
            all_keys = sorted(k for k in dilation_curves if "|" not in k)
        else:
            all_keys = sorted(k for k in dilation_curves if "|" in k)

        prev_options = set(getattr(state, "dilation_filter_options", []) or [])
        state.dilation_filter_options = all_keys

        # Auto-select all when the available options changed (new channels activated, mode switched)
        if set(all_keys) != prev_options:
            state.dilation_selected_channels = list(all_keys)
            dilation_selected = set(all_keys)
        else:
            dilation_selected = set(getattr(state, "dilation_selected_channels", []) or [])

        result = {k: dilation_curves[k] for k in all_keys if k in dilation_selected}
        state.dilation_data = result

        print(f"[callbacks] Dilation data updated: {len(result)}/{len(all_keys)} curves shown")
    
    def _compute_current_tile_ranges():
        """Compute tile grid ranges for the current viewport. Returns ((gx0, gx1), (gy0, gy1)) or None."""
        streamer = _refs.get("streamer")
        renderer = _refs.get("renderer")
        vp = _refs.get("viewport_plots")
        if not streamer or not renderer or not vp:
            return None
        try:
            from bioset.streaming.lod import compute_visible_xy_roi_vox
            bounds = streamer._volume_bounds_world(0)
            sp = streamer._spacing_for_component(0)
            _, ydim, xdim = streamer._dims_for_component(0)
            roi = compute_visible_xy_roi_vox(
                renderer, bounds_world=bounds, sx=sp.sx, sy=sp.sy,
                x_dim=xdim, y_dim=ydim, margin_vox=0,
            )
            # Viewport ranges are in tally-block units (128 voxels)
            from bioset.analysis.constants import BLOCK_VOX
            gx0 = roi.x0 // BLOCK_VOX
            gx1 = (roi.x1 + BLOCK_VOX - 1) // BLOCK_VOX
            gy0 = roi.y0 // BLOCK_VOX
            gy1 = (roi.y1 + BLOCK_VOX - 1) // BLOCK_VOX
            return (gx0, gx1), (gy0, gy1)
        except Exception as e:
            print(f"[viewport_plots] ROI computation error: {e}")
            return None

    def sync_viewport_plots_enabled():
        """Enable/disable viewport plot computation based on scope modes and drawer visibility."""
        vp = _refs.get("viewport_plots")
        if not vp:
            return

        drawer_open = getattr(state, "right_drawer_open", False)
        need_bar = drawer_open and getattr(state, "bar_scope_mode", "global") == "local"
        need_upset = drawer_open and getattr(state, "upset_scope_mode", "global") == "local"
        need_dilation = drawer_open and getattr(state, "dilation_scope_mode", "global") == "local"

        any_needed = need_bar or need_upset or need_dilation
        vp.set_enabled(any_needed)
        vp.update_needed_plots(need_bar, need_upset, need_dilation)

        if any_needed:
            # Sync active channel names for filtering viewport results
            active_ids = state.active_channels or []
            channels_list = state.channels or []
            id_to_name = {ch["id"]: ch["name"] for ch in channels_list}
            active_names = [id_to_name[ch_id] for ch_id in active_ids if ch_id in id_to_name]
            vp.update_active_channels(active_names)
            vp.update_dilation(getattr(state, "current_dilation", 0.0))
            loader = _refs.get("analysis_loader")
            if loader and loader.is_loaded:
                vp.update_min_channels(_combo_size(loader))
                vp.update_selected_channels(_upset_selection(loader))

            # Trigger immediate computation with current viewport
            ranges = _compute_current_tile_ranges()
            if ranges:
                vp.on_camera_moved(ranges[0], ranges[1])

    def reset_camera():
        """Reset camera to initial position (from when data was first loaded). Use after opening a Bookmark to return to default view."""
        streamer = _refs.get("streamer")
        if streamer and getattr(streamer, "renderer", None):
            if hasattr(streamer, "reset_camera_to_initial"):
                streamer.reset_camera_to_initial()
            else:
                streamer.renderer.ResetCamera()
                streamer.renderer.ResetCameraClippingRange()
        update_main_scale_bar()
        view = _refs.get("view")
        if view:
            # Force the client to re-measure and resync the server render-window
            # size/aspect. On a remote client the offscreen window can be left at
            # a stale aspect, which renders the volume stretched until the user
            # resizes the browser; resize() does that resync programmatically.
            try:
                view.resize()
            except Exception:
                pass
            view.update()

    def update_background_color(color_hex):
        """Update renderer background color.

        The main window is layered (heatmap fill = layer 0, volume = layer 1,
        heatmap outline = layer 2). Only the layer-0 renderer clears the window's
        color buffer, so the visible background comes from IT, not the volume
        renderer — setting the volume renderer's background alone has no effect.
        Set every renderer, and make the bottom (lowest-layer) one opaque so the
        chosen color actually shows."""
        print(f"[callbacks] Updating background color to {color_hex}")
        streamer = _refs.get("streamer")
        if not streamer:
            return
        color_hex = color_hex.lstrip('#')
        if len(color_hex) < 6:
            return
        r = int(color_hex[0:2], 16) / 255.0
        g = int(color_hex[2:4], 16) / 255.0
        b = int(color_hex[4:6], 16) / 255.0

        rw = getattr(streamer, 'render_window', None)
        renderers = []
        if rw is not None:
            coll = rw.GetRenderers()
            coll.InitTraversal()
            ren = coll.GetNextItem()
            while ren is not None:
                renderers.append(ren)
                ren = coll.GetNextItem()
        if not renderers and hasattr(streamer, 'renderer'):
            renderers = [streamer.renderer]

        for ren in renderers:
            ren.SetBackground(r, g, b)
        if renderers:
            bottom = min(renderers, key=lambda re: re.GetLayer())
            bottom.SetBackgroundAlpha(1.0)  # the layer that clears the window
        if rw is not None:
            rw.Render()
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

        # Free-form chat works without a selected tile; include stats only when available.
        channel_stats = _refs.get("last_tile_channel_stats")

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

        # Pick the data source matching current scope + channel toggles
        if state.upset_scope_mode == "local":
            source_data = list(state.upset_data_viewport_selected if state.upset_channel_mode == "selected" else state.upset_data_viewport)
        else:
            source_data = list(state.upset_data_local if state.upset_channel_mode == "selected" else state.upset_data)
        offset = state.upset_offset
        limit = state.upset_limit
        visible_data = source_data[offset:offset + limit]

        active_channel_names = [
            ch["name"] for ch in (state.channels or [])
            if ch["id"] in (state.active_channels or [])
        ]

        plot_payload = {
            "type": "upset",
            "scope_mode": state.upset_scope_mode,
            "channel_mode": state.upset_channel_mode,
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

        # Pick the data source matching current scope + channel toggles
        if state.bar_scope_mode == "local":
            raw_data = list(state.bar_data_viewport_selected if state.bar_channel_mode == "selected" else state.bar_data_viewport)
        else:
            raw_data = list(state.bar_data_local if state.bar_channel_mode == "selected" else state.bar_data)
        offset = state.bar_offset
        limit = state.bar_limit
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
            "scope_mode": state.bar_scope_mode,
            "channel_mode": state.bar_channel_mode,
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

    def chatbot_suggest_bookmark():
        """Suggest bookmark title/category/description and prefill the bookmark form."""
        if not state.chatbot_authenticated:
            print("[callbacks] Cannot suggest bookmark - Biomni not initialised")
            return

        markers = _build_markers()
        if not markers:
            msg = "Please select channels first."
            state.chatbot_messages = list(state.chatbot_messages) + [
                {"role": "error", "content": msg}
            ]
            print(f"[callbacks] {msg}")
            return

        state.chatbot_panel_open = True
        state.chatbot_messages = list(state.chatbot_messages) + [
            {"role": "user", "content": "Suggest bookmark text"}
        ]
        state.chatbot_loading = True

        try:
            client = _get_biomni_client()
            screenshot_base64 = capture_screenshot()
            result = client.suggest_bookmark(
                markers=markers,
                mode=state.biomni_mode,
                image=screenshot_base64,
            )

            suggested_title = (result.get("title") or "").strip()
            suggested_category = (result.get("category") or "").strip() or "Uncategorized"
            suggested_description = (result.get("description") or "").strip()

            if hasattr(ctrl, "bookmark_open_new_form"):
                ctrl.bookmark_open_new_form()
            else:
                state.bookmark_form_dialog = True

            if suggested_title:
                state.bookmark_form_name = suggested_title
            state.bookmark_form_category = suggested_category
            state.bookmark_form_description = suggested_description
            state.bookmark_open = True

            state.chatbot_messages = list(state.chatbot_messages) + [
                {
                    "role": "assistant",
                    "format": "bookmark",
                    "title": suggested_title or "(untitled)",
                    "category": suggested_category,
                    "description": suggested_description or "",
                }
            ]
            print("[callbacks] Biomni bookmark suggestion applied to bookmark form")
        except Exception as e:
            error_msg = f"Error: {e}"
            print(f"[callbacks] Biomni bookmark suggestion error: {error_msg}")
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
        """Refresh labels when the camera actually moves.

        `label_manager.update()` clears every label actor and re-runs
        hierarchical placement from scratch, which is the most expensive thing
        on the main thread per interaction. It used to run on every
        EndInteractionEvent — including the ones the streamer synthesises and
        the ones that end a drag which barely moved the camera. Gate it on a
        real change in camera pose so a nudge, a zoom that settles back, or a
        synthetic event costs nothing.
        """
        _last_pose = [None]
        # Relative move that counts as "the view changed": 0.5% of the camera's
        # distance to its focal point, so the threshold scales with zoom.
        REL_EPS = 0.005

        def _pose(cam):
            p, f, u = cam.GetPosition(), cam.GetFocalPoint(), cam.GetViewUp()
            return (p, f, u, cam.GetViewAngle(), cam.GetParallelScale())

        def _moved(a, b):
            if a is None:
                return True
            (pa, fa, ua, va, sa), (pb, fb, ub, vb, sb) = a, b
            scale = max(1e-9, sum((pb[i] - fb[i]) ** 2 for i in range(3)) ** 0.5)
            tol = scale * REL_EPS
            for x, y in ((pa, pb), (fa, fb)):
                if any(abs(x[i] - y[i]) > tol for i in range(3)):
                    return True
            if any(abs(ua[i] - ub[i]) > 1e-4 for i in range(3)):
                return True
            return abs(va - vb) > 1e-4 or abs(sa - sb) > tol

        def _on_end_interaction(obj, event):
            if state.anchor_labels or not state.show_labels:
                return  # pinned or hidden — nothing to place
            label_mgr = _refs.get("label_manager")
            if label_mgr is None:
                return
            try:
                pose = _pose(obj.GetRenderWindow().GetRenderers()
                             .GetFirstRenderer().GetActiveCamera())
            except Exception:
                pose = None
            if pose is not None:
                if not _moved(_last_pose[0], pose):
                    return
                _last_pose[0] = pose
            refresh_labels()

        interactor.AddObserver("EndInteractionEvent", _on_end_interaction)
        print("[callbacks] Label EndInteractionEvent observer registered (pose-gated)")

    def capture_screenshot():
        """Capture current VTK view as base64-encoded JPEG, capped under 5 MB."""
        import base64
        from io import BytesIO
        from PIL import Image

        png_bytes = capture_screenshot_png_bytes(_refs.get("streamer"))
        if not png_bytes:
            return None

        img = Image.open(BytesIO(png_bytes)).convert("RGB")

        # Down-scale if either dimension exceeds 1920
        max_dim = 1920
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)

        # Encode as JPEG, lowering quality until under 5 MB (base64 limit)
        max_b64_bytes = 5 * 1024 * 1024  # 5 MB
        for quality in (85, 70, 50):
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            raw = buf.getvalue()
            if len(raw) * 4 // 3 <= max_b64_bytes:
                return base64.b64encode(raw).decode("utf-8")

        # Last resort: already smallest quality
        return base64.b64encode(raw).decode("utf-8")

    def _print_tile_channel_stats(tile, dilation):
        """Print per-channel stats for the tally block containing a picked
        heatmap cell, and cache the channel_stats dict for Biomni."""
        loader = _refs.get("analysis_loader")
        heatmap = _refs.get("heatmap")
        if not loader or not loader.is_loaded or not heatmap:
            return

        from bioset.analysis.constants import BLOCK_VOX

        cs = heatmap.current_cell_size_vox or 1
        # Cell center in voxel coordinates → containing 128-voxel tally block
        vox_x = (tile.x0 + tile.x1) / 2.0 * cs
        vox_y = (tile.y0 + tile.y1) / 2.0 * cs
        block_x = int(vox_x // BLOCK_VOX)
        block_y = int(vox_y // BLOCK_VOX)

        stats = loader.get_block_channel_stats(block_y, block_x, dilation)
        print(f"[picker] Channel stats — cell ({tile.x0},{tile.y0}) "
              f"block ({block_y},{block_x}) radius={dilation}:")
        if stats:
            print(f"  {'Channel':<20} {'Voxels':>10} {'MeanInt':>10} {'SumInt':>14}")
            print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*14}")
            for row in stats:
                print(f"  {row['channel']:<20} {row['voxel_count']:>10} "
                      f"{row['mean_intensity']:>10.3f} {row['sum_intensity']:>14.1f}")
        else:
            print("  (no data for this block / radius)")

        bounds = loader.metadata.volume_bounds if loader.metadata else {}
        z_depth = max(1, bounds["z"][1] - bounds["z"][0]) if bounds and "z" in bounds else 1
        total_voxels = BLOCK_VOX * BLOCK_VOX * z_depth

        # Intensity scale comes from the image metadata when available.
        dtype_max = getattr(state, "dtype_max", None) or (
            loader.metadata.dtype_max if loader.metadata else 65535)

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
        """No-op: heatmap tile picking was removed.

        Right-click drill-down existed to choose which mesh tile to show. Now
        that surfaces are opt-in per channel and stream with the viewport, it
        drove nothing — while the picker plus the hover highlight it shared code
        with cost a display-ray pick and a full render/encode/push per event.
        Kept as a no-op so the app wiring and any bookmark replay stay valid.
        """
        return

    def generate_pdf_report(report_data=None):
        """
        API endpoint to generate and download a PDF report.
        report_data: dict with 'title', 'params', 'channels', etc.
        """
        report_data = []

        if state.export_general:
            # Report the URL only when the metadata actually came from it.
            metadata_src = (
                "Embedded in zarr store"
                if getattr(state, "metadata_source", "") == "embedded"
                else state.metadata_url
            )
            general_content = GeneralContent(state.zarr_url, metadata_src, datetime.datetime.now())
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
    ctrl.set_integrated_heatmap = set_integrated_heatmap
    ctrl.load_data = load_data
    ctrl.clear_data = clear_data
    ctrl.load_analysis_path = load_analysis_path
    ctrl.update_heatmap = update_heatmap
    ctrl.update_heatmap_combinations = update_heatmap_combinations
    ctrl.print_dilation_curve = print_dilation_curve
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
    ctrl.update_dilation_data = update_dilation_data
    ctrl.chatbot_login = chatbot_login
    ctrl.biomni_add_data = biomni_add_data
    ctrl.biomni_upload_file = biomni_upload_file
    ctrl.chatbot_send_message = chatbot_send_message
    ctrl.chatbot_label = chatbot_label
    ctrl.chatbot_suggest_bookmark = chatbot_suggest_bookmark
    ctrl.chatbot_suggest = chatbot_suggest
    ctrl.chatbot_explain_upset = chatbot_explain_upset
    ctrl.chatbot_explain_bar = chatbot_explain_bar
    ctrl.chatbot_clear = chatbot_clear
    ctrl.toggle_labels = toggle_labels
    ctrl.deselect_tile = deselect_tile
    ctrl.set_mesh_manager = set_mesh_manager
    ctrl.set_mesh_streamer = set_mesh_streamer
    ctrl.set_contours = set_contours
    ctrl.setup_right_click_picker = setup_right_click_picker
    ctrl.set_heatmap_lod = set_heatmap_lod
    ctrl.set_heatmap_lod_auto_mode = set_heatmap_lod_auto_mode
    ctrl.set_viewport_plots = set_viewport_plots
    ctrl.sync_viewport_plots_enabled = sync_viewport_plots_enabled
    ctrl.trigger("clear_analysis")(clear_analysis)
    ctrl.generate_pdf_report = generate_pdf_report
    ctrl.set_renderer = set_renderer
    ctrl.refresh_labels = refresh_labels
    ctrl.check_label_setup = check_label_setup
    ctrl.setup_label_interaction_observer = setup_label_interaction_observer

