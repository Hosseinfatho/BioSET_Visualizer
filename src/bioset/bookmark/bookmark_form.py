# bookmark_form.py
"""Bookmark form: transparent black background, white text. Placeholders only (Name, Description, Comment)."""

from __future__ import annotations

from trame.widgets import html, vuetify

_BG = "background: rgba(0,0,0,0.5); color: #fff;"
_FORM = _BG + " padding: 12px 14px; border-radius: 4px; font-size: 0.75rem;"
_FIELD = dict(
    dense=True, hide_details=True, flat=True, solo_flat=True, dark=True,
    background_color="rgba(0,0,0,0.3)", color="#fff",
    style="flex: 1; min-width: 0; font-size: 0.75rem; color: #fff;",
)
_FIELD_TRANSPARENT = {**_FIELD, "background_color": "transparent"}
_BTN = dict(style="background: rgba(0,0,0,0.4); color: #fff;")


def bookmark_form_panel(state, ctrl):
    with html.Div(style="position: fixed; left: 260px; bottom: 16px; z-index: 200; max-width: 380px; color: #fff;"):
        with html.Div(v_show=("bookmark_form_dialog", False)):
            with html.Div(style=_FORM):
                vuetify.VTextField(
                    v_model=("bookmark_form_name",),
                    placeholder="Name",
                    **_FIELD,
                )
                html.Div(style="margin-bottom: 6px;")
                vuetify.VTextField(
                    v_model=("bookmark_form_description",),
                    placeholder="Description",
                    **_FIELD,
                    rows=2,
                    multiline=True,
                )
                html.Div(style="margin-bottom: 6px;")
                vuetify.VTextField(
                    v_model=("bookmark_form_new_comment",),
                    placeholder="Comment",
                    **_FIELD,
                )
                with html.Div(style="display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap;"):
                    vuetify.VBtn("Cancel", text=True, x_small=True, click="bookmark_form_dialog = false", **_BTN)
                    vuetify.VBtn("Save", x_small=True, click=ctrl.bookmark_save_snapshot, **_BTN)
        with html.Div(v_show=("bookmark_display_snapshot && !bookmark_form_dialog && !bookmark_form_minimized", False)):
            with html.Div(style=_FORM + " margin-top: 8px; max-width: 100%;"):
                with html.Div(style="display: flex; align-items: center; gap: 6px; margin-bottom: 8px; flex-wrap: wrap;"):
                    with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_view_prev, **_BTN):
                        vuetify.VIcon("mdi-chevron-left", x_small=True)
                    html.Span("View {{ (bookmark_current_view_index || 0) + 1 }} of {{ (bookmark_display_snapshot && bookmark_display_snapshot.views && bookmark_display_snapshot.views.length) ? bookmark_display_snapshot.views.length : 1 }}", style="color: #fff; font-size: 0.75rem;")
                    with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_view_next, **_BTN):
                        vuetify.VIcon("mdi-chevron-right", x_small=True)
                    vuetify.VBtn("Add view", text=True, x_small=True, click=ctrl.bookmark_add_view, **_BTN)
                    with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_apply_current_view, **_BTN):
                        vuetify.VIcon("mdi-eye", x_small=True)
                    with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_update_snapshot, **_BTN):
                        vuetify.VIcon("mdi-content-save", x_small=True)
                    with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_open_export_screenshot, **_BTN):
                        vuetify.VIcon("mdi-image-plus", x_small=True)
                    with vuetify.VBtn(icon=True, x_small=True, click="bookmark_form_minimized = true", **_BTN):
                        vuetify.VIcon("mdi-window-minimize", x_small=True)
                    with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_close_display, **_BTN):
                        vuetify.VIcon("mdi-close", x_small=True)
                vuetify.VTextField(
                    v_model=("bookmark_edit_title",),
                    placeholder="Name",
                    **_FIELD,
                )
                html.Div(style="margin-bottom: 6px;")
                vuetify.VTextField(
                    v_model=("bookmark_edit_description",),
                    placeholder="Description",
                    dense=True, hide_details=True, flat=True, solo_flat=True, dark=True,
                    background_color="rgba(0,0,0,0.3)", color="#fff",
                    rows=4, multiline=True,
                    style="width: 100%; font-size: 0.75rem; color: #fff; white-space: pre-wrap; word-wrap: break-word; box-sizing: border-box;",
                )
                html.Div(style="margin-bottom: 6px;")
                vuetify.VTextField(
                    v_model=("bookmark_edit_comment",),
                    placeholder="Comment",
                    **_FIELD,
                )
        with html.Div(v_show=("bookmark_display_snapshot && !bookmark_form_dialog && bookmark_form_minimized", False)):
            with html.Div(style=_FORM + " margin-top: 8px; padding: 8px 12px; display: flex; align-items: center; gap: 8px;"):
                html.Span("View {{ (bookmark_current_view_index || 0) + 1 }} of {{ (bookmark_display_snapshot && bookmark_display_snapshot.views && bookmark_display_snapshot.views.length) ? bookmark_display_snapshot.views.length : 1 }}", style="color: #fff; font-size: 0.75rem;")
                with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_apply_current_view, **_BTN):
                    vuetify.VIcon("mdi-eye", x_small=True)
                with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_open_export_screenshot, **_BTN):
                    vuetify.VIcon("mdi-image-plus", x_small=True)
                with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_update_snapshot, **_BTN):
                    vuetify.VIcon("mdi-content-save", x_small=True)
                with vuetify.VBtn(icon=True, x_small=True, click="bookmark_form_minimized = false", **_BTN):
                    vuetify.VIcon("mdi-window-restore", x_small=True)
                with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_close_display, **_BTN):
                    vuetify.VIcon("mdi-close", x_small=True)
        # Export screenshot popup: two empty transparent boxes (placeholder only until user types)
        with vuetify.VDialog(v_model=("bookmark_export_screenshot_dialog", False), max_width="420", persistent=True):
            with vuetify.VCard(style=_FORM):
                vuetify.VCardTitle("Export screenshot", style="color: #fff; font-size: 0.9rem;")
                with vuetify.VCardText():
                    vuetify.VTextField(
                        v_model=("bookmark_export_screenshot_name",),
                        placeholder="Name",
                        **_FIELD_TRANSPARENT,
                    )
                    html.Div(style="margin-top: 8px;")
                    vuetify.VTextField(
                        v_model=("bookmark_export_screenshot_caption",),
                        placeholder="Caption",
                        **_FIELD_TRANSPARENT,
                        rows=3,
                        multiline=True,
                    )
                with vuetify.VCardActions():
                    vuetify.VSpacer()
                    vuetify.VBtn("Cancel", text=True, click="bookmark_export_screenshot_dialog = false", **_BTN)
                    vuetify.VBtn("Save", click=ctrl.bookmark_export_screenshot_save, **_BTN)
