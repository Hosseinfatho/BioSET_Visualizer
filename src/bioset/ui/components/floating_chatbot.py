from __future__ import annotations

from trame.widgets import html, vuetify


def floating_chatbot_section(state, ctrl):
    """
    Floating chatbot popup with FAB trigger.
    FAB turns primary-colored when a tile is selected.
    The popup shows a "Selected Tile" subtitle + deselect button when applicable.
    Label button is only visible when a tile is selected.
    Anchor toggle appears after labels have been generated.
    Eye button hides/shows the action button row.
    """

    # ── FAB (drawer closed) ───────────────────────────────────────────────────
    with vuetify.VBtn(
            v_if="analysis_loaded && !right_drawer_open",
            fab=True,
            large=True,
            dark=("!selected_tile",),
            color=("selected_tile ? 'white' : ''",),
            outlined=("!selected_tile",),
            style="position: absolute; bottom: 16px; right: 16px; z-index: 100;",
            click="chatbot_panel_open = !chatbot_panel_open",
    ):
        vuetify.VIcon("mdi-message-text")

    # ── FAB (drawer open) ─────────────────────────────────────────────────────
    with vuetify.VBtn(
            v_if="analysis_loaded && right_drawer_open",
            fab=True,
            large=True,
            dark=("!selected_tile",),
            color=("selected_tile ? 'white' : ''",),
            outlined=("!selected_tile",),
            style="position: absolute; bottom: 16px; right: 366px; z-index: 100;",
            click="chatbot_panel_open = !chatbot_panel_open",
    ):
        vuetify.VIcon("mdi-message-text")

    # ── Floating panel ────────────────────────────────────────────────────────
    with html.Div(
            v_if="chatbot_panel_open === true",
            v_bind_style=(
                "{'position': 'fixed', 'bottom': '95px', 'z-index': '200', "
                "'width': '350px', 'right': right_drawer_open ? '366px' : '16px'}"
            ),
    ):
        with vuetify.VCard(dark=True, classes="px-4 py-3"):

            # ── Header row: title + clear chat ───────────────────────────────
            with html.Div(classes="d-flex align-center justify-space-between"):
                html.Div("AI Assistant", classes="text-overline", style="color: white;")
                with vuetify.VBtn(
                        icon=True, x_small=True,
                        click=ctrl.chatbot_clear,
                        title="Clear chat",
                ):
                    vuetify.VIcon("mdi-delete-outline", x_small=True, color="grey lighten-1")

            # ── Selected-tile subtitle + deselect button ──────────────────────
            with html.Div(
                    v_if="selected_tile",
                    classes="d-flex align-center mb-1",
            ):
                vuetify.VIcon("mdi-grid", x_small=True, color="white", classes="mr-1")
                html.Span(
                    "Selected Tile",
                    classes="text-caption white--text font-weight-medium",
                    style="flex: 1;",
                )
                with vuetify.VBtn(
                        icon=True, x_small=True,
                        click=ctrl.deselect_tile,
                        title="Deselect tile — removes surface mesh and labels",
                ):
                    vuetify.VIcon("mdi-close-circle-outline", x_small=True, color="grey")

            # ── Not-initialized notice ────────────────────────────────────────
            with html.Div(v_if="!chatbot_authenticated", classes="mb-2 mt-2"):
                html.Span(
                    "LLM not initialized.",
                    classes="text-caption grey--text font-weight-bold",
                )

            # ── Authenticated content ─────────────────────────────────────────
            with html.Div(v_if="chatbot_authenticated"):

                # Messages area
                with html.Div(
                        classes="chatbot-messages mb-3 mt-2",
                        style=(
                            "max-height: 350px; overflow-y: auto; "
                            "border: 1px solid rgba(255,255,255,0.12); "
                            "border-radius: 4px; padding: 8px;"
                        ),
                ):
                    with html.Div(
                            v_if="chatbot_messages.length === 0",
                            classes="text-center py-4",
                    ):
                        vuetify.VIcon("mdi-message-outline", size=40, color="grey")
                        html.Div("Biomni / Claude", classes="text-caption grey--text mt-2")

                    with html.Div(
                            v_for="(message, index) in chatbot_messages",
                            key="index",
                            classes="mb-2",
                    ):
                        with html.Div(v_if="message.role === 'user'", classes="d-flex justify-end"):
                            with vuetify.VCard(classes="pa-2", color="#616161", dark=True, style="max-width:80%;"):
                                html.Div("{{ message.content }}", classes="text-body-2")

                        with html.Div(v_if="message.role === 'assistant' && message.format === 'json'", classes="d-flex justify-start"):
                            with vuetify.VCard(classes="pa-2", color="#303030", dark=True, style="max-width:90%;"):
                                html.Pre("{{ message.content }}", classes="text-body-2", style="white-space: pre-wrap; margin: 0;")

                        with html.Div(v_if="message.role === 'assistant' && message.format === 'suggest'", classes="d-flex justify-start"):
                            with vuetify.VCard(classes="pa-2", color="#303030", dark=True, style="max-width:90%; width:100%;"):
                                html.Div("Suggested Channels", classes="text-caption font-weight-bold mb-1")
                                with vuetify.VList(dense=True, dark=True, color="transparent", classes="pa-0"):
                                    with vuetify.VListItem(
                                        v_for="(s, si) in message.suggestions",
                                        key="si",
                                        classes="px-0",
                                        style="min-height: 28px;",
                                    ):
                                        with vuetify.VListItemContent(classes="py-0"):
                                            with html.Div(classes="d-flex align-center"):
                                                html.Span("{{ s.channel }}", classes="text-body-2 font-weight-medium", style="flex: 1;")
                                                html.Span(
                                                    "{{ '●'.repeat(Math.max(1, Math.min(3, s.dots))) }}",
                                                    classes="text-caption ml-2",
                                                    style="letter-spacing: 2px; color: #aaa;",
                                                )
                                            html.Div("{{ s.reason }}", classes="text-caption grey--text", style="line-height: 1.2;")

                        with html.Div(v_if="message.role === 'assistant' && message.format === 'bookmark'", classes="d-flex justify-start"):
                            with vuetify.VCard(classes="pa-3", color="#303030", dark=True, style="max-width:90%; width:100%;"):
                                html.Div("Bookmark Suggestion", classes="text-caption font-weight-bold mb-2", style="color: #ffffff;")
                                html.Div("Title", classes="text-caption grey--text mb-0")
                                html.Div("{{ message.title }}", classes="text-body-2 font-weight-medium mb-2")
                                html.Div("Category", classes="text-caption grey--text mb-0")
                                with html.Div(classes="mb-2"):
                                    vuetify.VChip(
                                        "{{ message.category }}",
                                        small=True,
                                        color="#616161",
                                        text_color="white",
                                    )
                                html.Div("Description", classes="text-caption grey--text mb-0", v_if="message.description")
                                html.Div("{{ message.description }}", classes="text-body-2", v_if="message.description", style="white-space: pre-wrap; line-height: 1.4;")

                        with html.Div(v_if="message.role === 'assistant' && !message.format", classes="d-flex justify-start"):
                            with vuetify.VCard(classes="pa-2", color="#303030", dark=True, style="max-width:80%;"):
                                html.Div("{{ message.content }}", classes="text-body-2")

                        with html.Div(v_if="message.role === 'error'", classes="d-flex justify-start"):
                            with vuetify.VAlert(type="error", dense=True, text=True):
                                html.Span("{{ message.content }}", classes="text-caption")

                    with html.Div(v_if="chatbot_loading", classes="d-flex justify-start mb-2"):
                        with vuetify.VCard(classes="pa-2", outlined=True):
                            with html.Div(classes="d-flex align-center"):
                                vuetify.VProgressCircular(indeterminate=True, size=16, width=2)
                                html.Span("Thinking...", classes="ml-2 text-caption")

                # Input row
                with html.Div(classes="d-flex align-center"):
                    vuetify.VTextField(
                        v_model=("chatbot_input",),
                        placeholder="Ask about active markers...",
                        dense=True,
                        outlined=True,
                        hide_details=True,
                        disabled=("chatbot_loading",),
                        classes="flex-grow-1",
                        __events=["keyup.enter"],
                        keyup__enter=ctrl.chatbot_send_message,
                    )
                    with vuetify.VBtn(
                            icon=True, small=True,
                            click=ctrl.chatbot_send_message,
                            disabled=("chatbot_loading || !chatbot_input || chatbot_input.trim() === ''",),
                            classes="ml-1",
                    ):
                        vuetify.VIcon("mdi-send", small=True)

                # ── Action buttons ────────────────────────────────────────────
                with html.Div(classes="d-flex align-center mt-2"):
                    # Label — tile-only
                    vuetify.VBtn(
                        "Label",
                        v_if="selected_tile",
                        click=ctrl.chatbot_label,
                        x_small=True,
                        outlined=True,
                        color="white",
                        disabled=("chatbot_loading",),
                        classes="mr-1",
                    )
                    # Suggest Channels — tile-only
                    vuetify.VBtn(
                        "Suggest Channels",
                        v_if="selected_tile",
                        click=ctrl.chatbot_suggest,
                        x_small=True,
                        outlined=True,
                        color="white",
                        disabled=("chatbot_loading",),
                        classes="mr-1",
                    )
                    # Anchor — filled when on, outlined when off; only after labels exist
                    vuetify.VBtn(
                        "Anchor",
                        v_if="chatbot_labels_generated",
                        click="anchor_labels = !anchor_labels",
                        x_small=True,
                        color=("anchor_labels ? 'white' : 'white'",),
                        light=("anchor_labels",),
                        depressed=("anchor_labels",),
                        outlined=("!anchor_labels",),
                        classes="mr-1",
                    )
                    # Eye — show/hide labels in scene (only when labels exist)
                    with vuetify.VBtn(
                            v_if="chatbot_labels_generated",
                            icon=True, x_small=True,
                            click=ctrl.toggle_labels,
                            title="Show / hide labels",
                            classes="mr-1",
                    ):
                        vuetify.VIcon(
                            "{{ show_labels ? 'mdi-eye' : 'mdi-eye-off' }}",
                            x_small=True, color="grey lighten-1",
                        )
