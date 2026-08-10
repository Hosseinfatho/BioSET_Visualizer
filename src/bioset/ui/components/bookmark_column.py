# bookmark_column.py
"""Bookmark column as overlay next to the left drawer: same width and style. Category dropdown, Show, New, list of bookmarks. Does not change view size."""

from __future__ import annotations

from trame.widgets import html, vuetify


def bookmark_column(state, ctrl):
    """Overlay column (250px wide, same style as left drawer) shown when bookmark_open. Rendered inside overlay wrapper in layout."""
    with html.Div(
            class_="bookmark-overlay-column",
            style="width: 100%; height: 100%; background: rgba(18, 18, 18, 0.6); display: flex; flex-direction: column; overflow: hidden;",
    ):
        with html.Div(style="display: flex; flex-direction: column; height: 100%; overflow: hidden;"):
            # Header
            with vuetify.VListItem(dense=True, style="flex: 0 0 auto;"):
                with vuetify.VListItemContent():
                    vuetify.VListItemTitle("Bookmarks", class_="bookmark-column-header",
                                           style="font-size: 1rem; font-weight: 600; color: #ffffff;")
                with vuetify.VListItemAction(style="margin: 0;"):
                    with vuetify.VBtn(
                            icon=True, x_small=True,
                            click="bookmark_open = false",
                            title="Close bookmarks",
                    ):
                        vuetify.VIcon("mdi-close", small=True, color="grey")
            vuetify.VDivider()
            # Top: Category dropdown, Show, New
            with html.Div(style="padding: 10px 8px; flex: 0 0 auto; border-bottom: 1px solid rgba(255,255,255,0.1);"):
                with html.Div(style="display: flex; align-items: center; gap: 4px;"):
                    vuetify.VSelect(
                        v_model=("bookmark_selected_category", "Uncategorized"),
                        items=("bookmark_categories", []),
                        dense=True,
                        hide_details=True,
                        placeholder="Category",
                        label="Category",
                        dark=True,
                        style="min-width: 0; font-size: 0.8rem; flex: 1;",
                    )
                    with vuetify.VBtn(
                            icon=True, x_small=True,
                            click=ctrl.bookmark_delete_category,
                            title="Delete this category and all its bookmarks",
                    ):
                        vuetify.VIcon("mdi-delete-outline", x_small=True, color="grey")
                with html.Div(style="display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap;"):
                    with vuetify.VBtn(
                            v_show=("!bookmark_flags_visible", True),
                            x_small=True,
                            color="white",
                            click=ctrl.bookmark_show_category_flags,
                            style="flex: 1 1 0; min-width: 0;",
                    ):
                        html.Span("Show")
                    with vuetify.VBtn(
                            v_show=("bookmark_flags_visible", False),
                            x_small=True,
                            color="white",
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
            # List of bookmarks: thumbnail 10% larger (79px), very small gap left/top/bottom
            with html.Div(
                    class_="bookmark-list-scroll",
                    style="flex: 1 1 0; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 3px 2px 3px 3px;",
            ):
                with vuetify.VList(dense=True, style="background: transparent;"):
                    with vuetify.VListItem(
                            v_for=("(item, idx) in bookmark_list_items",),
                            key=("item.name",),
                            class_="bookmark-list-item",
                            style="display: flex; flex-direction: row; align-items: flex-start; padding: 4px 2px; margin: 0; border-radius: 0; border-bottom: 1px solid rgba(255,255,255,0.2); gap: 0;",
                    ):
                        # Thumbnail: 10% larger than 72px = 79px
                        with html.Div(class_="bookmark-thumb-wrap",
                                      click=(ctrl.bookmark_thumbnail_single_click, "[item.name]"),
                                      dblclick=(ctrl.bookmark_thumbnail_double_click, "[item.name]"),
                                      style="position: relative; width: 79px; height: 79px; flex-shrink: 0; border-radius: 4px; overflow: hidden; border: 1px solid rgba(255,255,255,0.25); background: rgba(255,255,255,0.06); margin: 0; cursor: pointer;"):
                            html.Img(
                                v_show=("item.thumbnail", True),
                                v_bind_src=("item.thumbnail",),
                                style="width: 79px; height: 79px; object-fit: cover; display: block;",
                            )
                            vuetify.VIcon(
                                "mdi-bookmark-outline",
                                v_show=("!item.thumbnail", True),
                                style="font-size: 30px; color: rgba(255,255,255,0.5); position: absolute; left: 50%; top: 50%; transform: translate(-50%,-50%);",
                            )
                        # In front of thumbnail: Name (line 1), Description (next line) — white, one size larger font
                        with html.Div(class_="bookmark-item-text",
                                      style="flex: 1; min-width: 0; padding-left: 8px; margin: 0;"):
                            html.Div(
                                class_="bookmark-item-name",
                                style="font-size: 0.9rem; font-weight: 600; color: #ffffff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 4px;",
                                v_text=("item.name",),
                                click=(ctrl.bookmark_thumbnail_single_click, "[item.name]"),
                                dblclick=(ctrl.bookmark_thumbnail_double_click, "[item.name]"),
                            )
                            html.Div(
                                class_="bookmark-item-desc",
                                style="font-size: 0.8rem; color: #ffffff; line-height: 1.25; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;",
                                v_text=("item.description || '—'",),
                                click=(ctrl.bookmark_thumbnail_single_click, "[item.name]"),
                                dblclick=(ctrl.bookmark_thumbnail_double_click, "[item.name]"),
                            )
                            # Last row after description: Delete button (does not trigger open)
                            with html.Div(style="display: flex; justify-content: flex-end; margin-top: 6px;"):
                                with vuetify.VBtn(
                                        icon=True,
                                        x_small=True,
                                        color="error",
                                        click=(ctrl.bookmark_delete_snapshot, "[item.name, item.category]"),
                                ):
                                    vuetify.VIcon("mdi-delete", small=True)
