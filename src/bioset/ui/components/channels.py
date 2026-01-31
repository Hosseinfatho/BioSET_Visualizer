# channels.py
"""Channels section component."""

from __future__ import annotations

from trame.widgets import html, vuetify


def channels_section(state, ctrl):
    """Create the channels section with channel list."""
    
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
        
        with vuetify.VExpandTransition():
            with html.Div(v_show=("channels_open", False), class_="px-2 pb-2"):
                with vuetify.VListItemGroup(multiple=True):
                    # Use legacy channel state for now
                    # TODO: Migrate to dynamic state.channels list
                    for ch in state.channels:
                        _channel_item(state, ch["id"], ch["name"])


def _channel_item(state, idx, name):
    """Create a single channel item with color picker and range slider."""
    
    color_key = f"ch{idx}_color"
    dialog_key = f"ch{idx}_color_dialog"

    with vuetify.VListItem(dense=True, class_="px-2 py-1 mb-1"):
        
        with html.Div(class_="mr-2 channel-chip"):
            
            # Mini drawer view
            with html.Div(v_if="drawer_mini"):
                with vuetify.VBadge(
                    bordered=True,
                    overlap=True,
                    link=True,
                    color="rgba(0,0,0,0.75)",
                    icon="mdi-close",
                    offset_x="25",
                    offset_y="20",
                ):
                    with vuetify.VListItemAvatar(
                        tile=True,
                        size=40,
                        class_="ma-0 channel-avatar",
                        click=f"{dialog_key} = true",
                        style=(f"`background-color: ${{{color_key}}} !important;`",),
                    ):
                        html.Span(
                            f"{name[:2]}..",
                            class_="text-truncate",
                            style="color:black;",
                        )

            # Full drawer view
            with html.Div(v_else=True):
                with vuetify.VListItemAvatar(
                    tile=True,
                    size=40,
                    class_="ma-0 channel-avatar",
                    click=f"{dialog_key} = true",
                    style=(f"`background-color: ${{{color_key}}} !important;`",),
                ):
                    pass

        with vuetify.VListItemContent(class_="py-0"):
            html.Div(name, class_="text-truncate", style="line-height: 1.1;")
            vuetify.VRangeSlider(
                min=0,
                max=100,
                step=1,
                dense=True,
                hide_details=True,
                class_="mt-0 pt-0",
                style="height: 22px;",
            )

    # Color picker dialog
    with vuetify.VDialog(v_model=(dialog_key, False), max_width=320):
        with vuetify.VCard():
            with vuetify.VCardTitle(class_="py-2"):
                html.Span(f"{name} color", style="font-size: 14px;")
            with vuetify.VCardText(class_="pt-0"):
                vuetify.VColorPicker(
                    v_model=(color_key, "#FFFFFF"),
                    mode="hexa",
                    hide_mode_switch=True,
                    hide_inputs=True,
                )
            with vuetify.VCardActions():
                vuetify.VSpacer()
                with vuetify.VBtn(text=True, click=f"{dialog_key} = false"):
                    html.Span("Close")