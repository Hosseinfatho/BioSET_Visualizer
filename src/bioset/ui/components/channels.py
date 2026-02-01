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
                vuetify.VIcon("mdi-layers-triple-outline", style="font-size: 40px;")
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
                            v_for="(channel, index) in channels",
                            key="channel.id",
                            value=("channel.id",),  
                            link=True,
                            dense=True,
                            class_="ch-item",
                        ):
                            with vuetify.VListItemIcon():
                                with html.Div(
                                    class_="ch-icons",
                                    title=("channel.name",),
                                ):
                                    with vuetify.VMenu(
                                        offset_y=True,
                                        close_on_content_click=False,
                                    ):
                                        with html.Template(v_slot_activator="{ on, attrs }"):
                                            vuetify.VIcon(
                                                v_text="active_channels.includes(channel.id) ? 'mdi-square-rounded' : 'mdi-square-rounded-outline'",
                                                style=("`color: ${channel.color || '#fff'}; cursor: pointer;`",),
                                                v_bind="attrs",
                                                v_on="on",
                                                _class="mr-3",
                                                click_stop=True,
                                            )
                                        
                                        with vuetify.VCard():
                                            vuetify.VColorPicker(
                                                v_model=("channels[index].color",),
                                                mode="hexa",
                                                hide_mode_switch=True,
                                                hide_inputs=True,
                                                show_swatches=True,
                                                swatches_max_height=150,
                                                swatches=("color_swatches", []),
                                                input=(ctrl.on_channel_color_change, "[channel.id, $event]"),
                                            )

                                    vuetify.VIcon(
                                        "mdi-close-circle",
                                        v_if="active_channels.includes(channel.id)",
                                        class_="eye-icon mr-3",
                                        click_stop_prevent=(ctrl.toggle_channel, "[channel.id]"),
                                    )

                            with vuetify.VListItemContent(v_if="!drawer_mini"):
                                vuetify.VListItemTitle("{{ channel.name }}")

                                vuetify.VRangeSlider(
                                    class_="ch-slider",
                                    dense=True,
                                    hide_details=True,
                                    thumb_label="never",
                                    min=0,
                                    max=100,
                                    value=("channel.range", [0, 100]),
                                    click_stop=True,
                                    mousedown_stop=True,
                                    style="max-width: 150px;",
                                )