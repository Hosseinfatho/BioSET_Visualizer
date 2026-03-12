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
                
                # Camera / View Settings Header
                with vuetify.VListItem(v_if="!drawer_mini", classes="mt-2 text-left pl-4"):
                    with vuetify.VListItemContent(classes="py-0"):
                        html.Span("Camera / View", classes="text-caption grey--text font-weight-bold letter-spacing-1")
                
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

                # Biomni Settings Header
                with vuetify.VListItem(v_if="!drawer_mini", classes="mt-4 text-left pl-4"):
                    with vuetify.VListItemContent(classes="py-0"):
                        html.Span("Biomni / LLM", classes="text-caption grey--text font-weight-bold letter-spacing-1")

                # Model selection
                with vuetify.VListItem(class_="nav-item nav-item--nested"):
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        vuetify.VSelect(
                            v_model=("biomni_model",),
                            items=("biomni_available_models",),
                            label="Model",
                            dense=True,
                            outlined=True,
                            hide_details=True,
                        )

                # Mode selection
                with vuetify.VListItem(class_="nav-item nav-item--nested"):
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        vuetify.VSelect(
                            v_model=("biomni_mode",),
                            items=(["full", "db", "minimal"],),
                            label="Mode",
                            dense=True,
                            outlined=True,
                            hide_details=True,
                        )

                # Port mapping
                with vuetify.VListItem(class_="nav-item nav-item--nested"):
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        vuetify.VTextField(
                            v_model=("biomni_port",),
                            label="Port",
                            type="number",
                            dense=True,
                            outlined=True,
                            hide_details=True,
                        )
                
                # Context Data Upload
                with vuetify.VListItem(class_="nav-item nav-item--nested"):
                    with vuetify.VListItemContent(v_if="!drawer_mini", classes="pb-3"):
                        vuetify.VFileInput(
                            label="Add context data...",
                            dense=True,
                            outlined=True,
                            hide_details=True,
                            prepend_icon="mdi-paperclip",
                            __events=["change"],
                            change=(ctrl.biomni_add_data, "[$event]"),
                        )