# left_drawer.py
"""Left drawer component that assembles all sections."""

from __future__ import annotations

from trame.widgets import vuetify

from .data_sources import data_sources_section
from .settings import settings_section
from .channels import channels_section


def left_drawer(state, ctrl):
    """Create the left navigation drawer with all sections."""
    
    def toggle_mini():
        state.drawer_mini = not state.drawer_mini
    
    with vuetify.VNavigationDrawer(
         v_model=("drawer", True),
        mini_variant=("drawer_mini", False),
        mini_variant_width=100,
        width=250,
        permanent=True,
        app=True,
        dark=True,
        expand_on_hover=False,
        class_=("drawer_mini ? 'drawer-mini-lock' : ''", ""),
        color="rgba(18, 18, 18, 0.6)",
    ):
        # Logo and title
        with vuetify.VListItem(click=toggle_mini, style="padding-left: 5px;", dense=True):
            with vuetify.VListItemAvatar(size=60, classes="d-flex justify-center"):
                vuetify.VImg(src="assets/icon.jpg", contain=True)
            with vuetify.VListItemContent(classes="d-flex justify-center flex-column"):
                vuetify.VListItemTitle("BioSET", classes="brand-title")
                vuetify.VListItemSubtitle("Visualizer", classes="text-overline")
        
        vuetify.VDivider()
        
        # Data Sources
        data_sources_section(state, ctrl)
        
        vuetify.VDivider()
        
        # Settings
        settings_section(state, ctrl)
        
        vuetify.VDivider()
        
        # Channels
        channels_section(state, ctrl)