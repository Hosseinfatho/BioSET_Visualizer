# bookmark_column.py
"""Bookmark column next to the left drawer: same size and visual (VNavigationDrawer). Category dropdown, Show, New, list of bookmarks."""

from __future__ import annotations

from trame.widgets import html, vuetify


def bookmark_column(state, ctrl):
    """Second drawer (same width 250px, same style) shown when bookmark_open. Sits next to the main left drawer."""
    with vuetify.VNavigationDrawer(
        v_show=("bookmark_open", False),
        width=250,
        permanent=True,
        app=True,
        dark=True,
        color="rgba(18, 18, 18, 0.6)",
        style="flex-shrink: 0;",
    ):
        with html.Div(style="display: flex; flex-direction: column; height: 100%; overflow: hidden;"):
            # Header
            with vuetify.VListItem(dense=True, style="flex: 0 0 auto;"):
                with vuetify.VListItemContent():
                    vuetify.VListItemTitle("Bookmarks", style="font-size: 1rem; font-weight: 600;")
            vuetify.VDivider()
            # Top: Category dropdown, Show, New
            with html.Div(style="padding: 10px 8px; flex: 0 0 auto; border-bottom: 1px solid rgba(255,255,255,0.1);"):
                vuetify.VSelect(
                    v_model=("bookmark_selected_category", "Uncategorized"),
                    items=("bookmark_categories", []),
                    dense=True,
                    hide_details=True,
                    placeholder="Category",
                    label="Category",
                    dark=True,
                    style="min-width: 0; font-size: 0.8rem;",
                )
                with html.Div(style="display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap;"):
                    with vuetify.VBtn(
                        v_show=("!bookmark_flags_visible", True),
                        x_small=True,
                        color="primary",
                        click=ctrl.bookmark_show_category_flags,
                        style="flex: 1 1 0; min-width: 0;",
                    ):
                        html.Span("Show")
                    with vuetify.VBtn(
                        v_show=("bookmark_flags_visible", False),
                        x_small=True,
                        color="primary",
                        outlined=True,
                        click=ctrl.bookmark_hide_flags,
                        style="flex: 1 1 0; min-width: 0;",
                    ):
                        html.Span("Hide")
                    with vuetify.VBtn(
                        x_small=True,
                        color="secondary",
                        click=ctrl.bookmark_open_new_form,
                        style="flex: 1 1 0; min-width: 0;",
                    ):
                        html.Span("New")
            # List of bookmarks (scrollable)
            with html.Div(
                style="flex: 1 1 0; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 6px 4px;",
            ):
                with vuetify.VList(dense=True, style="background: transparent;"):
                    with vuetify.VListItem(
                        v_for=("(item, idx) in bookmark_list_items",),
                        key=("item.name",),
                        style="align-items: flex-start; padding: 6px 8px; margin-bottom: 4px; border-radius: 4px; "
                             "cursor: pointer; border: 1px solid rgba(255,255,255,0.08);",
                        click=(ctrl.bookmark_open_snapshot, "[item.name]"),
                    ):
                        with vuetify.VListItemAvatar(size=40, tile=True, style="min-width: 40px; border-radius: 4px; overflow: hidden; background: rgba(255,255,255,0.1);"):
                            html.Img(
                                v_show=("item.thumbnail", True),
                                v_bind_src=("item.thumbnail",),
                                style="width: 40px; height: 40px; object-fit: cover;",
                            )
                            vuetify.VIcon(
                                "mdi-bookmark-outline",
                                v_show=("!item.thumbnail", True),
                                small=True,
                                style="color: rgba(255,255,255,0.5);",
                            )
                        with vuetify.VListItemContent(style="padding-left: 10px; min-width: 0;"):
                            vuetify.VListItemTitle(
                                style="font-size: 0.8rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;",
                                text=("item.name",),
                            )
                            vuetify.VListItemSubtitle(
                                style="font-size: 0.7rem; opacity: 0.85; white-space: normal; line-height: 1.2; margin-top: 2px;",
                                text=("item.description || '—'",),
                            )
