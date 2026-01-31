# gui.py
from __future__ import annotations

from trame.ui.vuetify import VAppLayout
from trame.widgets import html, vtk, vuetify, client

from .styles import register_styles
from .scripts import register_scripts
from .state import init_state, register_state_change_handlers
from .callbacks import register_callbacks

def build_ui(server, render_window, streamer=None):
    ctrl = server.controller
    state = server.state
    
    init_state(state)

    with VAppLayout(server) as layout:
        register_styles(client)

        left_drawer(state, ctrl)
        right_drawer(state)
        
        # VTK RENDERER
        with layout.root:
            
            with vuetify.VContainer(
                fluid=True,
                classes="pa-0 fill-height",
            ):
                view = vtk.VtkRemoteView(render_window, interactive_ratio=1.0)
                
                def _on_ready(**_):
                    render_window.Render()
                    view.update()
                ctrl.on_server_ready.add(_on_ready)
                
            upset_plot(client)

    register_callbacks(ctrl, state, view, streamer)
    register_state_change_handlers(state, ctrl)
        
    return ctrl, view


    
def left_drawer(state, ctrl):
    
    def expand_drawer():
        if state.drawer_mini:
            state.drawer_mini = False
    
    def toggle_mini():
        state.drawer_mini = not state.drawer_mini
        
    def toggle_data():
        state.data_open = not state.data_open
        
    def toggle_settings():
        state.settings_open = not state.settings_open
        
    def open_bg_picker():
        state.bg_color_dialog = True

    def close_bg_picker():
        state.bg_color_dialog = False
        
    def toggle_channels():
        state.channels_open = not state.channels_open

        
    with vuetify.VNavigationDrawer(
            v_model=("drawer", True),
            mini_variant=("drawer_mini", False),
            mini_variant_width=65,     
            width=250,                  
            permanent=True,
            app=True,
            dark=True,
            color="rgba(18, 18, 18, 0.6)"
            
        ):
        # logo and title
        with vuetify.VListItem(click=toggle_mini, style="padding-left: 5px;",dense=True):
            with vuetify.VListItemAvatar(size=60,classes="d-flex justify-center"):
                vuetify.VImg(src="assets/icon.jpg", contain=True)
            with vuetify.VListItemContent(classes="d-flex justify-center flex-column"):
                vuetify.VListItemTitle("BioSET", classes="brand-title")
                vuetify.VListItemSubtitle("Visualizer", classes="brand-subtitle")
                
        vuetify.VDivider()
        
       
        with vuetify.VList(dense=True, nav=True):
            with vuetify.VListItem(class_=("data_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"), link=True, ripple=True, click=toggle_data):
                with vuetify.VListItemIcon():
                    vuetify.VIcon("mdi-upload-box-outline", style="font-size: 30px;")
                with vuetify.VListItemContent(v_if="!drawer_mini"):
                    vuetify.VListItemTitle("Data Sources", classes="text-overline")
            
            with vuetify.VExpandTransition():
                with html.Div(v_show=("data_open", False)):
                    with vuetify.VListItem(class_="nav-item nav-item--nested"):
                        with vuetify.VListItemIcon(v_if="drawer_mini"):
                            vuetify.VIcon("mdi-radiobox-marked", style="font-size: 25px;")
                        with vuetify.VListItemContent(v_if="!drawer_mini",class_="mb-0 pb-0"):
                            vuetify.VTextField(v_model=("zarr_url", ""), label="Zarr URL", placeholder="https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/0", dense=True, clearable=True)
                            
                    with vuetify.VListItem(class_="nav-item nav-item--nested"):
                        with vuetify.VListItemIcon(v_if="drawer_mini"):
                            vuetify.VIcon("mdi-radiobox-marked", style="font-size: 25px;")
                        with vuetify.VListItemContent(v_if="!drawer_mini",class_="mt-0 pt-0 mb-0 pb-0"):
                            vuetify.VTextField(v_model=("metadata_url", ""), label="Metadata URL", placeholder="https://lsp-public-data.s3.amazonaws.com/biomedvis-challenge-2025/Dataset1-LSP13626-melanoma-in-situ/OME/METADATA.ome.xml", dense=True, clearable=True)

                    with vuetify.VListItem(class_="nav-item nav-item--nested"):
                        with vuetify.VListItemContent(v_if="!drawer_mini", class_="mt-0 pt-0"):
                            vuetify.VBtn(
                                "Load Data",
                                click=ctrl.load_data,
                                loading=("data_loading", False),
                                disabled=("data_loading", False),
                                block=True,
                                small=True,
                                color="primary",
                            )
        
        
        vuetify.VDivider()
        
        # settings
        with vuetify.VList(dense=True, nav=True):
            with vuetify.VListItem(class_=("settings_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"), link=True, ripple=True, click=toggle_settings):
                with vuetify.VListItemIcon():
                    vuetify.VIcon("mdi-cog-outline", style="font-size: 30px;")
                with vuetify.VListItemContent(v_if="!drawer_mini"):
                    vuetify.VListItemTitle("Settings", classes="text-overline")

            with vuetify.VExpandTransition():
                with html.Div(v_show=("settings_open", False)):
                    with vuetify.VListItem(class_="nav-item nav-item--nested", link=True, ripple=True):
                        with vuetify.VListItemIcon():
                            vuetify.VIcon("mdi-lightbulb-outline", style="font-size: 25px;")
                        with vuetify.VListItemContent(v_if="!drawer_mini"):
                            html.Span("Toggle Theme")
                            
                    with vuetify.VListItem(class_="nav-item nav-item--nested", link=True, ripple=True, click=open_bg_picker):
                        with vuetify.VListItemIcon():
                            vuetify.VIcon("mdi-format-color-fill", style=("`font-size: 25px; background-color: ${bg_color};`",))
                        with vuetify.VListItemContent(v_if="!drawer_mini"):
                            html.Span("Background Color")
                        
                    with vuetify.VDialog(v_model=("bg_color_dialog", False), max_width=320):
                        with vuetify.VCard():
                            with vuetify.VCardTitle():
                                html.Span("Background color")
                            with vuetify.VCardText():
                                vuetify.VColorPicker(
                                    v_model=("bg_color", "#121212"),
                                    hide_inputs=True,
                                    hide_mode_switch=True,
                                    mode="hexa",
                                )
                            with vuetify.VCardActions():
                                vuetify.VSpacer()
                                with vuetify.VBtn(text=True, click=close_bg_picker):
                                    html.Span("Close")    
                    
                    with vuetify.VListItem(class_="nav-item nav-item--nested", link=True, ripple=True, click=ctrl.reset_camera):
                        with vuetify.VListItemIcon():
                            vuetify.VIcon("mdi-crop-free", style="font-size: 25px;")
                        with vuetify.VListItemContent(v_if="!drawer_mini"):
                            html.Span("Reset Camera")
            
                        
        vuetify.VDivider()

        # channels
        channels = [
            (0, "Hoechst", "#00FFFF"),
            (1, "MART1", "#FF00FF"),
            (2, "Pan-Cytokeratin", "#FFFF00"),
        ]

        with vuetify.VList(dense=True, nav=True):
            with vuetify.VListItem(class_=("channels_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"), link=True, ripple=True, click=toggle_channels):
                with vuetify.VListItemIcon():
                    vuetify.VIcon("mdi-layers-triple-outline", style="font-size: 30px;")
                with vuetify.VListItemContent(v_if="!drawer_mini"):
                    vuetify.VListItemTitle("Channels", classes="text-overline")
            
            with vuetify.VExpandTransition():
                with html.Div(v_show=("channels_open", False), class_="px-2 pb-2"):
                    with vuetify.VListItemGroup(multiple=True):
                        for idx, ch_name, _ in channels:
                            channel_item(state, idx, ch_name)
        
def channel_item(state, idx, name):
    color_key = f"ch{idx}_color"
    dialog_key = f"ch{idx}_color_dialog"
    

    with vuetify.VListItem(dense=True, class_="px-2 py-1 mb-1"):
        
        with html.Div(class_="mr-2 channel-chip"):
            
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
    
            
                    
def right_drawer(state):

    with vuetify.VNavigationDrawer(
        v_model=("right_drawer_open",),
        app=True,
        right=True,
        width=350,
        dark=True,
        color="rgba(18, 18, 18, 0.6)",
    ):
        with vuetify.VListItem(classes="px-3 py-2"):
            vuetify.VListItemTitle("Analysis", classes="text-subtitle-1")
            vuetify.VSpacer()
            with vuetify.VBtn(icon=True, small=True, click="right_drawer_open = false"):
                vuetify.VIcon("mdi-close", small=True)
        
        vuetify.VDivider()
        html.Div(id="upset-container", style="width: 100%; height: 300px; padding: 8px;")
        
        
def upset_plot(client):
    register_scripts(client)
