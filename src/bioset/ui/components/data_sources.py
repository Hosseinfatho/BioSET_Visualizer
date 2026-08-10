# data_sources.py
"""Data sources section in left drawer component."""

from __future__ import annotations

from trame.widgets import html, vuetify


def data_sources_section(state, ctrl):
    """Create the data sources section with URL inputs and Load button."""
    
    def toggle_data():
        state.data_open = not state.data_open
    
    with vuetify.VList(dense=True, nav=True):
        with vuetify.VListItem(
            class_=("data_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
                classes="mb-0",
            link=True,
            ripple=True,
            click=toggle_data,
        ):
            with vuetify.VListItemIcon():
                with vuetify.VTooltip(right=True, disabled=("!drawer_mini",)):
                    with html.Template(v_slot_activator="{ on, attrs }"):
                        vuetify.VIcon("mdi-database-outline", style="font-size: 30px;", v_bind="attrs", v_on="on")
                    html.Span("Data Sources")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                vuetify.VListItemTitle("Data Sources", classes="text-overline")
        
        with vuetify.VExpandTransition():
            with html.Div(
                    v_show=("data_open", False),
                    classes="mt-1",
            ):
                with vuetify.VListItem(class_="nav-item nav-item--nested"):
                    with vuetify.VListItemIcon(v_if="drawer_mini"):
                        with vuetify.VTooltip(right=True):
                            with html.Template(v_slot_activator="{ on, attrs }"):
                                vuetify.VIcon("mdi-link-variant", style="font-size: 25px;", v_bind="attrs", v_on="on")
                            html.Span("{{ zarr_url || 'Zarr URL' }}")
                    with vuetify.VListItemContent(v_if="!drawer_mini", class_="mb-0 pb-0"):
                        vuetify.VTextField(
                            v_model=("zarr_url", ""),
                            label="Zarr URL",
                            dense=True,
                            clearable=True,
                            hide_details=True,
                        )
                        
                # Metadata is normally baked into the zarr; the URL field stays
                # collapsed behind this toggle for stores that lack it.
                with vuetify.VListItem(class_="nav-item nav-item--nested", v_if="!drawer_mini"):
                    with vuetify.VListItemContent(class_="mt-0 pt-0 mb-0 pb-0"):
                        with html.Div(classes="d-flex align-center"):
                            html.Span(
                                "Separate metadata",
                                style="font-size: 11px; color: #9e9e9e;",
                            )
                            with vuetify.VBtn(
                                icon=True,
                                x_small=True,
                                classes="ml-1",
                                click="metadata_open = !metadata_open",
                            ):
                                vuetify.VIcon(
                                    "{{ metadata_open ? 'mdi-minus' : 'mdi-plus' }}",
                                    x_small=True,
                                )

                with vuetify.VExpandTransition():
                    with vuetify.VListItem(
                        class_="nav-item nav-item--nested",
                        v_show=("metadata_open", False),
                    ):
                        with vuetify.VListItemIcon(v_if="drawer_mini"):
                            with vuetify.VTooltip(right=True):
                                with html.Template(v_slot_activator="{ on, attrs }"):
                                    vuetify.VIcon("mdi-file-document-outline", style="font-size: 25px;", v_bind="attrs", v_on="on")
                                html.Span("{{ metadata_url || 'Metadata URL' }}")
                        with vuetify.VListItemContent(v_if="!drawer_mini", class_="mt-0 pt-0 mb-0 pb-0"):
                            vuetify.VTextField(
                                v_model=("metadata_url", ""),
                                label="Metadata URL",
                                dense=True,
                                clearable=True,
                                hide_details=True,
                            )

                with vuetify.VListItem(class_="nav-item nav-item--nested",v_if="!drawer_mini && !data_loaded"):
                    with vuetify.VListItemContent(class_="mt-2 pt-0"):
                        vuetify.VBtn(
                            "Load Data",
                            click=ctrl.load_data,
                            loading=("data_loading", False),
                            disabled=("data_loading", False),
                            block=True,
                            small=True,
                        )
                      
                with vuetify.VListItem(class_="nav-item nav-item--nested",v_if="data_loaded"):  
                    with vuetify.VListItemIcon(v_if="drawer_mini"):
                        with vuetify.VTooltip(right=True):
                            with html.Template(v_slot_activator="{ on, attrs }"):
                                vuetify.VIcon(
                                    "mdi-close-circle-outline",
                                    style="font-size: 25px;",
                                    v_bind="attrs",
                                    v_on="on",
                                    link=True,
                                    click=ctrl.clear_data,
                                )
                            html.Span("Clear Data")
                            
                    with vuetify.VListItemContent(class_="mt-2 pt-0"):
                        vuetify.VBtn(
                            "Clear Data",
                            click=ctrl.clear_data,
                            loading=("data_loading", False),
                            disabled=("data_loading", False),
                            block=True,
                            small=True,
                        )
                    
                        
                with html.Div(v_if="!drawer_mini && data_loaded"):
                    vuetify.VDivider()
                    with vuetify.VListItem(class_="nav-item nav-item--nested"):
                        with vuetify.VListItemContent(class_="mt-2 mb-0 pb-0"):
                            vuetify.VTextField(
                                v_model=("analysis_dir", ""),
                                label="Analysis results path",
                                placeholder="path to results dir (colocalization.zarr + tally)",
                                prepend_icon="mdi-chart-box-outline",
                                dense=True,
                                clearable=True,
                                hide_details=True,
                                disabled=("analysis_loading", False),
                            )
                    with vuetify.VListItem(
                        class_="nav-item nav-item--nested",
                        v_if="!analysis_loaded",
                    ):
                        with vuetify.VListItemContent(class_="mt-2 pt-0"):
                            vuetify.VBtn(
                                "Load Analysis",
                                click=ctrl.load_analysis_path,
                                loading=("analysis_loading", False),
                                disabled=("analysis_loading || !analysis_dir",),
                                block=True,
                                small=True,
                            )
                    with vuetify.VListItem(
                        class_="nav-item nav-item--nested",
                        v_if="analysis_loaded",
                    ):
                        with vuetify.VListItemContent(class_="mt-2 pt-0"):
                            vuetify.VBtn(
                                "Clear Analysis",
                                click="trigger('clear_analysis')",
                                block=True,
                                small=True,
                            )
                    with vuetify.VListItem(class_="nav-item nav-item--nested"):
                        with vuetify.VListItemContent():
                            # Show loaded status
                            with html.Div(v_if="analysis_loaded", class_="text-caption success--text"):
                                vuetify.VIcon("mdi-check-circle-outline", x_small=True, class_="mr-1")
                                html.Span("{{ analysis_file_name }}",classes="text-truncate text-center", style="font-size: 10px; color: #9e9e9e;")
                            
                            with vuetify.VCard(
                                    classes="text-center pa-1",
                                v_if="!analysis_loaded",
                            ):
                                html.A(
                                    "Instructions for running the BioSET analysis pipeline.",
                                    href="https://github.com/nyu-vis-krueger-group/BioSET_Preprocessing/tree/master",
                                    target="_blank",
                                    style="font-size: 10px; color: #9e9e9e;",
                                )