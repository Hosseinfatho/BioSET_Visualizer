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
                html.Div("Detail Level", classes="text-overline mb-3 text-center", style="color: white;")
                
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
                            v_text="level === 0 ? 'Fine' : (level === 1 ? 'Medium' : 'Coarse')",
                        )
            
            # vuetify.VDivider(class_="my-3")
            
            # Heatmap info
            # with html.Div(classes="mb-4"):
            #     html.Div("Heatmap", classes="text-overline mb-2", style="color: white;")
                
            #     with html.Div(classes="d-flex align-center justify-space-between"):
            #         html.Span("Tiles shown:", classes="text-caption")
            #         html.Span("{{ heatmap_tile_count }}", classes="text-caption font-weight-bold")
                
            #     # Heatmap visibility toggle
            #     vuetify.VSwitch(
            #         v_model=("heatmap_visible",),
            #         label="Show heatmap",
            #         dense=True,
            #         hide_details=True,
            #         class_="mt-2",
            #     )
            
            # vuetify.VDivider(class_="my-3")
            
            # Analysis metadata info
            # with html.Div():
            #     html.Div("Analysis Info", classes="text-overline mb-2", style="color: white;")
                
            #     with html.Div(classes="text-caption"):
            #         with html.Div(classes="d-flex justify-space-between"):
            #             html.Span("File:")
            #             html.Span("{{ analysis_file_name }}", classes="text-truncate", style="max-width: 150px;")
                    
            #         with html.Div(classes="d-flex justify-space-between"):
            #             html.Span("Channels:")
            #             html.Span("{{ analysis_channels.length }}")
                    
            #         with html.Div(classes="d-flex justify-space-between"):
            #             html.Span("Dilations:")
            #             html.Span("{{ analysis_dilation_amounts.join(', ') }}")
                    
            #         with html.Div(classes="d-flex justify-space-between"):
            #             html.Span("Levels:")
            #             html.Span("{{ analysis_hierarchy_levels.length }}")
        
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
                    @click="trigger('upset_click', $event)"
                />
                """
            )
        
        vuetify.VDivider()
        
        # Bar chart container
        with html.Div(classes="px-4 py-3"):
            html.Div("Channel Frequencies", classes="text-overline mb-2 text-center", style="color: white;")
  
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
                    @click="trigger('bar_click', $event)"
                />
                """
            )
            html.Div("Marker Combinations", classes="text-overline mb-3 text-center", style="color: white;")
            html.Div(id="upset-container", style="width: 100%; height: 300px;")
            
        # Chatbot section
        chatbot_section(state, ctrl)
