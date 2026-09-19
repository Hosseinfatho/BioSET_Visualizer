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
                classes="mb-0",
            link=True,
            ripple=True,
            click=toggle_settings,
        ):
            with vuetify.VListItemIcon():
                with vuetify.VTooltip(right=True):
                    with html.Template(v_slot_activator="{ on, attrs }"):
                        vuetify.VIcon("mdi-cog-outline", style="font-size: 30px;", v_bind="attrs", v_on="on")
                    html.Span("Settings")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                vuetify.VListItemTitle("Settings", classes="text-overline")

        with vuetify.VExpandTransition():
            with html.Div(v_show=("settings_open", False)):
                with vuetify.VListItem(classes="mt-2 text-left ml-4"):
                    html.Span("Camera / View", classes="text-caption grey--text font-weight-bold")

                # # Toggle Theme
                # with vuetify.VListItem(class_="nav-item nav-item--nested", link=True, ripple=True):
                #     with vuetify.VListItemIcon():
                #         with vuetify.VTooltip(right=True):
                #             with html.Template(v_slot_activator="{ on, attrs }"):
                #                 vuetify.VIcon("mdi-lightbulb-outline", style="font-size: 25px;", v_bind="attrs", v_on="on")
                #             html.Span("Light on/off")
                #     with vuetify.VListItemContent(v_if="!drawer_mini"):
                #         html.Span("Toggle Theme")
                
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

                with vuetify.VListItem(classes="mt-4 text-left ml-4"):
                    html.Span("Biomni / LLM", classes="text-caption grey--text font-weight-bold")

                # Row 1: Model, DB Model, Mode
                with vuetify.VListItem(class_="nav-item nav-item--nested", classes="mb-2 px-4"):
                    with vuetify.VRow(dense=True, no_gutters=False):
                        with vuetify.VCol(cols=4, classes="py-0"):
                            vuetify.VSelect(
                                v_model=("biomni_model",),
                                items=("biomni_available_models",),
                                label="Model",
                                dense=True,
                                outlined=True,
                                hide_details=True,
                                style="max-height: 32px;",
                                dark=True,
                            )
                        with vuetify.VCol(cols=4, classes="py-0"):
                            vuetify.VSelect(
                                v_model=("biomni_db_model",),
                                items=("biomni_available_models",),
                                label="DB Model",
                                dense=True,
                                outlined=True,
                                hide_details=True,
                                style="max-height: 32px;",
                                dark=True,
                            )
                        with vuetify.VCol(cols=4, classes="py-0"):
                            vuetify.VSelect(
                                v_model=("biomni_mode",),
                                items=(["full", "db", "minimal"],),
                                label="Mode",
                                dense=True,
                                outlined=True,
                                hide_details=True,
                                style="max-height: 32px;",
                                dark=True,
                            )

                # Row 2: Port, Dataset Description
                with vuetify.VListItem(class_="nav-item nav-item--nested", classes="mb-2 px-4"):
                    with vuetify.VRow(dense=True, no_gutters=False):
                        with vuetify.VCol(cols=3, classes="py-0"):
                            vuetify.VTextField(
                                v_model=("biomni_port",),
                                label="Port",
                                type="number",
                                dense=True,
                                outlined=True,
                                hide_details=True,
                                style="max-height: 32px;",
                                dark=True,
                            )
                        with vuetify.VCol(cols=9, classes="py-0"):
                            vuetify.VTextField(
                                v_model=("biomni_dataset",),
                                label="Dataset Description",
                                dense=True,
                                outlined=True,
                                hide_details=True,
                                style="max-height: 32px;",
                                dark=True,
                            )

                # Initialize / Update button
                with vuetify.VListItem(class_="nav-item nav-item--nested pt-2 pb-2"):
                    vuetify.VBtn(
                        "{{ chatbot_authenticated ? 'Update Settings' : 'Initialize Biomni' }}",
                        click=ctrl.chatbot_login,
                        block=True,
                        small=True,
                        loading=("chatbot_loading",),
                        disabled=("chatbot_loading",),
                    )
                with vuetify.VListItem(
                        v_show=("biomni_init_error", False),
                        class_="nav-item nav-item--nested px-4 pb-2",
                ):
                    html.Span(
                        "{{ biomni_init_error }}",
                        style="font-size: 0.7rem; color: #ff8a80; white-space: normal; word-break: break-word;",
                    )

                # File Upload section (only shown after initialization)
                with vuetify.VExpandTransition():
                    with html.Div(v_show=("chatbot_authenticated",)):
                        with vuetify.VListItem(classes="mt-2 text-left ml-4"):
                            html.Span("Upload Context Data", classes="text-caption grey--text font-weight-bold")

                        with vuetify.VListItem(class_="nav-item nav-item--nested", classes="mb-2 px-4"):
                            with vuetify.VRow(dense=True, no_gutters=False, align="center"):
                                with vuetify.VCol(cols=5, classes="py-0"):
                                    vuetify.VFileInput(
                                        label="Upload file",
                                        dense=True,
                                        outlined=True,
                                        hide_details=True,
                                        truncate_length=2,
                                        style="max-height: 32px;",
                                        __events=["change"],
                                        change=(ctrl.biomni_add_data, "[$event]"),
                                    )
                                with vuetify.VCol(cols=6, classes="py-0"):
                                    vuetify.VTextField(
                                        v_model=("biomni_file_description",),
                                        label="File description",
                                        dense=True,
                                        outlined=True,
                                        hide_details=True,
                                        style="max-height: 32px;",
                                    )
                                with vuetify.VCol(cols=1, classes="py-0 d-flex justify-center"):
                                    vuetify.VIcon(
                                        "mdi-check-circle",
                                        v_show=("biomni_upload_success",),
                                        color="white",
                                        small=True,
                                    )

                        with vuetify.VListItem(class_="nav-item nav-item--nested pt-1 pb-2 px-4"):
                            vuetify.VBtn(
                                "Upload File",
                                click=ctrl.biomni_upload_file,
                                block=True,
                                small=True,
                                disabled=("!biomni_file_description",),
                            )
