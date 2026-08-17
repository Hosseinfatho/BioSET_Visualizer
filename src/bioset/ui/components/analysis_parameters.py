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
                # Dilation Header: label + current radius + exact/computed chip.
                # Radii at the preprocessed detents are answered exactly from the
                # tally; anything else is computed from the EDT field.
                with vuetify.VListItem(classes="mt-2 text-left ml-4"):
                    html.Span("Dilation radius", classes="text-caption grey--text font-weight-bold")
                    html.Span(
                        "{{ Number(current_dilation).toFixed(2) }} \u03BCm",
                        classes="text-caption ml-2",
                        style="color: white;",
                    )
                    vuetify.VChip(
                        v_text="analysis_dilation_amounts.some(d => Math.abs(d - current_dilation) < 0.001) ? 'exact' : 'computed'",
                        x_small=True,
                        classes="ml-2",
                        color=("analysis_dilation_amounts.some(d => Math.abs(d - current_dilation) < 0.001) ? 'green darken-3' : 'amber darken-4'",),
                        text_color="white",
                    )

                with vuetify.VListItem(class_="nav-item nav-item--nested"):
                    with vuetify.VListItemContent(classes="pb-0"):
                        # Continuous radius slider with magnetic detents.
                        # The thumb tracks `radius_slider` client-side while
                        # dragging; the committed value lands in
                        # `current_dilation` on release, where the server snaps
                        # it to a nearby detent (see state.on_dilation_change).
                        with html.Div(classes="d-flex align-center justify-center flex-nowrap mb-0 mt-2"):
                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    click="(function(){var a = analysis_dilation_amounts.filter(function(d){return d < current_dilation - 1e-6}); var v = a.length ? a[a.length-1] : (analysis_dilation_amounts[0] || 0); radius_slider = v; current_dilation = v;})()",
                            ):
                                vuetify.VIcon("mdi-minus", small=True)

                            with html.Div(style="position: relative; flex: 1 1 auto;", classes="mx-2"):
                                vuetify.VSlider(
                                    v_model=("radius_slider", 0.0),
                                    min=0,
                                    max=("analysis_radius_max",),
                                    step=0.01,
                                    dense=True,
                                    dark=True,
                                    hide_details=True,
                                    thumb_label=True,
                                    small=True,
                                    style="font-size: 0.75rem;",
                                    __events=["change"],
                                    change="current_dilation = radius_slider",
                                )
                                # Detent ticks overlaid on the track
                                html.Div(
                                    v_for="d in analysis_dilation_amounts",
                                    key=("d",),
                                    style=(
                                        "'position: absolute; top: 12px; width: 2px; height: 10px; "
                                        "pointer-events: none; transform: translateX(-50%); "
                                        "left: ' + (d / analysis_radius_max * 100) + '%; "
                                        "background: ' + (Math.abs(d - current_dilation) < 0.001 ? '#4caf50' : '#9e9e9e')",
                                    ),
                                )
                                # Label with the EFFECTIVE radius (what the tally
                                # rows actually describe), positioned at the
                                # requested one (what we query with). Every other
                                # label is drawn so 8 detents stay legible.
                                html.Span(
                                    v_for="(d, di) in analysis_dilation_amounts",
                                    key=("'l' + d",),
                                    v_if="di % 2 === 0",
                                    v_text="(analysis_dilation_labels[di] !== undefined "
                                           "? analysis_dilation_labels[di] : d)",
                                    style=(
                                        "'position: absolute; top: 22px; font-size: 9px; "
                                        "pointer-events: none; transform: translateX(-50%); "
                                        "left: ' + (d / analysis_radius_max * 100) + '%; "
                                        "color: ' + (Math.abs(d - current_dilation) < 0.001 ? '#4caf50' : '#9e9e9e')",
                                    ),
                                )

                            with vuetify.VBtn(
                                    icon=True,
                                    small=True,
                                    click="(function(){var a = analysis_dilation_amounts.filter(function(d){return d > current_dilation + 1e-6}); if (a.length) { radius_slider = a[0]; current_dilation = a[0]; }})()",
                            ):
                                vuetify.VIcon("mdi-plus", small=True)
                # Spacer so the detent labels below the track stay visible
                with vuetify.VListItem(classes="mt-0 pt-0", style="min-height: 14px;"):
                    html.Span("")

                # Heatmap Header
                with vuetify.VListItem(classes="mt-4 text-left ml-4"):
                    html.Span("Heatmap", classes="text-caption grey--text font-weight-bold")

                with vuetify.VListItem(class_="nav-item nav-item--nested", v_if="!drawer_mini"):
                    with vuetify.VListItemContent():
                        # Eye toggle + Grid/Integrated + Manual/Auto in one row
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

                            # Grid / Integrated (shader) toggle. The solid-cube
                            # "filled" mode was removed.
                            with vuetify.VBtnToggle(
                                    v_model=("heatmap_mode", "grid"),
                                    mandatory=True,
                                    dense=True,
                                    style="background: transparent;",
                            ):
                                vuetify.VBtn("Grid", value="grid", small=True, classes="text-capitalize", outlined=True)
                                vuetify.VBtn("Integrated", value="integrated", small=True, classes="text-capitalize", outlined=True)

                            # Manual / Auto toggle
                            with vuetify.VBtnToggle(
                                    v_model=("heatmap_auto_level", "auto"),
                                    mandatory=True,
                                    dense=True,
                                    style="background: transparent;",
                            ):
                                vuetify.VBtn("Manual", value="manual", small=True, classes="text-capitalize", outlined=True)
                                vuetify.VBtn("Auto", value="auto", small=True, classes="text-capitalize", outlined=True)

                        # Integrated-mode effect toggles (gain / importance
                        # sampling / contour outline), each on by default. Tuning
                        # lives in config.IntegratedHeatmapConfig.
                        with html.Div(
                                classes="d-flex align-center justify-center mb-2",
                                style="gap: 12px;",
                                v_if="heatmap_mode === 'integrated'",
                        ):
                            vuetify.VSwitch(
                                v_model=("ihm_gain_enabled", True),
                                label="Gain",
                                dense=True,
                                hide_details=True,
                                classes="mt-0 pt-0",
                            )
                            vuetify.VSwitch(
                                v_model=("ihm_sampling_enabled", True),
                                label="Sampling",
                                dense=True,
                                hide_details=True,
                                classes="mt-0 pt-0",
                            )
                            vuetify.VSwitch(
                                v_model=("ihm_outline_enabled", True),
                                label="Outline",
                                dense=True,
                                hide_details=True,
                                classes="mt-0 pt-0",
                            )

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
                                                    <span v-if="combo.iou !== null" class="text-caption grey--text ml-1">
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
