# lineage_form.py
"""Lineage: one flat sheet, no outline/separator. User, Id, Description. Buttons: Agree, Disagree, Comment, Save."""

from __future__ import annotations

from trame.widgets import html, vuetify

_COMMON_FIELD = dict(
    dense=True,
    hide_details=True,
    flat=True,
    solo_flat=True,
    background_color="transparent",
)


def lineage_form_panel(state, ctrl):
    with html.Div(style="position: fixed; left: 260px; bottom: 16px; z-index: 200; max-width: 360px;"):
        # New snapshot: one sheet, no outline/separator
        with html.Div(v_show=("lineage_form_dialog", False)):
            with html.Div(
                style="background: rgba(24, 24, 24, 0.94); padding: 12px 14px; border-radius: 4px;",
            ):
                html.Span("User:", classes="text-caption d-block mb-0")
                vuetify.VTextField(v_model=("lineage_form_user",), **_COMMON_FIELD, class_="mt-0 mb-1 pt-0")
                html.Span("Id:", classes="text-caption d-block mb-0")
                vuetify.VTextField(v_model=("lineage_form_name",), **_COMMON_FIELD, class_="mt-0 mb-1 pt-0")
                html.Span("Description:", classes="text-caption d-block mb-0")
                vuetify.VTextField(
                    v_model=("lineage_form_description",),
                    **_COMMON_FIELD,
                    rows=2,
                    multiline=True,
                    class_="mt-0 mb-2 pt-0",
                )
                with html.Div(style="display: flex; flex-wrap: wrap; gap: 6px; align-items: center;"):
                    with vuetify.VBtn(text=True, x_small=True, click="lineage_form_dialog = false"):
                        html.Span("Disagree")
                    with vuetify.VBtn(color="primary", x_small=True, click=ctrl.lineage_save_snapshot):
                        html.Span("Save")
        # Opened snapshot: same sheet style, editable, Agree / Disagree / Comment / Save
        with html.Div(v_show=("lineage_display_snapshot && !lineage_form_dialog", False)):
            with html.Div(
                style="background: rgba(24, 24, 24, 0.94); padding: 12px 14px; border-radius: 4px; margin-top: 8px;",
            ):
                html.Span("User:", classes="text-caption d-block mb-0")
                html.Span("{{ lineage_display_snapshot ? (lineage_display_snapshot.user || '') : '' }}", classes="text-body2 d-block mb-1")
                html.Span("Id:", classes="text-caption d-block mb-0")
                html.Span("{{ lineage_display_snapshot ? (lineage_display_snapshot.title || '') : '' }}", classes="text-body2 d-block mb-1")
                html.Span("Description:", classes="text-caption d-block mb-0")
                vuetify.VTextField(
                    v_model=("lineage_edit_description",),
                    **_COMMON_FIELD,
                    rows=2,
                    multiline=True,
                    class_="mt-0 mb-1 pt-0",
                )
                vuetify.VTextField(
                    v_model=("lineage_edit_comment",),
                    **_COMMON_FIELD,
                    placeholder="Add comment...",
                    class_="mb-2",
                )
                html.Span("Agree: {{ lineage_display_snapshot ? (lineage_display_snapshot.agreements || 0) : 0 }} | Disagree: {{ lineage_display_snapshot ? (lineage_display_snapshot.disagreements || 0) : 0 }}", classes="text-caption d-block mb-1")
                with html.Div(style="display: flex; flex-wrap: wrap; gap: 6px; align-items: center;"):
                    with vuetify.VBtn(text=True, x_small=True, click=ctrl.lineage_agree):
                        html.Span("Agree")
                    with vuetify.VBtn(text=True, x_small=True, click=ctrl.lineage_disagree):
                        html.Span("Disagree")
                    with vuetify.VBtn(text=True, x_small=True, click=ctrl.lineage_comment):
                        html.Span("Comment")
                    with vuetify.VBtn(text=True, x_small=True, color="primary", click=ctrl.lineage_update_snapshot):
                        html.Span("Save")
                    with vuetify.VBtn(icon=True, x_small=True, click="lineage_display_snapshot = null"):
                        vuetify.VIcon("mdi-close", x_small=True)