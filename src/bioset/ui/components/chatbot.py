from __future__ import annotations

from trame.widgets import html, vuetify


def chatbot_section(state, ctrl):
    """
    Chatbot section for the right drawer.
    Provides an LLM chat interface for querying the visualization.
    """
    
    with vuetify.VExpansionPanels(
        v_model=("chatbot_panel_open",),
        accordion=True,
        flat=True,
        dark=True
    ):
        with vuetify.VExpansionPanel():
            # Panel Header
            with vuetify.VExpansionPanelHeader(
                classes="px-4 py-2",
                disable_icon_rotate=True,
            ):
                html.Div("AI Assistant", classes="text-overline", style="color: white;")
                with vuetify.Template(v_slot_actions=True):
                    vuetify.VIcon(
                        "{{ chatbot_panel_open === 0 ? 'mdi-chevron-up' : 'mdi-chevron-down' }}",
                        small=True,
                    )
            
            # Panel Content
            with vuetify.VExpansionPanelContent(classes="px-0 py-0"):
                with html.Div(v_if="!chatbot_authenticated", classes="mb-3"):
                    html.Span("Biomni not initialized.",
                              classes="text-caption grey--text font-weight-bold letter-spacing-1")
                with html.Div(v_if="chatbot_authenticated"):
                    with html.Div(classes="px-4 py-3"):
                        with html.Div(
                                classes="chatbot-messages mb-3 mt-3",
                            style="max-height: 400px; overflow-y: auto; border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 4px; padding: 8px;",
                        ):
                            # Empty state
                            with html.Div(
                                v_if="chatbot_messages.length === 0",
                                classes="text-center py-4",
                            ):
                                vuetify.VIcon("mdi-message-outline", size=48, color="grey")
                                html.Div(
                                    "Label active channels with Biomni",
                                    classes="text-caption grey--text mt-2",
                                )

                            # Message list
                            with html.Div(
                                v_for="(message, index) in chatbot_messages",
                                key="index",
                                classes="mb-2",
                            ):
                                # User message
                                with html.Div(
                                    v_if="message.role === 'user'",
                                    classes="d-flex justify-end",
                                ):
                                    with vuetify.VCard(
                                        classes="pa-2",
                                        color="#616161",
                                        dark=True,
                                        style="max-width: 80%;",
                                    ):
                                        html.Div(
                                            "{{ message.content }}",
                                            classes="text-body-2",
                                        )

                                # Assistant message
                                with html.Div(
                                    v_if="message.role === 'assistant'",
                                    classes="d-flex justify-start",
                                ):
                                    with vuetify.VCard(
                                        classes="pa-2",
                                        color="#303030",
                                        dark=True,
                                        style="max-width: 80%;",
                                    ):
                                        html.Div(
                                            "{{ message.content }}",
                                            classes="text-body-2",
                                        )

                                # Error message
                                with html.Div(
                                    v_if="message.role === 'error'",
                                    classes="d-flex justify-start",
                                ):
                                    with vuetify.VAlert(
                                        type="error",
                                        dense=True,
                                        text=True,
                                    ):
                                        html.Span(
                                            "{{ message.content }}",
                                            classes="text-caption",
                                        )

                            # Loading indicator
                            with html.Div(
                                v_if="chatbot_loading",
                                classes="d-flex justify-start mb-2",
                            ):
                                with vuetify.VCard(
                                    classes="pa-2",
                                    outlined=True,
                                ):
                                    with html.Div(classes="d-flex align-center"):
                                        vuetify.VProgressCircular(
                                            indeterminate=True,
                                            size=16,
                                            width=2,
                                        )
                                        html.Span(
                                            "Thinking...",
                                            classes="ml-2 text-caption",
                                        )

                        # Input area
                        with html.Div(classes="d-flex align-center"):
                            vuetify.VTextField(
                                v_model=("chatbot_input",),
                                placeholder="Ask a question about active markers...",
                                dense=True,
                                outlined=True,
                                hide_details=True,
                                disabled=("chatbot_loading",),
                                classes="flex-grow-1",
                                __events=["keyup.enter"],
                                keyup__enter=ctrl.chatbot_send_message,
                            )

                            with vuetify.VBtn(
                                icon=True,
                                small=True,
                                click=ctrl.chatbot_send_message,
                                disabled=("chatbot_loading || !chatbot_input || chatbot_input.trim() === ''",),
                                classes="ml-1",
                            ):
                                vuetify.VIcon("mdi-send", small=True)

                        # Label button + Clear chat
                        with html.Div(classes="d-flex mt-2"):
                            vuetify.VBtn(
                                "Label",
                                click=ctrl.chatbot_label,
                                x_small=True,
                                outlined=True,
                                color="white",
                                disabled=("chatbot_loading",),
                                classes="flex-grow-1 mr-1",
                            )
                            vuetify.VBtn(
                                "Clear",
                                click=ctrl.chatbot_clear,
                                x_small=True,
                                text=True,
                                color="grey",
                                classes="flex-grow-1",
                            )