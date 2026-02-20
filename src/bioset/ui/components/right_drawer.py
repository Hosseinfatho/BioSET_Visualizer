from __future__ import annotations

from trame.widgets import html, vuetify

from .chatbot import chatbot_section


def right_drawer(state, ctrl):
        
    with vuetify.VNavigationDrawer(
        v_model=("right_drawer_open",),
        v_if="analysis_loaded",
        app=True,
        right=True,
        width=350,
        dark=True,
        color="rgba(18, 18, 18, 0.6)",
    ):
        # Header
        with vuetify.VListItem(classes="px-3 py-2"):
            vuetify.VListItemTitle("Analysis", classes="")
            vuetify.VSpacer()
            with vuetify.VBtn(icon=True, small=True, click="right_drawer_open = false"):
                vuetify.VIcon("mdi-close", small=True)
        
        vuetify.VDivider()
        
        with html.Div(classes="px-4 py-3"):
            with html.Div(classes="mb-4"):
                html.Div("Dilation", classes="text-overline", style="width: 100%; align-items: center; display: flex; justify-content: center; color:white;")
                
                # Dilation slider 
                with html.Div(classes="d-flex align-center justify-center flex-nowrap mb-4"):
                    with vuetify.VBtn(
                        icon=True,
                        small=True,
                        click="current_dilation = Math.max(analysis_dilation_amounts[0] || 0, current_dilation - (analysis_dilation_amounts.length > 1 ? analysis_dilation_amounts[1] - analysis_dilation_amounts[0] : 1))",
                    ):
                        vuetify.VIcon("mdi-minus", small=True)
                    
                    vuetify.VSlider(
                        v_model=("current_dilation",),
                        min=("analysis_dilation_amounts.length > 0 ? analysis_dilation_amounts[0] : 0",),
                        max=("analysis_dilation_amounts.length > 0 ? analysis_dilation_amounts[analysis_dilation_amounts.length - 1] : 0",),
                        step=("analysis_dilation_amounts.length > 1 ? analysis_dilation_amounts[1] - analysis_dilation_amounts[0] : 1",),
                        # thumb_label=True,
                        ticks="always",
                        dense=True,
                        dark=True,
                        hide_details=False,
                        class_="mx-2",
                        #style="max-width: 200px; flex-shrink: 0;",
                        tick_labels=("analysis_dilation_amounts.map(v => v + ' \u03BCm')",),
                    )
                    
                    with vuetify.VBtn(
                        icon=True,
                        small=True,
                        click="current_dilation = Math.min(analysis_dilation_amounts[analysis_dilation_amounts.length - 1] || 0, current_dilation + (analysis_dilation_amounts.length > 1 ? analysis_dilation_amounts[1] - analysis_dilation_amounts[0] : 1))",
                    ):
                        vuetify.VIcon("mdi-plus", small=True)
            
            vuetify.VDivider()
            
            # Hierarchy level control
            with html.Div(classes="mb-4 mt-3"):
                html.Div("Heatmap", classes="text-overline mb-3 text-center", style="color: white;")
                
                with html.Div(classes="d-flex justify-center"):
                    with vuetify.VBtnToggle(
                        v_model=("current_hierarchy_level",),
                        mandatory=True,
                        dense=True,
                    ):
                        vuetify.VBtn(
                            v_for="level in analysis_hierarchy_levels",
                            key="level",
                            value=("level",),
                            small=True,
                            v_text="level === 0 ? 'Fine' : (level === 1 ? 'Medium' : (level === 2 ? 'Coarse' : 'Overview'))",
                        )
        
        vuetify.VDivider()
        
        # UpSet plot container
        with html.Div(classes="px-4 py-3"):
            html.Div("Marker Combinations", classes="text-overline mb-2 text-center", style="color: white;")
 
            with html.Div(classes="d-flex justify-center mb-2 align-center"):
                with vuetify.VBtnToggle(
                    v_model=("upset_view_mode", "global"),
                    mandatory=True,
                    dense=True,
                    classes="mr-2",
                    style="background: transparent;",
                ):
                    vuetify.VBtn("Global", value="global", small=True, classes="text-capitalize", outlined=True)
                    vuetify.VBtn("Local", value="local", small=True, classes="text-capitalize", outlined=True)
                
                # UpSet Filter button
                with vuetify.VBtn(icon=True, small=True, click="upset_filter_dialog = true"):
                    vuetify.VIcon("mdi-filter-variant", small=True)

                # UpSet Expand button
                with vuetify.VBtn(icon=True, small=True, click="upset_expanded = true"):
                    vuetify.VIcon("mdi-arrow-expand-all", small=True)

            # Pagination Controls (Offset based)
            with html.Div(classes="d-flex justify-center mb-2 align-center"):
                with vuetify.VBtn(
                        icon=True,
                        small=True,
                        disabled=("upset_offset <= 0",),
                        click="upset_offset = 0"
                ):
                    vuetify.VIcon("mdi-skip-backward", small=True)

                with vuetify.VBtn(
                        icon=True,
                        small=True,
                        disabled=("upset_offset <= 0",),
                        click="upset_offset = Math.max(0, upset_offset - 1)"
                ):
                    vuetify.VIcon("mdi-chevron-left", small=True)

                html.Span(
                    "{{ upset_offset + 1 }} - {{ Math.min(upset_offset + upset_limit, (upset_view_mode === 'local' ? upset_data_local.length : upset_data.length)) }}",
                    classes="text-caption mx-2",
                    style="color: white; min-width: 50px; text-align: center;"
                )

                with vuetify.VBtn(
                        icon=True,
                        small=True,
                        disabled=(
                                "upset_offset + upset_limit >= (upset_view_mode === 'local' ? upset_data_local.length : upset_data.length)",),
                        click="upset_offset = upset_offset + 1"
                ):
                    vuetify.VIcon("mdi-chevron-right", small=True)

            # UpSet Expand Dialog
            with vuetify.VDialog(v_model=("upset_expanded",), width="auto"):
                with vuetify.VCard(classes="grey darken-4 white--text", style="overflow: hidden;"):
                    with vuetify.VCardTitle(classes="headline grey darken-3"):
                        html.Span("Marker Combinations (Expanded)")
                        vuetify.VSpacer()
                        vuetify.VBtn("Close", color="surface-variant", click="upset_expanded = false")

                    with vuetify.VCardText(classes="pa-4"):
                        # Expanded Pagination Controls
                        with html.Div(classes="d-flex justify-center mb-2 align-center"):
                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    color="white",
                                    dark=True,
                                    disabled=("upset_expanded_offset <= 0",),
                                    click="upset_expanded_offset = 0"
                            ):
                                vuetify.VIcon("mdi-skip-backward", small=True)

                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    color="white",
                                    dark=True,
                                    disabled=("upset_expanded_offset <= 0",),
                                    click="upset_expanded_offset = Math.max(0, upset_expanded_offset - 1)"
                            ):
                                vuetify.VIcon("mdi-chevron-left", small=True)

                            html.Span(
                                "{{ upset_expanded_offset + 1 }} - {{ Math.min(upset_expanded_offset + upset_expanded_limit, (upset_view_mode === 'local' ? upset_data_local.length : upset_data.length)) }}",
                                classes="text-caption mx-2",
                                style="color: white; min-width: 50px; text-align: center;"
                            )

                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    color="white",
                                    dark=True,
                                    disabled=(
                                            "upset_expanded_offset + upset_expanded_limit >= (upset_view_mode === 'local' ? upset_data_local.length : upset_data.length)",),
                                    click="upset_expanded_offset = upset_expanded_offset + 1"
                            ):
                                vuetify.VIcon("mdi-chevron-right", small=True)

                        vuetify.Template(
                            """
                            <upset-plot
                                :data="upset_data"
                                :dataLocal="upset_data_local"
                                :channelData="channels"
                                :view-mode="upset_view_mode"
                                :offset="upset_expanded_offset"
                                :limit="upset_expanded_limit"
                                :width="1200"
                                :height="800"
                                @click="trigger('upset_click', $event)"
                            />
                            """
                        )

            # UpSet Filter Dialog
            with vuetify.VDialog(v_model=("upset_filter_dialog",), max_width="600px", scrollable=True):
                with vuetify.VCard(classes="grey darken-4 white--text"):
                    vuetify.VCardTitle("Select channels for UpSet plot", classes="headline grey darken-3")
                    vuetify.VDivider()
                    
                    with vuetify.VCardText():
                        with html.Div(classes="d-flex justify-space-between my-2"):
                            # Search bar
                            vuetify.VTextField(
                                v_model=("upset_search",),
                                label="Search channels...",
                                prepend_inner_icon="mdi-magnify",
                                clearable=True,
                                dense=True,
                                hide_details=True,
                                classes="ma-1",
                                dark=True,
                            )

                            vuetify.VBtn("Select All", text=True, color="white", classes="ma-1", 
                                    click="upset_selected_channels = analysis_channels")
                            vuetify.VBtn("Deselect All", text=True, color="white", classes="ma-1", 
                                    click="upset_selected_channels = []")
                            
                        
                        vuetify.VDivider(classes="mb-2")
                        
                        # Use checkboxes directly
                        with html.Div():
                            vuetify.VCheckbox(
                                v_for=("channel in upset_filtered_channels",),
                                key="channel",
                                v_model=("upset_selected_channels",),
                                label=("channel",),
                                value=("channel",),
                                dense=True,
                                hide_details=True,
                                dark=True,
                            )

                    vuetify.VDivider()
                    with vuetify.VCardActions(classes="grey darken-3"):
                        vuetify.VSpacer()
                        vuetify.VBtn("Close", color="surface-variant", click="upset_filter_dialog = false")
            
            # Vue component for UpSet plot
            vuetify.Template(
                """
                <upset-plot
                    :data="upset_data"
                    :dataLocal="upset_data_local"
                    :channelData="channels"
                    :view-mode="upset_view_mode"
                    :offset="upset_offset"
                    :limit="upset_limit"
                    @click="trigger('upset_click', $event)"
                />
                """
            )
        
        vuetify.VDivider()
        
        # Bar chart container
        with html.Div(classes="px-4 py-3"):
            html.Div("Marker Coverage", classes="text-overline mb-2 text-center", style="color: white;")
  
            with html.Div(classes="d-flex justify-center mb-2 align-center"):
                with vuetify.VBtnToggle(
                    v_model=("bar_view_mode", "global"),
                    mandatory=True,
                    dense=True,
                    classes="mr-2",
                    style="background: transparent;",
                ):
                    vuetify.VBtn("Global", value="global", small=True, classes="text-capitalize", outlined=True)
                    vuetify.VBtn("Local", value="local", small=True, classes="text-capitalize", outlined=True)

                # Bar Filter button
                with vuetify.VBtn(icon=True, small=True, click="bar_filter_dialog = true"):
                    vuetify.VIcon("mdi-filter-variant", small=True)

                # Bar Expand button
                with vuetify.VBtn(icon=True, small=True, click="bar_expanded = true"):
                    vuetify.VIcon("mdi-arrow-expand-all", small=True)

            # Pagination Controls (Offset based) - Bar Chart
            with html.Div(classes="d-flex justify-center mb-2 align-center"):
                with vuetify.VBtn(
                        icon=True,
                        small=True,
                        disabled=("bar_offset <= 0",),
                        click="bar_offset = 0"
                ):
                    vuetify.VIcon("mdi-skip-backward", small=True)

                with vuetify.VBtn(
                        icon=True,
                        small=True,
                        disabled=("bar_offset <= 0",),
                        click="bar_offset = Math.max(0, bar_offset - 1)"
                ):
                    vuetify.VIcon("mdi-chevron-left", small=True)

                html.Span(
                    "{{ bar_offset + 1 }} - {{ Math.min(bar_offset + bar_limit, (bar_view_mode === 'local' ? bar_data_local.length : bar_data.length)) }}",
                    classes="text-caption mx-2",
                    style="color: white; min-width: 50px; text-align: center;"
                )

                with vuetify.VBtn(
                        icon=True,
                        small=True,
                        disabled=(
                                "bar_offset + bar_limit >= (bar_view_mode === 'local' ? bar_data_local.length : bar_data.length)",),
                        click="bar_offset = bar_offset + 1"
                ):
                    vuetify.VIcon("mdi-chevron-right", small=True)

            # Bar Expand Dialog
            with vuetify.VDialog(v_model=("bar_expanded",), width="auto"):
                with vuetify.VCard(classes="grey darken-4 white--text", style="overflow: hidden;"):
                    with vuetify.VCardTitle(classes="headline grey darken-3"):
                        html.Span("Channel Frequencies (Expanded)")
                        vuetify.VSpacer()
                        vuetify.VBtn("Close", color="surface-variant", click="bar_expanded = false")

                    with vuetify.VCardText(classes="pa-4"):
                        # Expanded Pagination Controls
                        with html.Div(classes="d-flex justify-center mb-2 align-center"):
                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    color="white",
                                    dark=True,
                                    disabled=("bar_expanded_offset <= 0",),
                                    click="bar_expanded_offset = 0"
                            ):
                                vuetify.VIcon("mdi-skip-backward", small=True)

                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    color="white",
                                    dark=True,
                                    disabled=("bar_expanded_offset <= 0",),
                                    click="bar_expanded_offset = Math.max(0, bar_expanded_offset - 1)"
                            ):
                                vuetify.VIcon("mdi-chevron-left", small=True)

                            html.Span(
                                "{{ bar_expanded_offset + 1 }} - {{ Math.min(bar_expanded_offset + bar_expanded_limit, (bar_view_mode === 'local' ? bar_data_local.length : bar_data.length)) }}",
                                classes="text-caption mx-2",
                                style="color: white; min-width: 50px; text-align: center;"
                            )

                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    color="white",
                                    dark=True,
                                    disabled=(
                                            "bar_expanded_offset + bar_expanded_limit >= (bar_view_mode === 'local' ? bar_data_local.length : bar_data.length)",),
                                    click="bar_expanded_offset = bar_expanded_offset + 1"
                            ):
                                vuetify.VIcon("mdi-chevron-right", small=True)

                        vuetify.Template(
                            """
                            <bar-plot
                                :data="bar_data"
                                :dataLocal="bar_data_local"
                                :channelData="channels"
                                :view-mode="bar_view_mode"
                                :offset="bar_expanded_offset"
                                :limit="bar_expanded_limit"
                                :width="1200"
                                :height="700"
                                @click="trigger('bar_click', $event)"
                            />
                            """
                        )

            # Bar Filter Dialog
            with vuetify.VDialog(v_model=("bar_filter_dialog",), max_width="600px", scrollable=True):
                with vuetify.VCard(classes="grey darken-4 white--text"):
                    vuetify.VCardTitle("Select channels for Bar plot", classes="headline grey darken-3")
                    vuetify.VDivider()
                    
                    with vuetify.VCardText():
                        with html.Div(classes="d-flex justify-space-between my-2"):
                            # Search bar
                            vuetify.VTextField(
                                v_model=("bar_search",),
                                label="Search channels...",
                                prepend_inner_icon="mdi-magnify",
                                clearable=True,
                                dense=True,
                                hide_details=True,
                                classes="ma-1",
                                dark=True,
                            )

                    
                            vuetify.VBtn("Select All", text=True, color="white", classes="ma-1", 
                                click="bar_selected_channels = analysis_channels")
                            vuetify.VBtn("Deselect All", text=True, color="white", classes="ma-1",
                                click="bar_selected_channels = []")
                        
                        vuetify.VDivider(classes="mb-2")
                        
                        # Use checkboxes directly
                        with html.Div():
                            vuetify.VCheckbox(
                                v_for=("channel in bar_filtered_channels",),
                                key="channel",
                                v_model=("bar_selected_channels",),
                                label=("channel",),
                                value=("channel",),
                                dense=True,
                                hide_details=True,
                                dark=True,
                            )

                    vuetify.VDivider()
                    with vuetify.VCardActions(classes="grey darken-3"):
                        vuetify.VSpacer()
                        vuetify.VBtn("Close", color="surface-variant", click="bar_filter_dialog = false")

            # Vue component for Bar chart
            vuetify.Template(
                """
                <bar-plot
                    :data="bar_data"
                    :dataLocal="bar_data_local"
                    :channelData="channels"
                    :view-mode="bar_view_mode"
                    :offset="bar_offset"
                    :limit="bar_limit"
                    @click="trigger('bar_click', $event)"
                />
                """
            )
            
        # Chatbot section
        chatbot_section(state, ctrl)
