# channels.py
"""Channels section component."""

from __future__ import annotations

from trame.widgets import html, vuetify


def channels_section(state, ctrl):
    """Create the channels section with dynamic channel list."""
    
    def toggle_channels():
        state.channels_open = not state.channels_open
    
    with vuetify.VList(dense=True, nav=True):
        with vuetify.VListItem(
            class_=("channels_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"),
            link=True,
            ripple=True,
            click=toggle_channels,
        ):
            with vuetify.VListItemIcon():
                vuetify.VIcon("mdi-layers-triple-outline", style="font-size: 30px;")
            with vuetify.VListItemContent(v_if="!drawer_mini"):
                vuetify.VListItemTitle("Channels", classes="text-overline")
                # Show count badge
                vuetify.VChip(
                    v_if="channels.length > 0",
                    x_small=True,
                    class_="ml-2",
                    v_text="channels.length",
                )
        
        with vuetify.VExpandTransition():
            with html.Div(v_show=("channels_open", False), class_="px-2 pb-2"):
                # Message when no data loaded
                with html.Div(
                    v_if="!data_loaded",
                    class_="text-center pa-4 text--secondary",
                    style="font-size: 12px;",
                ):
                    html.Span("Load data to see channels")
                
                # Dynamic channel list
                with html.Div(v_if="data_loaded"):
                    # Use v-for for dynamic rendering
                    with vuetify.VListItem(
                        v_for="channel in channels",
                        key="channel.id",
                        dense=True,
                        class_="px-2 py-1 mb-1",
                    ):
                        # Channel chip with color indicator
                        with html.Div(class_="mr-2 channel-chip"):
                            with vuetify.VListItemAvatar(
                                tile=True,
                                size=40,
                                class_="ma-0 channel-avatar",
                                click=(ctrl.toggle_channel, "[channel.id]"),
                                style=("`background-color: ${channel.color} !important; opacity: ${active_channels.includes(channel.id) ? 1 : 0.3};`",),
                            ):
                                # Show checkmark if active
                                vuetify.VIcon(
                                    v_if="active_channels.includes(channel.id)",
                                    small=True,
                                    style="color: black;",
                                    v_text="'mdi-check'",
                                )
                        
                        with vuetify.VListItemContent(class_="py-0", v_if="!drawer_mini"):
                            html.Div(
                                v_text="channel.name",
                                class_="text-truncate",
                                style="line-height: 1.3; font-size: 12px;",
                            )
                            # Show "active" indicator
                            html.Div(
                                v_if="active_channels.includes(channel.id)",
                                class_="text--secondary",
                                style="font-size: 10px;",
                                v_text="'Rendering'",
                            )