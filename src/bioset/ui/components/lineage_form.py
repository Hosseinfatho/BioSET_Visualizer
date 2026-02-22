# lineage_form.py
"""Lineage form: font 2 sizes smaller, left-aligned. Description ends with (User, d, m, y) in smaller font."""

from __future__ import annotations

from trame.widgets import html, vuetify

# Form font 2 sizes smaller, left-aligned
_FORM_BASE = "background: rgba(18, 18, 18, 0.6); color: rgba(255, 255, 255, 0.9); padding: 12px 14px; border-radius: 4px; font-size: 0.75rem; text-align: left;"
_FORM_STYLE = _FORM_BASE
_ROW = "display: flex; align-items: center; gap: 8px; margin-bottom: 6px; text-align: left;"
# (User, date) 2 font sizes smaller than form
_SIGNATURE = "font-size: 0.65rem; color: rgba(255,255,255,0.75); margin-top: 2px;"
_FIELD_PROPS = dict(
    dense=True,
    hide_details=True,
    flat=True,
    solo_flat=True,
    dark=True,
    background_color="transparent",
    style="flex: 1; min-width: 0; font-size: 0.75rem;",
)


def lineage_form_panel(state, ctrl):
    with html.Div(style="position: fixed; left: 260px; bottom: 16px; z-index: 200; max-width: 380px; color: rgba(255,255,255,0.9); font-size: 0.75rem; text-align: left;"):
        # New snapshot
        with html.Div(v_show=("lineage_form_dialog", False)):
            with html.Div(style=_FORM_STYLE):
                with html.Div(style=_ROW):
                    html.Span("User:", style="min-width: 72px; color: rgba(255,255,255,0.9); font-size: 0.75rem;")
                    vuetify.VTextField(v_model=("lineage_form_user",), **_FIELD_PROPS)
                with html.Div(style=_ROW):
                    html.Span("Id:", style="min-width: 72px; color: rgba(255,255,255,0.9); font-size: 0.75rem;")
                    vuetify.VTextField(v_model=("lineage_form_name",), **_FIELD_PROPS)
                with html.Div(style=_ROW):
                    html.Span("Description:", style="min-width: 72px; color: rgba(255,255,255,0.9); font-size: 0.75rem;")
                    vuetify.VTextField(v_model=("lineage_form_description",), **_FIELD_PROPS, rows=2, multiline=True)
                with html.Div(style=_ROW):
                    html.Span("Comment:", style="min-width: 72px; color: rgba(255,255,255,0.9); font-size: 0.75rem;")
                    vuetify.VTextField(v_model=("lineage_form_new_comment",), **_FIELD_PROPS)
                with html.Div(style="display: flex; align-items: center; gap: 8px; margin-top: 8px;"):
                    with vuetify.VBtn(text=True, x_small=True, click="lineage_form_dialog = false"):
                        html.Span("Disagree")
                    with vuetify.VBtn(color="primary", x_small=True, click=ctrl.lineage_save_snapshot):
                        html.Span("Save")
        # Opened snapshot: same; at end of Description show (User, d, m, y) 2 sizes smaller
        with html.Div(v_show=("lineage_display_snapshot && !lineage_form_dialog", False)):
            with html.Div(style=_FORM_STYLE + " margin-top: 8px;"):
                with html.Div(style=_ROW):
                    html.Span("User:", style="min-width: 72px; color: rgba(255,255,255,0.9); font-size: 0.75rem;")
                    html.Span("{{ lineage_display_snapshot ? (lineage_display_snapshot.user || '') : '' }}", style="color: rgba(255,255,255,0.9); font-size: 0.75rem;")
                with html.Div(style=_ROW):
                    html.Span("Id:", style="min-width: 72px; color: rgba(255,255,255,0.9); font-size: 0.75rem;")
                    html.Span("{{ lineage_display_snapshot ? (lineage_display_snapshot.title || '') : '' }}", style="color: rgba(255,255,255,0.9); font-size: 0.75rem;")
                with html.Div(style="margin-bottom: 6px;"):
                    html.Span("Description:", style="min-width: 72px; color: rgba(255,255,255,0.9); font-size: 0.75rem; display: block; margin-bottom: 2px;")
                    vuetify.VTextField(v_model=("lineage_edit_description",), **_FIELD_PROPS, rows=2, multiline=True)
                    with html.Div(v_show=("lineage_display_snapshot && (lineage_display_snapshot.user || lineage_display_snapshot.updated_short)", False)):
                        html.Span(
                            "({{ lineage_display_snapshot ? lineage_display_snapshot.user : '' }}, {{ lineage_display_snapshot ? (lineage_display_snapshot.updated_short || '') : '' }})",
                            style=_SIGNATURE,
                            class_="d-block",
                        )
                with html.Div(style=_ROW):
                    html.Span("Comment:", style="min-width: 72px; color: rgba(255,255,255,0.9); font-size: 0.75rem;")
                    vuetify.VTextField(v_model=("lineage_edit_comment",), **_FIELD_PROPS, placeholder="Add comment...")
                with html.Div(style="display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: nowrap;"):
                    with vuetify.VBtn(text=True, x_small=True, click=ctrl.lineage_agree):
                        html.Span("Agree")
                    with vuetify.VBtn(text=True, x_small=True, click=ctrl.lineage_disagree):
                        html.Span("Disagree")
                    html.Span("Disagree: {{ lineage_display_snapshot ? (lineage_display_snapshot.disagreements || 0) : 0 }}", style="color: rgba(255,255,255,0.85); white-space: nowrap; font-size: 0.75rem;")
                    with vuetify.VBtn(text=True, x_small=True, color="primary", click=ctrl.lineage_update_snapshot):
                        html.Span("Save")
                    with vuetify.VBtn(icon=True, x_small=True, click="lineage_display_snapshot = null"):
                        vuetify.VIcon("mdi-close", x_small=True)