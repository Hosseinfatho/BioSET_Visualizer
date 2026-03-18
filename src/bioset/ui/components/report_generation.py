from __future__ import annotations

from trame.widgets import html, vuetify


def report_generation_section(state, ctrl):
    """Create the report generation section."""

    def toggle_report_generation():
        state.report_generation_open = not state.report_generation_open

    with vuetify.VList(dense=True, nav=True, v_if="data_loaded"):
        with vuetify.VListItem(
                class_=("report_generation_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
                classes="mb-0",
                link=True,
                ripple=True,
                click=toggle_report_generation,
        ):
            with vuetify.VListItemIcon():
                with vuetify.VTooltip(right=True, disabled=("!drawer_mini",)):
                    with html.Template(v_slot_activator="{ on, attrs }"):
                        vuetify.VIcon("mdi-file-pdf-box", style="font-size: 30px;", v_bind="attrs", v_on="on")
                    html.Span("Report Generation")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                vuetify.VListItemTitle("Report Generation", classes="text-overline")

        with vuetify.VExpandTransition():
            with html.Div(
                    v_show=("report_generation_open", False),
                    classes="mt-1",
            ):
                with html.Div(classes="mb-2 px-2"):
                    vuetify.VCheckbox(
                        label="General Information",
                        v_model="export_general",
                        dense=True,
                        hide_details=True,
                    )
                    vuetify.VCheckbox(
                        label="Analysis Dataset",
                        v_model="export_analysis",
                        dense=True,
                        hide_details=True,
                    )
                    vuetify.VCheckbox(
                        label="Chat History",
                        v_model="export_chat",
                        dense=True,
                        hide_details=True,
                    )

                    vuetify.VCheckbox(
                        label="Bookmarks",
                        v_model="export_bookmarks",
                        dense=True,
                        hide_details=True,
                    )

                with html.Div(classes="mb-2 mt-4"):
                    vuetify.VBtn(
                        "Generate Report",
                        block=True,
                        small=True,
                        classes="ml-1 px-2",
                        click=ctrl.generate_pdf_report,
                    )
