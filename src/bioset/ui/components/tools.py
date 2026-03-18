from __future__ import annotations

from trame.widgets import html, vuetify


def report_generation_section(state, ctrl):
    """Create the Tools section with Optimal View, Bookmark, and Report Generation."""

    def toggle_tools():
        state.tools_open = not state.tools_open

    with vuetify.VList(dense=True, nav=True):
        with vuetify.VListItem(
                class_=("tools_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
                classes="mb-0",
                link=True,
                ripple=True,
                click=toggle_tools,
        ):
            with vuetify.VListItemIcon():
                with vuetify.VTooltip(right=True, disabled=("!drawer_mini",)):
                    with html.Template(v_slot_activator="{ on, attrs }"):
                        vuetify.VIcon("mdi-tools", style="font-size: 30px;", v_bind="attrs", v_on="on")
                    html.Span("Tools")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                vuetify.VListItemTitle("Tools", classes="text-overline")

        with vuetify.VExpandTransition():
            with html.Div(
                    v_show=("tools_open", False),
                    classes="mt-1",
            ):
                # NOV: press to show box and panel
                with vuetify.VListItem(class_="nav-item nav-item--nested", style="overflow: visible;"):
                    with vuetify.VListItemIcon():
                        with vuetify.VTooltip(right=True):
                            with html.Template(v_slot_activator="{ on, attrs }"):
                                vuetify.VIcon("mdi-camera-enhance", style="font-size: 25px;", v_bind="attrs", v_on="on")
                            html.Span("Next Best View (popup)")
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        html.Span("Optimal View", style="cursor: pointer; flex-shrink: 0;",
                                  click=ctrl.nov_toggle)

                # Bookmark (saved views / snapshots)
                with vuetify.VListItem(
                        class_=("bookmark_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
                        link=True,
                        ripple=True,
                        click="bookmark_open = !bookmark_open",
                ):
                    with vuetify.VListItemIcon():
                        with vuetify.VTooltip(right=True):
                            with html.Template(v_slot_activator="{ on, attrs }"):
                                vuetify.VIcon("mdi-bookmark", style="font-size: 25px;", v_bind="attrs", v_on="on")
                            html.Span("Bookmark")
                    with vuetify.VListItemContent(v_if="!drawer_mini"):
                        html.Span("Bookmark")

                # Report Generation sub-header
                with vuetify.VListItem(classes="mt-4 text-left ml-4"):
                    html.Span("Report Generation", classes="text-caption grey--text font-weight-bold")

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

                with html.Div(classes="mb-2 mt-4"):
                    vuetify.VBtn(
                        "Generate Report",
                        block=True,
                        small=True,
                        classes="ml-1 px-2",
                        click=ctrl.generate_pdf_report,
                    )
