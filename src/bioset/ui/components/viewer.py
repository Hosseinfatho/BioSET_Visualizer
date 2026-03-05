# viewer.py
"""VTK viewer component. Includes 2D NOV rectangle (position/size from state); +/- for size only."""

from __future__ import annotations

from trame.widgets import vtk, vuetify, html


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
            ctrl.view_update = view.update
            # 2D NOV lens: position/size from state; +/- for size only (no drag, no JS).
            with html.Div(
                v_show=("nov_show_rect", False),
                class_="nov-rect-overlay",
                style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; z-index: 50; pointer-events: none;",
            ):
                with html.Div(
                    class_="nov-rect-lens",
                    style=(
                        "'left: ' + (nov_rect_x * 100) + '%; bottom: ' + (nov_rect_y * 100) + '%; width: ' + (Math.min(nov_rect_w, nov_rect_h) * 100) + '%; height: auto; aspect-ratio: 1 / 1; position: absolute; border: 2px solid rgba(0,255,100,0.95); background: rgba(0,255,100,0.12); box-sizing: border-box; border-radius: 4px; pointer-events: none;'",
                        "left: 35%; bottom: 35%; width: 30%; height: auto; aspect-ratio: 1/1; position: absolute; border: 2px solid rgba(0,255,100,0.95); background: rgba(0,255,100,0.12); border-radius: 4px; pointer-events: none;",
                    ),
                ):
                    # Controls: - + for size only
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