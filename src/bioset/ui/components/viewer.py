# viewer.py
"""VTK viewer component. Includes 2D NOV lens: circle (inscribed in rect, same center, radius = half rect side); drag and +/- for size."""

from __future__ import annotations

from trame.widgets import vtk, vuetify, html

from bioset.bookmark import bookmark_form_panel  # noqa: F401 - for ctrl ref in popup


def viewer(ctrl, render_window):
    """Create the VTK viewer container. Returns the view object."""
    
    with vuetify.VContainer(
        fluid=True,
        classes="pa-0 fill-height",
        style="position: relative;",
    ):
        with html.Div(style="position: relative; width: 100%; height: 100%; min-height: 200px;"):
            view = vtk.VtkRemoteView(
                render_window,
                interactive_ratio=1.0,
            )
            # Client-side mouse tracker: emits hover + right-click on the VTK canvas and forwards to server triggers.
            vuetify.Template(
                """
                <hover-tracker
                  @hover="trigger('on_hover', $event)"
                  @rightclick="trigger('on_right_click', $event)"
                />
                """
            )
            ctrl.view_update = view.update
            vuetify.Template("""
                <hover-tracker @hover="trigger('on_hover', $event)" />
            """)
            # 2D NOV lens: drag to move via Trame v_on (no custom JS); +/- for size.
            # Overlay: pointer-events auto when lens visible so lens can receive clicks (script needs this to start drag)
            _overlay_style = (
                "'position: absolute; top: 0; left: 0; right: 0; bottom: 0; z-index: 50; pointer-events: ' + (nov_show_rect ? 'auto' : 'none')",
                "position: absolute; top: 0; left: 0; right: 0; bottom: 0; z-index: 50; pointer-events: auto;",
            )
            # Immediate DOM capture so overlay gets mousemove/mouseup without waiting for state sync
            _mousedown = (
                "if ($event.target.closest('.nov-rect-controls')) return; "
                "var el = $event.currentTarget.closest('.nov-rect-overlay'); if (el) el.style.pointerEvents = 'auto'; "
                "nov_dragging = true; "
                "nov_drag_start_rect_x = nov_rect_x; nov_drag_start_rect_y = nov_rect_y; "
                "nov_drag_start_client_x = $event.clientX; nov_drag_start_client_y = $event.clientY; "
                "nov_viewport_w = window.innerWidth; nov_viewport_h = window.innerHeight; "
                "nov_drag_delta_x = 0; nov_drag_delta_y = 0"
            )
            _mousemove = (
                "if (nov_dragging) { "
                "nov_drag_delta_x = ($event.clientX - nov_drag_start_client_x) / nov_viewport_w; "
                "nov_drag_delta_y = -(($event.clientY - nov_drag_start_client_y) / nov_viewport_h); "
                "}"
            )
            _mouseup = (
                "var el = $event.currentTarget.closest ? $event.currentTarget.closest('.nov-rect-overlay') : $event.currentTarget; "
                "if (el) el.style.pointerEvents = 'none'; "
                "if (nov_dragging) { var s = Math.min(nov_rect_w, nov_rect_h); "
                "var x = Math.max(0, Math.min(1 - s, nov_drag_start_rect_x + nov_drag_delta_x)); "
                "var y = Math.max(0, Math.min(1 - s, nov_drag_start_rect_y + nov_drag_delta_y)); "
                "nov_drag_end = x + ',' + y + ',' + nov_rect_w + ',' + nov_rect_h; } "
                "nov_dragging = false"
            )
            with html.Div(
                    v_show=("nov_show_rect", False),
                    class_="nov-rect-overlay",
                    style=_overlay_style,
                    v_on={"mousemove": _mousemove, "mouseup": _mouseup},
            ):
                _lens_left = "(nov_dragging ? (nov_drag_start_rect_x + nov_drag_delta_x) : nov_rect_x) * 100"
                _lens_bottom = "(nov_dragging ? (nov_drag_start_rect_y + nov_drag_delta_y) : nov_rect_y) * 100"
                _lens_style = (
                    "'left: ' + " + _lens_left + " + '%; bottom: ' + " + _lens_bottom + " + '%; width: ' + (Math.min(nov_rect_w, nov_rect_h) * 100) + '%; height: auto; aspect-ratio: 1/1; position: absolute; border: 4px solid rgba(0,255,100,0.95); background: transparent; box-sizing: border-box; border-radius: 50%; pointer-events: auto;'",
                    "left: 35%; bottom: 35%; width: 30%; height: auto; aspect-ratio: 1/1; position: absolute; border: 4px solid rgba(0,255,100,0.95); background: transparent; border-radius: 50%; pointer-events: auto;",
                )
                _start_drag = "if(window.novStartDrag){ $event.preventDefault(); $event.stopPropagation(); window.novStartDrag($event); }"
                with html.Div(
                        class_="nov-rect-lens",
                        style=_lens_style,
                        mousedown=_start_drag,
                ):
                    html.Div(
                        class_="nov-rect-drag-handle",
                        style="position: absolute; inset: 0; border-radius: 50%; cursor: move; z-index: 0;",
                    )
                    _btn = "cursor: pointer; display: flex; align-items: center; justify-content: center; color: rgba(255,255,255,0.95);"
                    _pm = "width: 24px; height: 24px; font-size: 0.95rem; font-weight: bold; " + _btn
                    with html.Div(
                            class_="nov-rect-controls",
                            style="position: absolute; top: 0; right: 0; transform: translateX(100%); margin-left: 0; display: flex; flex-direction: column; align-items: center; gap: 2px; background: rgba(0,0,0,0.4); border-radius: 8px; padding: 5px; pointer-events: auto; z-index: 1;",
                    ):
                        with html.Div(style=_pm, click=ctrl.nov_rect_size_minus):
                            html.Span("−")
                        with html.Div(style=_pm, click=ctrl.nov_rect_size_plus):
                            html.Span("+")
                html.Input(
                    type="text",
                    v_model=("nov_drag_live_str", ""),
                    attrs={"id": "nov-rect-drag-live", "aria-hidden": "true", "tabindex": "-1"},
                    style="position: absolute; opacity: 0; width: 0; height: 0; pointer-events: none;",
                )
                html.Input(
                    type="text",
                    v_model=("nov_drag_end", ""),
                    attrs={"id": "nov-rect-drag-end", "aria-hidden": "true", "tabindex": "-1"},
                    style="position: absolute; opacity: 0; width: 0; height: 0; pointer-events: none;",
                )
            # Flag popup: position absolute so it appears near the flag (same coord system as VTK render window)
            with html.Div(
                    v_show=("bookmark_flag_popup", False),
                    class_="bookmark-flag-popup-near-flag",
                    style=(
                            "'position: absolute; z-index: 400; left: ' + (bookmark_flag_popup_left || 0) + 'px; top: ' + (bookmark_flag_popup_top || 0) + 'px; transform: translateY(-100%); min-width: 200px; max-width: 320px; padding: 10px 12px; border-radius: 6px; background: rgba(0,0,0,0.9); color: #fff; font-size: 1rem; box-shadow: 0 2px 12px rgba(0,0,0,0.4); white-space: pre-wrap; word-break: break-word; line-height: 1.1; pointer-events: auto;'",
                    ),
            ):
                with html.Div(v_html=("bookmark_flag_popup_html", ""),
                              style="color: #fff; font-size: 1rem; line-height: 1.2; margin: 0; padding: 0;"):
                    pass
                with vuetify.VBtn(icon=True, x_small=True, click=ctrl.bookmark_close_flag_popup,
                                  style="position: absolute; top: 4px; right: 4px; color: #fff;"):
                    vuetify.VIcon("mdi-close", x_small=True)
        
        with vuetify.VBtn(
            v_if="analysis_loaded && !right_drawer_open",
            fab=True,
            large=True,
            dark=True,
            outlined=True,
            style="position: absolute; top: 16px; right: 16px; z-index: 100;",
            click="right_drawer_open = true",
        ):
            vuetify.VIcon("mdi-chart-box-outline")

        def _on_ready(**_):
            render_window.Render()
            view.update()
        ctrl.on_server_ready.add(_on_ready)
    
    return view