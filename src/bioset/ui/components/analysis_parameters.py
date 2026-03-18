from __future__ import annotations

from trame.widgets import html, vuetify


def analysis_parameters_section(state, ctrl):
    """Create the analysis parameters section with Dilation and Heatmap."""

    def toggle_analysis():
        state.analysis_params_open = not state.analysis_params_open

    with vuetify.VList(dense=True, nav=True, v_if="analysis_loaded"):
        with vuetify.VListItem(
                class_=("analysis_params_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
                classes="mb-0",
                link=True,
                ripple=True,
                click=toggle_analysis,
        ):
            with vuetify.VListItemIcon():
                with vuetify.VTooltip(right=True, disabled=("!drawer_mini",)):
                    with html.Template(v_slot_activator="{ on, attrs }"):
                        vuetify.VIcon("mdi-tune", style="font-size: 30px;", v_bind="attrs", v_on="on")
                    html.Span("Analysis Parameters")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                vuetify.VListItemTitle("Analysis Parameters", classes="text-overline")

        with vuetify.VExpandTransition():
            with html.Div(
                    v_show=("analysis_params_open", False),
                    classes="mt-1",
            ):
                # Dilation Header
                with vuetify.VListItem(classes="mt-2 text-left ml-4"):
                    html.Span("Dilation", classes="text-caption grey--text font-weight-bold")

                with vuetify.VListItem(class_="nav-item nav-item--nested"):
                    with vuetify.VListItemContent(classes="pb-0"):
                        # Dilation slider 
                        with html.Div(classes="d-flex align-center justify-center flex-nowrap mb-2 mt-2"):
                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    click="current_dilation = Math.max(analysis_dilation_amounts[0] || 0, current_dilation - (analysis_dilation_amounts.length > 1 ? analysis_dilation_amounts[1] - analysis_dilation_amounts[0] : 1))",
                            ):
                                vuetify.VIcon("mdi-minus", small=True)

                            vuetify.VSlider(
                                v_model=("current_dilation",),
                                min=("analysis_dilation_amounts.length > 0 ? analysis_dilation_amounts[0] : 0",),
                                max=(
                                    "analysis_dilation_amounts.length > 0 ? analysis_dilation_amounts[analysis_dilation_amounts.length - 1] : 0",),
                                step=(
                                    "analysis_dilation_amounts.length > 1 ? analysis_dilation_amounts[1] - analysis_dilation_amounts[0] : 1",),
                                ticks="always",
                                dense=True,
                                dark=True,
                                hide_details=False,
                                classes="mx-2",
                                small=True,
                                style="font-size: 0.75rem;",
                                tick_labels=("analysis_dilation_amounts.map(v => v + ' \u03BCm')",),
                            )

                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    click="current_dilation = Math.min(analysis_dilation_amounts[analysis_dilation_amounts.length - 1] || 0, current_dilation + (analysis_dilation_amounts.length > 1 ? analysis_dilation_amounts[1] - analysis_dilation_amounts[0] : 1))",
                            ):
                                vuetify.VIcon("mdi-plus", small=True)

                # Heatmap Header
                with vuetify.VListItem(classes="mt-4 text-left ml-4"):
                    html.Span("Heatmap", classes="text-caption grey--text font-weight-bold")

                with vuetify.VListItem(class_="nav-item nav-item--nested", v_if="!drawer_mini"):
                    with vuetify.VListItemContent():
                        # Eye toggle + Filled/Outline + Manual/Auto in one row
                        with html.Div(classes="d-flex align-center justify-center mb-2", style="gap: 8px;"):
                            # Eye visibility toggle (square icon button)
                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    outlined=True,
                                    click="heatmap_visible = !heatmap_visible",
                                    style="min-width: 32px; width: 32px; height: 32px;",
                            ):
                                vuetify.VIcon(
                                    v_text="heatmap_visible ? 'mdi-eye' : 'mdi-eye-off'",
                                    small=True,
                                    style=("heatmap_visible ? 'color:white' : 'color:#555'",),
                                )

                            # Filled / Outline toggle
                            with vuetify.VBtnToggle(
                                    v_model=("heatmap_outline_only", "filled"),
                                    mandatory=True,
                                    dense=True,
                                    style="background: transparent;",
                            ):
                                vuetify.VBtn("Filled", value="filled", small=True, classes="text-capitalize", outlined=True)
                                vuetify.VBtn("Outline", value="outline", small=True, classes="text-capitalize", outlined=True)

                            # Manual / Auto toggle
                            with vuetify.VBtnToggle(
                                    v_model=("heatmap_auto_level", "auto"),
                                    mandatory=True,
                                    dense=True,
                                    style="background: transparent;",
                            ):
                                vuetify.VBtn("Manual", value="manual", small=True, classes="text-capitalize", outlined=True)
                                vuetify.VBtn("Auto", value="auto", small=True, classes="text-capitalize", outlined=True)

                        # Combination dropdown
                        with html.Div(classes="d-flex justify-center mb-3"):
                            with vuetify.VMenu(
                                    offset_y=True,
                                    v_if="heatmap_available_combinations",
                            ):
                                with vuetify.Template(v_slot_activator="{ on, attrs }"):
                                    with vuetify.VBtn(
                                            v_bind="attrs",
                                            v_on="on",
                                            small=True,
                                            dark=True,
                                            classes="text-none",
                                    ):
                                        # Show selected combo as chips inside the button
                                        vuetify.Template(
                                            """
                                            <span v-if="!heatmap_combination || heatmap_combination.length === 0" style="color: #888;">
                                                No selection
                                            </span>
                                            <span v-else class="d-flex flex-wrap align-center" style="gap: 3px;">
                                                <v-chip
                                                    v-for="(ch, ci) in heatmap_combination"
                                                    :key="ci"
                                                    x-small
                                                    :style="(function(){var c=channels.find(function(x){return x.name===ch});var clr=c?c.color:'#888';return 'background:'+clr+';border:1px solid '+clr+';color:#000'})()"
                                                >{{ ch }}</v-chip>
                                            </span>
                                            <v-icon small class="ml-1">mdi-chevron-down</v-icon>
                                            """
                                        )

                                with vuetify.VList(dense=True, dark=True, classes="grey darken-4"):
                                    with vuetify.VListItem(
                                            v_for="(combo, idx) in heatmap_available_combinations",
                                            key=("idx",),
                                            dense=True,
                                            click="heatmap_combination = combo.channels",
                                            style="min-height: 32px;",
                                    ):
                                        with vuetify.VListItemContent():
                                            vuetify.Template(
                                                """
                                                <div class="d-flex flex-wrap align-center" style="gap: 3px;">
                                                    <v-chip
                                                        v-for="(ch, ci) in combo.channels"
                                                        :key="ci"
                                                        x-small
                                                        :style="(function(){var c=channels.find(function(x){return x.name===ch});var clr=c?c.color:'#888';var sel=JSON.stringify(heatmap_combination)===JSON.stringify(combo.channels);return 'background:'+(sel?clr:'transparent')+';border:1px solid '+clr+';color:'+(sel?'#000':clr)})()"
                                                    >{{ ch }}</v-chip>
                                                    <span v-if="combo.iou !== null" style="color: #aaa; font-size: 10px; margin-left: 4px;">
                                                        IoU: {{ combo.iou.toFixed(4) }}
                                                    </span>
                                                </div>
                                                """
                                            )

                        # Granularity toggle
                        with html.Div(classes="d-flex justify-center mb-1"):
                            with vuetify.VBtnToggle(
                                    v_model=("current_hierarchy_level",),
                                    mandatory=True,
                                    dense=True,
                                    disabled=("heatmap_auto_level === 'auto'",),
                            ):
                                vuetify.VBtn(
                                    v_for="level in analysis_hierarchy_levels",
                                    key="level",
                                    value=("level",),
                                    small=True,
                                    v_text="level === 0 ? 'Fine' : (level === 1 ? 'Medium' : (level === 2 ? 'Coarse' : 'Overview'))",
                                )
