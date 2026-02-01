# right_drawer.py
"""Right drawer component with analysis panel."""

from __future__ import annotations

from trame.widgets import html, vuetify


def right_drawer(state, ctrl):
    """Create the right drawer with analysis panel."""
    
    # Only show when analysis is loaded
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
            vuetify.VListItemTitle("Analysis", classes="text-subtitle-1")
            vuetify.VSpacer()
            with vuetify.VBtn(icon=True, small=True, click="right_drawer_open = false"):
                vuetify.VIcon("mdi-close", small=True)
        
        vuetify.VDivider()
        
        # Analysis controls
        with html.Div(class_="px-4 py-3"):
            # Dilation control section
            with html.Div(class_="mb-4"):
                html.Div("Dilation", class_="text-overline mb-2")
                
                # Dilation slider with discrete steps from analysis_dilation_amounts
                vuetify.VSlider(
                    v_model=("current_dilation",),
                    min=("analysis_dilation_amounts.length > 0 ? analysis_dilation_amounts[0] : 0",),
                    max=("analysis_dilation_amounts.length > 0 ? analysis_dilation_amounts[analysis_dilation_amounts.length - 1] : 0",),
                    step=("analysis_dilation_amounts.length > 1 ? analysis_dilation_amounts[1] - analysis_dilation_amounts[0] : 1",),
                    thumb_label="always",
                    tick_labels=("analysis_dilation_amounts",),
                    ticks="always",
                    dense=True,
                    hide_details=False,
                    class_="mt-6",
                )
                
                # Show current value
                with html.Div(class_="text-caption text-center mt-1"):
                    html.Span("Current: ")
                    html.Span("{{ current_dilation }}", class_="font-weight-bold")
            
            vuetify.VDivider(class_="my-3")
            
            # Hierarchy level control
            with html.Div(class_="mb-4"):
                html.Div("Detail Level", class_="text-overline mb-2")
                
                with vuetify.VBtnToggle(
                    v_model=("current_hierarchy_level",),
                    mandatory=True,
                    dense=True,
                    class_="d-flex",
                ):
                    vuetify.VBtn(
                        v_for="level in analysis_hierarchy_levels",
                        key="level",
                        value=("level",),
                        small=True,
                        v_text="level === 0 ? 'Fine' : (level === 1 ? 'Medium' : 'Coarse')",
                    )
            
            vuetify.VDivider(class_="my-3")
            
            # Heatmap info
            with html.Div(class_="mb-4"):
                html.Div("Heatmap", class_="text-overline mb-2")
                
                with html.Div(class_="d-flex align-center justify-space-between"):
                    html.Span("Tiles shown:", class_="text-caption")
                    html.Span("{{ heatmap_tile_count }}", class_="text-caption font-weight-bold")
                
                # Heatmap visibility toggle
                vuetify.VSwitch(
                    v_model=("heatmap_visible",),
                    label="Show heatmap",
                    dense=True,
                    hide_details=True,
                    class_="mt-2",
                )
            
            vuetify.VDivider(class_="my-3")
            
            # Analysis metadata info
            with html.Div():
                html.Div("Analysis Info", class_="text-overline mb-2")
                
                with html.Div(class_="text-caption"):
                    with html.Div(class_="d-flex justify-space-between"):
                        html.Span("File:")
                        html.Span("{{ analysis_file_name }}", class_="text-truncate", style="max-width: 150px;")
                    
                    with html.Div(class_="d-flex justify-space-between"):
                        html.Span("Channels:")
                        html.Span("{{ analysis_channels.length }}")
                    
                    with html.Div(class_="d-flex justify-space-between"):
                        html.Span("Dilations:")
                        html.Span("{{ analysis_dilation_amounts.join(', ') }}")
                    
                    with html.Div(class_="d-flex justify-space-between"):
                        html.Span("Levels:")
                        html.Span("{{ analysis_hierarchy_levels.length }}")
        
        vuetify.VDivider()
        
        # UpSet plot container (for future use)
        html.Div(id="upset-container", style="width: 100%; height: 300px; padding: 8px;")