# settings.py
"""Settings section component."""

from __future__ import annotations

from trame.widgets import html, vuetify


def settings_section(state, ctrl):
    """Create the settings section with theme, background color, and camera reset."""
    
    def toggle_settings():
        state.settings_open = not state.settings_open
    
    def open_bg_picker():
        state.bg_color_dialog = True

    def close_bg_picker():
        state.bg_color_dialog = False
    
    with vuetify.VList(dense=True, nav=True):
        with vuetify.VListItem(
            class_=("settings_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
            link=True,
            ripple=True,
            click=toggle_settings,
        ):
            with vuetify.VListItemIcon():
                with vuetify.VTooltip(right=True):
                    with html.Template(v_slot_activator="{ on, attrs }"):
                        vuetify.VIcon("mdi-cog-outline", style="font-size: 40px;", v_bind="attrs", v_on="on")
                    html.Span("Settings")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                vuetify.VListItemTitle("Settings", classes="text-overline")

        with vuetify.VExpandTransition():
            with html.Div(v_show=("settings_open", False)):
                # Toggle Theme
                with vuetify.VListItem(class_="nav-item nav-item--nested", link=True, ripple=True):
                    with vuetify.VListItemIcon():
                        with vuetify.VTooltip(right=True):
                            with html.Template(v_slot_activator="{ on, attrs }"):
                                vuetify.VIcon("mdi-lightbulb-outline", style="font-size: 25px;", v_bind="attrs", v_on="on")
                            html.Span("Light on/off")
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        html.Span("Toggle Theme")
                
                # Background Color
                with vuetify.VListItem(
                    class_="nav-item nav-item--nested",
                    link=True,
                    ripple=True,
                    click=open_bg_picker,
                ):
                    with vuetify.VListItemIcon():
                        with vuetify.VTooltip(right=True):
                            with html.Template(v_slot_activator="{ on, attrs }"):
                                vuetify.VIcon(
                                    "mdi-format-color-fill",
                                    style=("`font-size: 25px; background-color: ${bg_color};`",),
                                    v_bind="attrs",
                                    v_on="on",
                                )
                            html.Span("Change background color")
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        html.Span("Background Color")
                
                # Background Color Dialog
                with vuetify.VDialog(v_model=("bg_color_dialog", False), max_width=320):
                    with vuetify.VCard():
                        with vuetify.VCardTitle():
                            html.Span("Background color")
                        with vuetify.VCardText():
                            vuetify.VColorPicker(
                                v_model=("bg_color", "#121212"),
                                hide_inputs=True,
                                hide_mode_switch=True,
                                mode="hexa",
                            )
                        with vuetify.VCardActions():
                            vuetify.VSpacer()
                            with vuetify.VBtn(text=True, click=close_bg_picker):
                                html.Span("Close")
                
                # Reset Camera
                with vuetify.VListItem(
                    class_="nav-item nav-item--nested",
                    link=True,
                    ripple=True,
                    click=ctrl.reset_camera,
                ):
                    with vuetify.VListItemIcon():
                        with vuetify.VTooltip(right=True):
                            with html.Template(v_slot_activator="{ on, attrs }"):
                                vuetify.VIcon("mdi-crop-free", style="font-size: 25px;", v_bind="attrs", v_on="on")
                            html.Span("Reset camera position")
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        html.Span("Reset Camera")

                # NOV: before press only "NOV"; after press show "NOV  < n/8 >" (one chevron each side)
                with vuetify.VListItem(class_="nav-item nav-item--nested", style="overflow: visible;"):
                    with vuetify.VListItemIcon():
                        with vuetify.VTooltip(right=True):
                            with html.Template(v_slot_activator="{ on, attrs }"):
                                vuetify.VIcon("mdi-camera-enhance", style="font-size: 25px;", v_bind="attrs", v_on="on")
                            html.Span("Next Best View")
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        with html.Div(style="display: flex; align-items: center; gap: 2px; flex-wrap: nowrap; min-width: 0;"):
                            html.Span("NOV", style="cursor: pointer; flex-shrink: 0;", click=ctrl.nov_toggle)
                            with html.Div(
                                v_show=("nov_panel_visible", False),
                                style="display: inline-flex; align-items: center; gap: 1px; flex-shrink: 0;",
                            ):
                                with vuetify.VBtn(icon=True, x_small=True, dense=True, small=True, click=ctrl.nov_prev):
                                    vuetify.VIcon("mdi-chevron-left", small=True)
                                html.Span("{{ nov_view_index_display || '0/10' }}", style="min-width: 3ch; font-size: 0.7em;")
                                with vuetify.VBtn(icon=True, x_small=True, dense=True, small=True, click=ctrl.nov_next):
                                    vuetify.VIcon("mdi-chevron-right", small=True)
                            with html.Div(
                                v_show=("nov_panel_visible && nov_sphere_svg", False),
                                style="margin-left: 3px; flex-shrink: 0; display: inline-flex; align-items: center; gap: 1px;",
                            ):
                                html.Span("F", v_show=("nov_is_front", True), style="font-size: 0.65em; opacity: 0.8;")
                                html.Span("B", v_show=("!nov_is_front", False), style="font-size: 0.65em; opacity: 0.8;")
                                html.Div(v_html=("nov_sphere_svg", ""), style="flex-shrink: 0;")
                # Lineage
                with vuetify.VListItem(
                    class_=("lineage_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
                    link=True,
                    ripple=True,
                    click="lineage_open = !lineage_open",
                ):
                    with vuetify.VListItemIcon():
                        with vuetify.VTooltip(right=True):
                            with html.Template(v_slot_activator="{ on, attrs }"):
                                vuetify.VIcon("mdi-book-open-variant", style="font-size: 25px;", v_bind="attrs", v_on="on")
                            html.Span("Lineage")
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        html.Span("Lineage")
                with vuetify.VExpandTransition():
                    with html.Div(v_show=("lineage_open", False)):
                        with vuetify.VListItem(class_="nav-item nav-item--nested"):
                            with vuetify.VListItemContent(v_if="!drawer_mini", style="display: flex; align-items: center; gap: 6px; flex-wrap: nowrap;"):
                                vuetify.VAutocomplete(
                                    v_model=("lineage_selected_name", "Name"),
                                    items=("lineage_snapshot_names", []),
                                    dense=True,
                                    hide_details=True,
                                    placeholder="Select...",
                                    style="flex: 0.4 1 0; min-width: 0;",
                                )
                                with vuetify.VBtn(
                                    x_small=True,
                                    color="primary",
                                    disabled=("!lineage_selected_name || !String(lineage_selected_name).trim()",),
                                    click=ctrl.lineage_open_snapshot,
                                    style="flex: 0.3 1 0; min-width: 0;",
                                ):
                                    html.Span("Open")
                                with vuetify.VBtn(
                                    x_small=True,
                                    color="secondary",
                                    click=ctrl.lineage_open_new_form,
                                    style="flex: 0.3 1 0; min-width: 0;",
                                ):
                                    html.Span("New")