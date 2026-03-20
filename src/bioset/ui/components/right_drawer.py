from __future__ import annotations

from trame.widgets import html, vuetify


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

                # UpSet Explain button
                with vuetify.VBtn(
                    icon=True,
                    small=True,
                    click=ctrl.chatbot_explain_upset,
                    disabled=("!chatbot_authenticated",),
                ):
                    vuetify.VIcon("mdi-message-text-outline", small=True)

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
                                :metric="upset_metric"
                                :offset="upset_expanded_offset"
                                :limit="upset_expanded_limit"
                                :width="1200"
                                :height="800"
                                @click="upset_click = $event"
                            />
                            """
                        )

            # UpSet Filter Dialog
            with vuetify.VDialog(v_model=("upset_filter_dialog",), max_width="600px", scrollable=True):
                with vuetify.VCard(classes="grey darken-4 white--text"):
                    vuetify.VCardTitle("Settings - UpSet", classes="headline grey darken-3")
                    vuetify.VDivider()
                    
                    with vuetify.VCardText():
                        with html.Div(classes="mb-4 mt-2"):
                            html.Div("Metric", classes="text-caption mb-2 text-left", style="color: white;")
                            with html.Div(classes="d-flex justify-space-between", style="width: 100%; gap: 8px;"):
                                vuetify.VBtn(
                                    "IoU",
                                    click="upset_metric = 'iou'",
                                    color=("upset_metric === 'iou' ? 'white' : 'grey darken-3'",),
                                    dark=("upset_metric !== 'iou'",),
                                    title="Intersection over Union",
                                    class_="flex-grow-1 rounded px-4",
                                    style="flex: 1;"
                                )
                                vuetify.VBtn(
                                    "Overlap Coefficient",
                                    click="upset_metric = 'overlap_coeff'",
                                    color=("upset_metric === 'overlap_coeff' ? 'white' : 'grey darken-3'",),
                                    dark=("upset_metric !== 'overlap_coeff'",),
                                    title="Overlap Coefficient",
                                    class_="flex-grow-1 rounded px-4",
                                    style="flex: 1;"
                                )

                        with html.Div(classes="mb-4 mt-2"):
                            html.Div("Minimum Number of Channels", classes="text-caption mb-2 text-left", style="color: white;")
                            with html.Div(classes="d-flex justify-space-between", style="width: 100%; gap: 8px;"):
                                for val in [1, 2, 3, 4, 5]:
                                    vuetify.VBtn(
                                        str(val),
                                        click=f"upset_min_channels = {val}",
                                        color=(f"upset_min_channels === {val} ? 'white' : 'grey darken-3'",),
                                        dark=(f"upset_min_channels !== {val}",),
                                        class_="flex-grow-1 rounded px-4",
                                        elevation=0,
                                        style="flex: 1;",
                                    )

                        vuetify.VDivider(classes="mb-3")

                        html.Div("Channels", classes="text-caption mb-2 text-left", style="color: white;")

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

                        vuetify.VDivider(classes="mb-3 mt-1")
                        
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
                    :metric="upset_metric"
                    :offset="upset_offset"
                    :limit="upset_limit"
                    @click="upset_click = $event"
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

                # Bar Explain button
                with vuetify.VBtn(
                    icon=True,
                    small=True,
                    click=ctrl.chatbot_explain_bar,
                    disabled=("!chatbot_authenticated",),
                ):
                    vuetify.VIcon("mdi-message-text-outline", small=True)

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
                    vuetify.VCardTitle("Settings - Bar Chart", classes="headline grey darken-3")
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

        vuetify.VDivider()

        with html.Div(classes="px-4 py-3"):
            html.Div("Dilation Curves", classes="text-overline mb-2 text-center", style="color: white;")

            with html.Div(classes="d-flex justify-center mb-2 align-center"):
                with vuetify.VBtnToggle(
                        v_model=("dilation_view_mode", "single"),
                        mandatory=True,
                        dense=True,
                        classes="mr-2",
                        style="background: transparent;",
                ):
                    vuetify.VBtn("Single", value="single", small=True, classes="text-capitalize", outlined=True)
                    vuetify.VBtn("Multiple", value="multiple", small=True, classes="text-capitalize", outlined=True)

                # Filter button
                with vuetify.VBtn(icon=True, small=True, v_show="dilation_view_mode === 'multiple'",
                                  click="dilation_filter_dialog = true"):
                    vuetify.VIcon("mdi-filter-variant", small=True)

            with vuetify.VDialog(v_model=("dilation_filter_dialog",), max_width="900px", scrollable=True):
                with vuetify.VCard(classes="grey darken-4 white--text"):
                    vuetify.VCardTitle("Settings - Dilation Plot", classes="headline grey darken-3")
                    vuetify.VDivider()

                    with vuetify.VCardText():
                        with html.Div(v_show="dilation_view_mode === 'multiple'", classes="mb-4"):
                            html.Div("Intersection Metric", classes="text-overline mb-1", style="color: white;")
                            with html.Div(classes="d-flex justify-space-between", style="width: 100%; gap: 8px;"):
                                vuetify.VBtn(
                                    "IoU",
                                    click="dilation_metric_multiple = 'iou'",
                                    color=("dilation_metric_multiple === 'iou' ? 'white' : 'grey darken-3'",),
                                    dark=("dilation_metric_multiple !== 'iou'",),
                                    title="Intersection over Union",
                                    class_="flex-grow-1 rounded px-4",
                                    style="flex: 1;"
                                )
                                vuetify.VBtn(
                                    "Overlap Coefficient",
                                    click="dilation_metric_multiple = 'overlap_coeff'",
                                    color=("dilation_metric_multiple === 'overlap_coeff' ? 'white' : 'grey darken-3'",),
                                    dark=("dilation_metric_multiple !== 'overlap_coeff'",),
                                    title="Overlap Coefficient",
                                    class_="flex-grow-1 rounded px-4",
                                    style="flex: 1;"
                                )
                                vuetify.VBtn(
                                    "Density",
                                    click="dilation_metric_multiple = 'density'",
                                    color=("dilation_metric_multiple === 'density' ? 'white' : 'grey darken-3'",),
                                    dark=("dilation_metric_multiple !== 'density'",),
                                    title="Density",
                                    class_="flex-grow-1 rounded px-4",
                                    style="flex: 1;"
                                )
                                vuetify.VBtn(
                                    "Count",
                                    click="dilation_metric_multiple = 'count'",
                                    color=("dilation_metric_multiple === 'count' ? 'white' : 'grey darken-3'",),
                                    dark=("dilation_metric_multiple !== 'count'",),
                                    title="Count",
                                    class_="flex-grow-1 rounded px-4",
                                    style="flex: 1;"
                                )

                    vuetify.VDivider()
                    with vuetify.VCardActions(classes="grey darken-3"):
                        vuetify.VSpacer()
                        vuetify.VBtn("Close", color="surface-variant", click="dilation_filter_dialog = false")

            # Vue component for Line plot
            vuetify.Template(
                """
                <linechart
                    :data="dilation_data"
                    :channelData="channels"
                    :view-mode="dilation_view_mode"
                    :metric="dilation_view_mode === 'single' ? dilation_metric_single : dilation_metric_multiple"
                />
                """
            )
