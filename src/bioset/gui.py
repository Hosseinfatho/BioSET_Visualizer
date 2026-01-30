# gui.py
from __future__ import annotations

from trame.ui.vuetify import VAppLayout
from trame.widgets import html, vtk, vuetify, client


def build_ui(server, render_window, streamer=None):
    ctrl = server.controller
    state = server.state

    if not hasattr(state, 'trame__scripts') or state.trame__scripts is None:
        state.trame__scripts = []
    state.trame__scripts = list(state.trame__scripts) + ["https://unpkg.com/@upsetjs/bundle"]

    state.upset_click = None  

    @state.change("upset_click")
    def _on_upset_click(upset_click, **_):
        if upset_click is None:
            return
        print("[UpSet Click]", upset_click)
        
    # Drawer state
    state.drawer_mini = False
    state.right_drawer_open = True
    
    # Channel states - TEMP!
    state.ch1_visible = True
    state.ch1_color = "#00FFFF"
    state.ch1_opacity = 80
    
    state.ch2_visible = True
    state.ch2_color = "#FF00FF"
    state.ch2_opacity = 80
    
    state.ch3_visible = True
    state.ch3_color = "#FFFF00"
    state.ch3_opacity = 80
    
    # Selected channel 
    state.selected_channel = None
    
    @ctrl.trigger("on_upset_click")
    def on_upset_click(selection):
        print(f"[UpSet Click] {selection}")

    with VAppLayout(server) as layout:
        left_drawer()
        right_drawer()
        
        # VTK RENDERER
        with layout.root:
            #client.Script(src="https://cdn.jsdelivr.net/npm/@upsetjs/bundle@1.11.0/dist/upsetjs.umd.production.min.js")
            #client.Script(src="https://unpkg.com/@upsetjs/bundle")
            
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


def channel_item(name: str, channel_key: str, visible_var: str, color_var: str, opacity_var: str):

    with vuetify.VSheet(
        classes="mb-2 pa-2 rounded",
        color="rgba(255,255,255,0.05)",
        style=(f"selected_channel === '{channel_key}' ? 'outline: 2px solid #1976D2; outline-offset: -2px;' : ''",),
        click=f"selected_channel = selected_channel === '{channel_key}' ? null : '{channel_key}'",
    ):
        with vuetify.VRow(align="center", dense=True, classes="mb-1"):
            # Color picker
            with vuetify.VCol(cols="auto", classes="pa-0"):
                with vuetify.VMenu(offset_y=True, close_on_content_click=False):
                    with vuetify.Template(v_slot_activator="{ on, attrs }"):
                        with vuetify.VSheet(
                            v_bind="attrs",
                            v_on="on",
                            rounded="circle",
                            width=16,
                            height=16,
                            style=(f"`background-color: ${{{color_var}}}; cursor: pointer;`",),
                        ):
                            pass  
                    vuetify.VColorPicker(
                        v_model=(color_var,),
                        hide_inputs=True,
                        show_swatches=True,
                        flat=True,
                    )
            
            # Name
            with vuetify.VCol(classes="pa-0 pl-2"):
                html.Span(name, classes="white--text text-body-2")
            
            # Visibility toggle
            with vuetify.VCol(cols="auto", classes="pa-0"):
                vuetify.VBtn(
                    icon=True,
                    x_small=True,
                    plain=True,
                    click=(f"event.stopPropagation(); {visible_var} = !{visible_var}",),
                )
                vuetify.VIcon(
                    (f"{visible_var} ? 'mdi-eye' : 'mdi-eye-off'",),
                    small=True,
                    color=(f"{visible_var} ? 'white' : 'grey'",),
                    style="pointer-events: none; position: relative; right: 28px;",
                )
        
        # 
        vuetify.VSlider(
            v_model=(opacity_var,),
            min=0,
            max=100,
            dense=True,
            hide_details=True,
            track_color="grey darken-3",
            thumb_size=12,
            disabled=(f"!{visible_var}",),
            classes="ma-0",
        )


def left_drawer():
    with vuetify.VNavigationDrawer(
        app=True,
        permanent=True,
        mini_variant=("drawer_mini",),
        mini_variant_width=48,
        width=200,
        dark=True,
        color="rgba(18, 18, 18, 0.7)",
    ):
        # Logo and title
        with vuetify.VListItem(
            classes="px-2 py-3",
            style="min-height: 48px;",
            click="drawer_mini = !drawer_mini",
        ):
            with vuetify.VListItemIcon(classes="my-0 mr-2 align-self-center"):
                vuetify.VImg(
                    src="assets/icon.svg",
                    contain=True,
                    max_width=28,
                    max_height=28,
                )
            with vuetify.VListItemContent(classes="py-0"):
                vuetify.VListItemTitle("BioSET", classes="text-subtitle-1 font-weight-medium")
        
        vuetify.VDivider()
        
        # Channels
        with vuetify.VSheet(
            v_show=("!drawer_mini",),
            color="transparent",
            classes="pa-3",
        ):
            with vuetify.VRow(classes="mb-2"):
                with vuetify.VCol(classes="pa-0"):
                    html.Span("Channels", classes="text-overline grey--text")
            
            channel_item("Channel 1", "ch1", "ch1_visible", "ch1_color", "ch1_opacity")
            channel_item("Channel 2", "ch2", "ch2_visible", "ch2_color", "ch2_opacity")
            channel_item("Channel 3", "ch3", "ch3_visible", "ch3_color", "ch3_opacity")
        
        vuetify.VSpacer()
        
        # Other stuff
        # vuetify.VDivider()
        # with vuetify.VList(dense=True, nav=True, classes="py-1"):
        #     with vuetify.VListItem(link=True):
        #         with vuetify.VListItemIcon(classes="mr-3"):
        #             vuetify.VIcon("mdi-cog-outline", size=20)
        #         with vuetify.VListItemContent():
        #             vuetify.VListItemTitle("Settings", classes="text-body-2")
 
        
def right_drawer():
    with vuetify.VNavigationDrawer(
        v_model=("right_drawer_open",),
        app=True,
        right=True,
        width=350,
        dark=True,
        color="rgba(18, 18, 18, 0.7)",
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

