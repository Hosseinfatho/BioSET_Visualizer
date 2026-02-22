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
                        vuetify.VListItemTitle("Lineage", classes="text-overline")
                with vuetify.VExpandTransition():
                    with html.Div(v_show=("lineage_open", False)):
                        with vuetify.VListItem(class_="nav-item nav-item--nested", style="flex-wrap: wrap;"):
                            with vuetify.VListItemContent(v_if="!drawer_mini", style="width: 100%;"):
                                html.Span("Name", classes="text-caption d-block mb-1")
                                vuetify.VAutocomplete(
                                    v_model=("lineage_selected_name", "Name"),
                                    items=("lineage_snapshot_names", []),
                                    dense=True,
                                    hide_details=True,
                                    placeholder="Name",
                                    style="max-width: 100%;",
                                )
                        with vuetify.VListItem(class_="nav-item nav-item--nested", style="flex-wrap: wrap;"):
                            with vuetify.VListItemContent(v_if="!drawer_mini", style="display: flex; gap: 4px; flex-wrap: wrap;"):
                                with vuetify.VBtn(
                                    small=True,
                                    color="primary",
                                    disabled=("!lineage_selected_name || !String(lineage_selected_name).trim()",),
                                    click=ctrl.lineage_open_snapshot,
                                ):
                                    html.Span("Open")
                                with vuetify.VBtn(
                                    small=True,
                                    color="secondary",
                                    click=ctrl.lineage_open_new_form,
                                ):
                                    html.Span("New")