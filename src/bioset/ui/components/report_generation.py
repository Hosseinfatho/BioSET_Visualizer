from __future__ import annotations

from trame.widgets import html, vuetify


def report_generation_section(state, ctrl):
    """Create the report generation section."""

    def toggle_report_generation():
        state.report_generation_open = not state.report_generation_open

    with vuetify.VList(dense=True, nav=True):
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
                        vuetify.VIcon("mdi-tune", style="font-size: 30px;", v_bind="attrs", v_on="on")
                    html.Span("Report Generation")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                vuetify.VListItemTitle("Report Generation", classes="text-overline")

        with vuetify.VExpandTransition():
            with html.Div(
                    v_show=("report_generation_open", False),
                    classes="mt-1",
            ):
                # Action List Header
                with html.Div(classes="d-flex align-center justify-center flex-nowrap mb-2 mt-2"):
                    html.Span("To be filled.")

                # Generate Header
                with html.Div(classes="d-flex align-center justify-center flex-nowrap mb-2 mt-2"):
                    vuetify.VBtn(
                        "Generate Report",
                        small=True,
                        outlined=True,
                        classes="ml-1 px-2 text-none",
                        click=ctrl.generate_pdf_report,
                    )
