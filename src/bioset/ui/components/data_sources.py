# data_sources.py
"""Data sources section component."""

from __future__ import annotations

from trame.widgets import html, vuetify


def data_sources_section(state, ctrl):
    """Create the data sources section with URL inputs and Load button."""
    
    def toggle_data():
        state.data_open = not state.data_open
    
    with vuetify.VList(dense=True, nav=True):
        with vuetify.VListItem(
            class_=("data_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
            link=True,
            ripple=True,
            click=toggle_data,
        ):
            with vuetify.VListItemIcon():
                vuetify.VIcon("mdi-database-outline", style="font-size: 40px;")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                vuetify.VListItemTitle("Data Sources", classes="text-overline")
        
        with vuetify.VExpandTransition():
            with html.Div(v_show=("data_open", False)):
                with vuetify.VListItem(class_="nav-item nav-item--nested"):
                    with vuetify.VListItemIcon(v_if="drawer_mini", title=("zarr_url",)):
                        vuetify.VIcon("mdi-link-variant", style="font-size: 25px;")
                    with vuetify.VListItemContent(v_if="!drawer_mini", class_="mb-0 pb-0"):
                        vuetify.VTextField(
                            v_model=("zarr_url", ""),
                            label="Zarr URL",
                            dense=True,
                            clearable=True,
                            hide_details=True,
                        )
                        
                with vuetify.VListItem(class_="nav-item nav-item--nested"):
                    with vuetify.VListItemIcon(v_if="drawer_mini", title=("metadata_url",)):
                        vuetify.VIcon("mdi-file-document-outline", style="font-size: 25px;")
                    with vuetify.VListItemContent(v_if="!drawer_mini", class_="mt-0 pt-0 mb-0 pb-0"):
                        vuetify.VTextField(
                            v_model=("metadata_url", ""),
                            label="Metadata URL",
                            dense=True,
                            clearable=True,
                            hide_details=True,
                        )
                
                with vuetify.VListItem(class_="nav-item nav-item--nested", v_if="!drawer_mini"):
                    with vuetify.VListItemContent(class_="mt-2 pt-0"):
                        vuetify.VBtn(
                            "Load Data",
                            click=ctrl.load_data,
                            loading=("data_loading", False),
                            disabled=("data_loading", False),
                            block=True,
                            small=True,
                        )