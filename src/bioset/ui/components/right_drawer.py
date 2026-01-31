# right_drawer.py
"""Right drawer component with analysis panel."""

from __future__ import annotations

from trame.widgets import html, vuetify


def right_drawer(state):
    """Create the right drawer with analysis panel."""
    
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