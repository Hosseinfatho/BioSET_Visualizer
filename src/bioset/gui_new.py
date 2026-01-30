# gui.py
from __future__ import annotations

from trame.ui.vuetify import VAppLayout
from trame.widgets import html, vtk, vuetify, client

def build_ui(server, render_window, streamer=None):
    ctrl = server.controller
    state = server.state
    
    server.state.trame__title = "BioSET"
    server.state.trame__favicon = "assets/icon_new.jpg"
    
    if not hasattr(state, 'trame__scripts') or state.trame__scripts is None:
        state.trame__scripts = []
    state.trame__scripts = list(state.trame__scripts) + ["https://unpkg.com/@upsetjs/bundle"]
    
    with VAppLayout(server) as layout:
        client.Style("""
    @import url('https://fonts.googleapis.com/css2?family=Merriweather:wght@300;400;700&display=swap');

    .v-application {
      font-family: 'Merriweather', serif !important;
    }

    .v-application * {
      font-family: 'Merriweather', serif !important;
    }
    
    .brand-title { 
        font-size: 25px !important;
        line-height: 1.15 !important;
        font-weight: 700 !important;
    }
    .brand-subtitle {
        font-size: 14px !important;
        line-height: 1.1 !important;
        opacity: 0.85;
    }
    
    
    """)
        
        client.Style("""
        /* Fixed icon column => aligned midpoints + aligned text start */
        .nav-item .v-list-item__icon {
        width: 40px !important;
        min-width: 40px !important;
        margin-right: 8px !important;
        }

        /* Keep paddings consistent */
        .nav-item {
        padding-left: 8px !important;
        padding-right: 8px !important;
        }

        /* Nested row: slightly smaller + darker */
        .nav-item--nested .v-list-item__title {
        font-size: 0.92rem !important;
        opacity: 0.75;
        }
        .nav-item--nested .v-icon {
        opacity: 0.75;
        }
        """)
        
        client.Style("""
.nav-item--active {
  background: rgba(255,255,255,0.12) !important;
}
""")
        
        left_drawer(layout, state)
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
        
    return ctrl, view


    
def left_drawer(layout, state):
    state.setdefault("drawer", True)
    state.setdefault("drawer_mini", False)
    
    state.setdefault("settings_open", False)
    state.setdefault("bg_color", "#000000")
    state.setdefault("bg_color_dialog", False)
    
    state.setdefault("channels_open", True)
    
    def expand_drawer():
        if state.drawer_mini:
            state.drawer_mini = False
    
    def toggle_mini():
        state.drawer_mini = not state.drawer_mini
        
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
                vuetify.VImg(src="assets/icon_new.jpg", contain=True)
            with vuetify.VListItemContent(classes="d-flex justify-center flex-column"):
                vuetify.VListItemTitle("BioSET", classes="brand-title")
                vuetify.VListItemSubtitle("Visualizer", classes="brand-subtitle")
                
        vuetify.VDivider()
        
        # zarr data sources
        # todo
        
        # settings
        with vuetify.VList(dense=True, nav=True):
            with vuetify.VListItem(class_=("settings_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"), link=True, ripple=True, click=toggle_settings):
                with vuetify.VListItemIcon():
                    vuetify.VIcon("mdi-cog-outline", style="font-size: 30px;")
                with vuetify.VListItemContent(v_if="!drawer_mini"):
                    vuetify.VListItemTitle("Settings", classes="text-body-1")

            with vuetify.VExpandTransition():
                with html.Div(v_show=("settings_open", False)):
                    with vuetify.VListItem(class_="nav-item nav-item--nested", link=True, ripple=True):
                        with vuetify.VListItemIcon():
                            vuetify.VIcon("mdi-lightbulb-outline", style="font-size: 25px;")
                        with vuetify.VListItemContent(v_if="!drawer_mini"):
                            vuetify.VListItemTitle("Toggle Theme", classes="text-body-2")
                            
                    with vuetify.VListItem(class_="nav-item nav-item--nested", link=True, ripple=True, click=open_bg_picker):
                        with vuetify.VListItemIcon():
                            vuetify.VIcon("mdi-circle-outline", style=("`font-size: 25px; color: ${bg_color};`",))
                        with vuetify.VListItemContent(v_if="!drawer_mini"):
                            vuetify.VListItemTitle("Bg-color", classes="text-body-2")
                        
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
                    
                    with vuetify.VListItem(class_="nav-item nav-item--nested", link=True, ripple=True):
                        with vuetify.VListItemIcon():
                            vuetify.VIcon("mdi-crop-free", style="font-size: 25px;")
                        with vuetify.VListItemContent(v_if="!drawer_mini"):
                            vuetify.VListItemTitle("Reset Camera", classes="text-body-2")
            
                        
        vuetify.VDivider()

        # channels
        channels = [(0,"Hoechst"), (3,"MART1"), (13,"pan-Cytokeratin")]

        with vuetify.VList(dense=True, nav=True):
            with vuetify.VListItem(class_=("channels_open ? 'nav-item nav-item--active' : 'nav-item'", "nav-item"), link=True, ripple=True, click=toggle_channels):
                with vuetify.VListItemIcon():
                    vuetify.VIcon("mdi-layers-triple-outline", style="font-size: 30px;")
                with vuetify.VListItemContent(v_if="!drawer_mini"):
                    vuetify.VListItemTitle("Channels", classes="text-body-1")
            
            with vuetify.VExpandTransition():
                with html.Div(v_show=("channels_open", False), class_="px-2 pb-2"):
                    for ch_idx, ch_name in channels:
                        channel_row(
                            state=state,
                            name=ch_name,
                            index=ch_idx,
                            isActive=f"ch{ch_idx}_active",
                            color=f"ch{ch_idx}_color",
                            opacity=f"ch{ch_idx}_opacity",
                        )
        

def channel_row(state, name, index, isActive, color, opacity):
    # defaults
    state.setdefault(isActive, True)
    state.setdefault(color, "#00FFFF")
    state.setdefault(opacity, [0, 100])
    state.setdefault(f"ch{index}_color_dialog", False)

    def open_color():
        state[f"ch{index}_color_dialog"] = True

    def close_color():
        state[f"ch{index}_color_dialog"] = False

    with vuetify.VListItem(
        dense=True,
        class_="channel-row px-2",
        style="min-height: 44px;",
    ):
        # channel toggle
        with vuetify.VListItemAction(class_="mr-1"):
            vuetify.VSwitch(
                v_model=(isActive, True),
                hide_details=True,
                dense=True,
                inset=True,
                class_="ma-0 pa-0",
            )

        with vuetify.VListItemContent(class_="py-0"):
            with html.Div(class_="d-flex align-center"):
                # color pick
                with vuetify.VBtn(icon=True, x_small=True, class_="mr-2", click=open_color):
                    with html.Div(style="position: relative; width: 18px; height: 18px;"):
                        vuetify.VIcon(
                            "mdi-circle",
                            style=(f"`position:absolute; left:0; top:0; font-size: 16px; color: ${{{color}}};`",),
                        )
                        vuetify.VIcon(
                            "mdi-circle-outline",
                            style="position:absolute; left:0; top:0; font-size: 16px; color: white;",
                        )

                # name + index
                html.Div(
                    f"{index} · {name}",
                    class_="channel-name text-body-2",
                    style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 160px;",
                )

            # opacity slider 
            vuetify.VRangeSlider(
                v_model=(opacity, [0, 100]),
                min=0,
                max=100,
                step=1,
                dense=True,
                hide_details=True,
                track_color="grey",
                class_="mt-0 pt-0",
                style="height: 22px;",
            )

    # color picker dialog 
    with vuetify.VDialog(
        v_model=(f"ch{index}_color_dialog", False),
        max_width=320,
    ):
        with vuetify.VCard():
            with vuetify.VCardTitle(class_="py-2"):
                html.Span(f"{index} · {name}", style="font-size: 14px;")
            with vuetify.VCardText(class_="pt-0"):
                vuetify.VColorPicker(
                    v_model=(color, "#00FFFF"),
                    mode="hexa",
                    hide_mode_switch=True,
                    hide_inputs=True,
                )
            with vuetify.VCardActions():
                vuetify.VSpacer()
                with vuetify.VBtn(text=True, click=close_color):
                    html.Span("Close")
                    
                    
def right_drawer(state):
    state.right_drawer_open = True
    
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
    client.Script(r"""
            (function initUpSet(){
  const container = document.getElementById('upset-container');
  if (!container) { setTimeout(initUpSet, 100); return; }

  // Wait until UpSetJS is actually available
  if (!window.UpSetJS) { 
    console.warn("UpSetJS not loaded yet; retrying...");
    setTimeout(initUpSet, 200); 
    return; 
  }

  // Wait until trame state bridge exists
  if (!window.trame || !window.trame.state) {
    console.warn("window.trame.state not available yet; retrying...");
    setTimeout(initUpSet, 200);
    return;
  }

  const elems = [
    { name: 'E1', sets: ['Ch1'] },
    { name: 'E2', sets: ['Ch1', 'Ch2'] },
    { name: 'E3', sets: ['Ch1', 'Ch2'] },
    { name: 'E4', sets: ['Ch1', 'Ch2', 'Ch3'] },
  ];

  const { sets, combinations } = UpSetJS.extractCombinations(elems);

  container.innerHTML = ""; // avoid double-render on hot reload
  UpSetJS.render(container, {
    sets,
    combinations,
    width: 330,
    height: 280,
    theme: 'dark',
    onClick: (set) => {
      if (!set) return;

      // Send to Python by writing into trame state
      window.trame.state.upset_click = {
        name: set.name,
        size: set.cardinality,
        ts: Date.now(),
      };

      // Some trame builds need an explicit flush; call if it exists
      if (window.trame.flushState) window.trame.flushState();
      if (window.trame.pushState) window.trame.pushState();
    },
  });

  console.log("UpSet rendered; clicks will update state.upset_click");
})();
        """)

