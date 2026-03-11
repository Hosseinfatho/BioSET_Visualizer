from __future__ import annotations

from trame.widgets import html, vuetify


def channels_section(state, ctrl):
    """Create the channels section with dynamic channel list."""

    def toggle_channels():
        state.channels_open = not state.channels_open

    with vuetify.VList(dense=True, nav=True, v_if="data_loaded"):
        with vuetify.VListItem(
            class_=("channels_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
            link=True,
            ripple=True,
            click=toggle_channels,
        ):
            with vuetify.VListItemIcon():
                with vuetify.VTooltip(right=True, disabled=("!drawer_mini",)):
                    with html.Template(v_slot_activator="{ on, attrs }"):
                        vuetify.VIcon("mdi-layers-triple-outline", style="font-size: 40px;", v_bind="attrs", v_on="on")
                    html.Span("Channels")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                with vuetify.VListItemTitle(classes="d-flex align-center"):
                    html.Span("Channels", classes="text-overline mr-2")
                    vuetify.VChip(
                        v_if="channels.length > 0",
                        x_small=True,
                        v_text="channels.length",
                    )

        with vuetify.VExpandTransition():
            with html.Div(v_show=("channels_open", False), class_="px-2 pb-2"):
                with vuetify.VList(dense=True):
                    with vuetify.VListItemGroup(
                        multiple=True,
                        v_model=("active_channels", []),
                    ):
                        with vuetify.VListItem(
                            v_for="channel in channels.filter(ch => visible_channel_ids.includes(ch.id))",
                            key="channel.id",
                            value=("channel.id",),  
                            link=True,
                            dense=True,
                            class_="ch-item",
                        ):
                            with vuetify.VListItemIcon():
                                with vuetify.VTooltip(right=True, disabled=("!drawer_mini",)):
                                    with html.Template(v_slot_activator="{ on, attrs }"):
                                        with html.Div(
                                            class_="ch-icons",
                                            v_bind="attrs",
                                            v_on="on",
                                        ):
                                            with vuetify.VMenu(
                                                offset_y=True,
                                                close_on_content_click=False,
                                            ):
                                                with html.Template(v_slot_activator="{ on: menuOn, attrs: menuAttrs }"):
                                                    vuetify.VIcon(
                                                        v_text="active_channels.includes(channel.id) ? 'mdi-square-rounded' : 'mdi-square-rounded-outline'",
                                                        style=("`color: ${channel.color || '#fff'}; cursor: pointer;`",),
                                                        v_bind="menuAttrs",
                                                        v_on="menuOn",
                                                        _class="mr-3",
                                                        click_stop=True,
                                                    )
                                                    
                                                with vuetify.VCard(class_="pa-2", dark=True):
                                                    vuetify.VColorPicker(
                                                        value=("channel.color",),
                                                        mode="rgba",
                                                        hide_canvas=True,
                                                        hide_inputs=False,
                                                        hide_mode_switch=False,
                                                        show_swatches=False, 
                                                        flat=True,
                                                        width="280",
                                                        dark=True,
                                                        __events=["input", "update:color"],
                                                        input=(ctrl.on_channel_color_change, "[channel.id, $event]"),
                                                        update_color=(ctrl.on_channel_color_change, "[channel.id, $event]"),
                                                    )
                                                    
                                                    vuetify.VDivider(classes="my-2")
                                                    
                                                    with html.Div(
                                                        v_for="(row, rowIndex) in color_swatches",
                                                        key="'row-' + rowIndex",
                                                        classes="d-flex justify-center mb-1",
                                                        style="gap: 5px;",
                                                    ):
                                                        vuetify.VBtn(
                                                            v_for="(swatch_color, colIndex) in row",
                                                            key="'swatch-' + rowIndex + '-' + colIndex",
                                                            style=("`background-color: ${swatch_color} !important; border: 2px solid ${channel.color === swatch_color ? '#FFFFFF' : (swatch_color === '#000000' ? '#555' : '#333')}; min-width: 72px; width: 72px; height: 32px; border-radius: 4px;`",),
                                                            click=(ctrl.on_channel_color_change, "[channel.id, swatch_color]"),
                                                        )
 

                                            with html.Span(
                                                    v_on="{'mousedown': (e) => e.stopPropagation(), 'click': (e) => e.stopPropagation()}",
                                                ):
                                                    vuetify.VIcon(
                                                        "mdi-close-circle",
                                                        class_="eye-icon mr-3",
                                                        click=(ctrl.remove_channel_from_visible, "[channel.id]"),
                                                    )
                                            

                                    html.Span("{{ channel.name }}")

                            with vuetify.VListItemContent(v_if="!drawer_mini"):
                                vuetify.VListItemTitle("{{ channel.name }}")
                                with html.Span(
                                        v_on="{'mousedown': (e) => e.stopPropagation(), 'click': (e) => e.stopPropagation()}",
                                    ):
                                    vuetify.VRangeSlider(
                                        class_="ch-slider",
                                        dense=True,
                                        hide_details=True,
                                        min=0,
                                        max=100,
                                        value=("channel.range", [0, 100]),
                                        click_stop=True,
                                        mousedown_stop=True,
                                        style="max-width: 150px;",
                                        __events=["end"],  
                                        end=(ctrl.on_channel_range_change, "[channel.id, $event]"),
                                    )

                with vuetify.VListItem(
                    v_if="channels.length > visible_channel_ids.length && drawer_mini",
                    class_="nav-item nav-item--nested",
                ):
                    with vuetify.VMenu(
                        offset_y=True,
                        max_height=300,
                        dark=True,
                            close_on_content_click=False,
                    ):
                        with html.Template(v_slot_activator="{ on: menuOn, attrs: menuAttrs }"):
                            with vuetify.VListItemIcon():
                                with vuetify.VTooltip(right=True):
                                    with html.Template(v_slot_activator="{ on, attrs }"):
                                        vuetify.VIcon(
                                            "mdi-plus-circle-outline",
                                            style="font-size: 25px;",
                                            v_bind="{ ...attrs, ...menuAttrs }",
                                            v_on="{ ...on, ...menuOn }",
                                            link=True,
                                        )
                                    html.Span("Add Channel")

                        with html.Div():
                            vuetify.VTextField(
                                v_model=("add_channel_search", ""),
                                placeholder="Search channels...",
                                prepend_inner_icon="mdi-magnify",
                                dense=True,
                                hide_details=True,
                                clearable=True,
                                solo=True,
                                flat=True,
                                class_="mx-2 mt-2 mb-1",
                            )
                            with vuetify.VList(dense=True):
                                with vuetify.VListItem(
                                        v_for=(
                                                "channel in channels"
                                                # filter duplicates
                                                ".filter((ch, i, arr) => arr.findIndex(c => c.name === ch.name) === i)"
                                                # filter visible channels
                                                ".filter(ch => !channels.some(c => visible_channel_ids.includes(c.id) && c.name === ch.name))"
                                                # filter search
                                                ".filter(ch => !add_channel_search || ch.name.toLowerCase().includes(add_channel_search.toLowerCase()))"
                                        ),
                                        key="'add-' + channel.id",
                                        click=(ctrl.add_channel_to_visible, "[channel.id]"),
                                ):
                                    with vuetify.VListItemContent():
                                        vuetify.VListItemTitle("{{ channel.name }}")

                with vuetify.VListItem(
                    v_if="channels.length > visible_channel_ids.length && !drawer_mini",
                    class_="nav-item nav-item--nested",
                ):
                    with vuetify.VMenu(
                        offset_y=True,
                        max_height=300,
                        dark=True,
                            close_on_content_click=False,
                    ):
                        with html.Template(v_slot_activator="{ on: menuOn, attrs: menuAttrs }"):
                            with vuetify.VListItemContent(class_="mt-2 pt-0"):
                                vuetify.VBtn(
                                    "Add Channel",
                                    v_bind="menuAttrs",
                                    v_on="menuOn",
                                    block=True,
                                    small=True,
                                )

                        with html.Div():
                            vuetify.VTextField(
                                v_model=("add_channel_search", ""),
                                placeholder="Search channels...",
                                prepend_inner_icon="mdi-magnify",
                                dense=True,
                                hide_details=True,
                                clearable=True,
                                solo=True,
                                flat=True,
                                class_="mx-2 mt-2 mb-1",
                            )
                            with vuetify.VList(dense=True):
                                with vuetify.VListItem(
                                        v_for=(
                                                "channel in channels"
                                                # filter duplicates
                                                ".filter((ch, i, arr) => arr.findIndex(c => c.name === ch.name) === i)"
                                                # filter visible channels
                                                ".filter(ch => !channels.some(c => visible_channel_ids.includes(c.id) && c.name === ch.name))"
                                                # filter search
                                                ".filter(ch => !add_channel_search || ch.name.toLowerCase().includes(add_channel_search.toLowerCase()))"
                                        ),
                                        key="'add-' + channel.id",
                                        click=(ctrl.add_channel_to_visible, "[channel.id]"),
                                ):
                                    with vuetify.VListItemContent():
                                        vuetify.VListItemTitle("{{ channel.name }}")
