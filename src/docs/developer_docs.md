# BioSET Visualizer - Developer Documentation

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [State Management](#state-management)
4. [Callbacks & Controllers](#callbacks--controllers)
5. [UI Components](#ui-components)
6. [Analysis Database](#analysis-database)
7. [VTK Scene & Rendering](#vtk-scene--rendering)
8. [Custom JavaScript](#custom-javascript)
9. [Custom CSS](#custom-css)
10. [Common Tasks & Recipes](#common-tasks--recipes)

---

## Architecture Overview

BioSET Visualizer is built on [Trame](https://kitware.github.io/trame/), a Python framework for building interactive web applications with VTK. The architecture follows a reactive pattern:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        APPLICATION ARCHITECTURE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────┐      ┌─────────────┐      ┌─────────────────────────┐    │
│   │   State     │◄────►│  Callbacks  │◄────►│   VTK Scene             │    │
│   │  (state.py) │      │(callbacks.py)│      │  (scene/builder.py)    │    │
│   └──────┬──────┘      └──────┬──────┘      └───────────┬─────────────┘    │
│          │                    │                         │                   │
│          │    Reactive        │                         │                   │
│          │    Binding         │                         │                   │
│          ▼                    ▼                         ▼                   │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                     UI Components (Vuetify)                          │  │
│   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │  │
│   │  │ Left Drawer │  │   Viewer    │  │Right Drawer │                  │  │
│   │  │  - Data     │  │  (VTK 3D)   │  │  - Analysis │                  │  │
│   │  │  - Channels │  │             │  │  - Controls │                  │  │
│   │  │  - Settings │  │             │  │             │                  │  │
│   │  └─────────────┘  └─────────────┘  └─────────────┘                  │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                     Data Layer                                       │  │
│   │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │  │
│   │  │  Zarr Streamer  │  │ Analysis Loader │  │   Metadata Parser   │  │  │
│   │  │  (S3 volumes)   │  │  (.bioset DB)   │  │    (OME-XML)        │  │  │
│   │  └─────────────────┘  └─────────────────┘  └─────────────────────┘  │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
User Interaction → UI Component → Callback Function → Update State/VTK → Re-render
                                         ↓
                              Query Database/Stream Data
```

---

## Project Structure

```
src/bioset/
├── __init__.py
├── app.py                 # Application entry point
├── config.py              # Configuration dataclasses
│
├── analysis/              # Analysis data module
│   ├── __init__.py
│   └── loader.py          # .bioset database loader & queries
│
├── cache/                 # Caching utilities
│   ├── __init__.py
│   └── store.py
│
├── metadata/              # Metadata parsing
│   ├── __init__.py
│   └── parser.py          # OME-XML parser
│
├── scene/                 # VTK scene components
│   ├── __init__.py
│   ├── builder.py         # Scene construction
│   ├── heatmap.py         # Tile heatmap renderer
│   ├── meshes.py          # Mesh utilities
│   └── volumes.py         # Volume rendering
│
├── streaming/             # Data streaming
│   ├── __init__.py
│   ├── lod.py             # Level-of-detail management
│   ├── streamer.py        # Volume streaming controller
│   └── zarr_source.py     # Zarr data source
│
└── ui/                    # User interface
    ├── __init__.py
    ├── layout.py          # Main layout builder
    ├── state.py           # State initialization & handlers
    ├── callbacks.py       # Controller functions
    │
    ├── components/        # UI components
    │   ├── __init__.py
    │   ├── left_drawer.py
    │   ├── right_drawer.py
    │   ├── viewer.py
    │   ├── data_sources.py
    │   ├── channels.py
    │   └── settings.py
    │
    ├── scripts/           # Custom JavaScript
    │   ├── __init__.py
    │   └── upset.js
    │
    ├── styles/            # Custom CSS
    │   ├── __init__.py
    │   └── base.css
    │
    └── assets/            # Static assets
        └── icon.jpg
```

---

## State Management

### Overview

State is the single source of truth for the entire application. All UI components reactively bind to state variables, and changes automatically propagate to the UI.

**Location:** `src/bioset/ui/state.py`

### State Initialization

State variables are initialized in `init_state(state)`:

```python
def init_state(state):
    """Initialize all UI state with defaults."""
    
    # Use setdefault to avoid overwriting existing values
    state.setdefault("variable_name", default_value)
```

### Current State Variables

#### Application Metadata
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `trame__title` | `str` | `"BioSET"` | Browser tab title |
| `trame__favicon` | `str` | `"assets/icon.jpg"` | Favicon path |
| `trame__scripts` | `list[str]` | `[]` | External JS scripts to load |

#### Data Sources
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `zarr_url` | `str` | `"https://..."` | URL to zarr volume data |
| `metadata_url` | `str` | `"https://..."` | URL to OME-XML metadata |
| `data_loading` | `bool` | `False` | Loading spinner state |
| `data_loaded` | `bool` | `False` | Whether volume data is loaded |
| `physical_size_x` | `float` | - | Voxel spacing X (set after load) |
| `physical_size_y` | `float` | - | Voxel spacing Y (set after load) |
| `physical_size_z` | `float` | - | Voxel spacing Z (set after load) |

#### UI Layout
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `drawer` | `bool` | `True` | Left drawer visibility |
| `drawer_mini` | `bool` | `False` | Left drawer collapsed state |
| `right_drawer_open` | `bool` | `False` | Right drawer visibility |
| `data_open` | `bool` | `True` | Data section expanded |
| `settings_open` | `bool` | `False` | Settings section expanded |
| `channels_open` | `bool` | `True` | Channels section expanded |

#### Settings
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `bg_color` | `str` | `"#000000"` | Background color (hex) |
| `bg_color_dialog` | `bool` | `False` | Color picker dialog state |
| `color_swatches` | `list` | `[...]` | Color picker preset swatches |

#### Channels
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `channels` | `list[dict]` | `[]` | All available channels |
| `active_channels` | `list[int]` | `[]` | Currently selected channel IDs |

**Channel dict schema:**
```python
{
    "id": int,           # Unique channel identifier
    "name": str,         # Display name (e.g., "Hoechst")
    "color": str,        # Hex color (e.g., "#FFFFFF")
    "color_dialog": bool, # Color picker open state
    "range": [int, int]  # Intensity range [min, max]
}
```

#### Analysis
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `analysis_loaded` | `bool` | `False` | Whether .bioset file is loaded |
| `analysis_loading` | `bool` | `False` | Loading spinner state |
| `analysis_file_name` | `str` | `""` | Name of uploaded file |
| `analysis_channels` | `list[str]` | `[]` | Channel names from analysis |
| `analysis_dilation_amounts` | `list[int]` | `[]` | Available dilation values |
| `analysis_hierarchy_levels` | `list[int]` | `[]` | Available hierarchy levels |
| `analysis_volume_bounds` | `dict` | `{}` | Spatial bounds `{x, y, z}` |
| `current_dilation` | `int` | `0` | Selected dilation amount |
| `current_hierarchy_level` | `int` | `1` | Selected hierarchy level |

#### Heatmap
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `heatmap_visible` | `bool` | `True` | Heatmap visibility toggle |
| `heatmap_color` | `str` | `"#FF6600"` | Heatmap color (hex) |
| `heatmap_tile_count` | `int` | `0` | Number of tiles shown |

### Adding New State Variables

1. **Add to `init_state()`:**

```python
def init_state(state):
    # ... existing code ...
    
    # My new feature
    state.setdefault("my_new_variable", "default_value")
    state.setdefault("my_feature_enabled", False)
```

2. **Add a change handler (optional):**

```python
def register_state_change_handlers(state, ctrl):
    # ... existing handlers ...
    
    @state.change("my_new_variable")
    def on_my_variable_change(my_new_variable, **kwargs):
        print(f"Variable changed to: {my_new_variable}")
        # Call a callback or update VTK
        if hasattr(ctrl, 'handle_my_variable'):
            ctrl.handle_my_variable(my_new_variable)
```

### Accessing State in Components

In UI components, bind to state using tuples:

```python
# Read state (reactive)
vuetify.VTextField(v_model=("my_variable",))

# Read with default
vuetify.VSwitch(v_model=("my_feature_enabled", False))

# Computed expression
html.Span(v_if="my_variable.length > 0")

# Direct assignment in template
vuetify.VBtn(click="my_variable = 'new_value'")
```

---

## Callbacks & Controllers

### Overview

Callbacks are Python functions that handle user interactions and business logic. They are registered on the `ctrl` (controller) object and can be called from UI components.

**Location:** `src/bioset/ui/callbacks.py`

### Structure

```python
def register_callbacks(ctrl, state, view, streamer=None):
    """Register all controller methods."""
    
    # Internal references (closure)
    _refs = {
        "streamer": None,
        "view": view,
        "analysis_loader": None,
        "heatmap": None,
    }
    
    # Define callback functions
    def my_callback(arg1, arg2):
        """Do something."""
        # Access state
        current_value = state.my_variable
        
        # Modify state
        state.my_variable = "new_value"
        
        # Access internal refs
        streamer = _refs.get("streamer")
        
        # Update view
        if _refs["view"]:
            _refs["view"].update()
    
    # Bind to controller
    ctrl.my_callback = my_callback
```

### Current Callbacks

| Callback | Parameters | Description |
|----------|------------|-------------|
| `set_streamer(streamer)` | `VolumeStreamer` | Set streamer reference |
| `set_heatmap(heatmap)` | `HeatmapRenderer` | Set heatmap reference |
| `load_data()` | - | Load zarr volume and metadata |
| `load_analysis_file(file_info)` | `{content, name}` | Load .bioset file |
| `update_heatmap()` | - | Refresh heatmap tiles |
| `toggle_channel(channel_id)` | `int` | Add/remove channel from active |
| `update_active_channels(ids)` | `list[int]` | Sync channels with streamer |
| `reset_camera()` | - | Reset VTK camera |
| `update_background_color(hex)` | `str` | Change background color |
| `update_channel_color(id, hex)` | `int, str` | Change channel color |
| `on_channel_color_change(id, hex)` | `int, str` | Handle color picker change |

### Adding New Callbacks

1. **Define the function inside `register_callbacks()`:**

```python
def register_callbacks(ctrl, state, view, streamer=None):
    _refs = {...}
    
    # ... existing callbacks ...
    
    def my_new_callback(param1, param2=None):
        """
        Description of what this callback does.
        
        Args:
            param1: Description
            param2: Optional description
        """
        print(f"[callbacks] my_new_callback called with {param1}")
        
        try:
            # Your logic here
            result = do_something(param1)
            
            # Update state
            state.my_result = result
            
            # Refresh view if needed
            if _refs["view"]:
                _refs["view"].update()
                
        except Exception as e:
            print(f"[callbacks] Error: {e}")
            import traceback
            traceback.print_exc()
    
    # Bind to controller (IMPORTANT!)
    ctrl.my_new_callback = my_new_callback
```

2. **Call from UI component:**

```python
# Simple call
vuetify.VBtn("Click Me", click=ctrl.my_new_callback)

# With parameters
vuetify.VBtn(click=(ctrl.my_new_callback, "['value1', 'value2']"))

# With JavaScript expression
vuetify.VBtn(click=(ctrl.my_new_callback, "[item.id, $event]"))
```

### File Upload Callback Pattern

For file uploads, the file info is passed as a dict:

```python
def load_file(file_info):
    """
    Handle file upload.
    
    Args:
        file_info: Dict with keys:
            - content: Base64 encoded file content (may have data URL prefix)
            - name: Original filename
            - size: File size in bytes
            - type: MIME type
    """
    import base64
    
    content = file_info.get("content", "")
    
    # Remove data URL prefix if present
    if "," in content:
        content = content.split(",", 1)[1]
    
    file_bytes = base64.b64decode(content)
    file_name = file_info.get("name", "unknown")
    
    # Process file_bytes...
```

UI binding:

```python
vuetify.VFileInput(
    __events=["change"],
    change=(ctrl.load_file, "[$event]"),
)
```

---

## UI Components

### Overview

UI components are built using [Vuetify](https://vuetifyjs.com/en/) widgets wrapped by Trame. There are many [Vuetify 2 UI components](https://v2.vuetifyjs.com/en/getting-started/installation/) that we utilize in this project. Components are Python functions that create widget hierarchies.

**Location:** `src/bioset/ui/components/`

### Component Pattern

```python
# my_component.py
"""My component description."""

from __future__ import annotations

from trame.widgets import html, vuetify


def my_component(state, ctrl):
    """
    Create my component.
    
    Args:
        state: Trame state object
        ctrl: Controller with callbacks
    """
    with vuetify.VCard(class_="ma-2"):
        vuetify.VCardTitle("My Component")
        
        with vuetify.VCardText():
            # Bind to state
            vuetify.VTextField(
                v_model=("my_variable",),
                label="Enter value",
            )
            
            # Call callback
            vuetify.VBtn(
                "Submit",
                click=ctrl.my_callback,
            )
```

### Registering Components

1. **Export from `__init__.py`:**

```python
# components/__init__.py
from .my_component import my_component
```

2. **Import in `layout.py`:**

```python
from .components import left_drawer, right_drawer, viewer, my_component
```

3. **Add to layout:**

```python
def build_ui(server, render_window, streamer=None):
    with VAppLayout(server) as layout:
        # ... existing components ...
        my_component(state, ctrl)
```

### Common Vuetify Patterns

#### Conditional Rendering

```python
# Show/hide based on state
html.Div(v_if="condition_variable")
html.Div(v_show="condition_variable")  # Keeps in DOM, just hidden

# Complex conditions
html.Div(v_if="array.length > 0 && enabled")
```

#### Loops

```python
# v-for iteration
with vuetify.VList():
    with vuetify.VListItem(
        v_for="(item, index) in items",
        key="item.id",
    ):
        vuetify.VListItemTitle("{{ item.name }}")
```

#### Two-way Binding

```python
# v-model binds state variable
vuetify.VTextField(v_model=("my_text",))
vuetify.VSlider(v_model=("my_number",))
vuetify.VSwitch(v_model=("my_bool",))
vuetify.VSelect(v_model=("my_selection",), items=("options",))
```

#### Event Handling

```python
# Simple click
vuetify.VBtn(click=ctrl.my_callback)

# With parameters
vuetify.VBtn(click=(ctrl.my_callback, "[param1, param2]"))

# Inline state modification
vuetify.VBtn(click="my_variable = !my_variable")

# Multiple events
vuetify.VTextField(
    __events=["input", "blur"],
    input=(ctrl.on_input, "[$event]"),
    blur=ctrl.on_blur,
)
```

#### Slots (Advanced)

```python
with vuetify.VTooltip(bottom=True):
    with html.Template(v_slot_activator="{ on, attrs }"):
        vuetify.VBtn(v_bind="attrs", v_on="on", icon=True):
            vuetify.VIcon("mdi-help")
    html.Span("Tooltip text")
```

---

## Analysis Database

### Overview

The `.bioset` file is a gzipped SQLite database containing biomarker co-location analysis results.

**Location:** `src/bioset/analysis/loader.py`

### Database Schema

```
┌─────────────────────────────────────────────────────────────────┐
│                        .bioset file                             │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │  metadata   │  │  combinations   │  │       tiles         │  │
│  │  (4 rows)   │  │                 │  │                     │  │
│  └─────────────┘  └─────────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

#### Table: `metadata`

Stores global configuration as JSON.

| Column | Type | Description |
|--------|------|-------------|
| `key` | TEXT (PK) | Metadata key |
| `value` | TEXT | JSON-encoded value |

**Keys:**
- `channels`: `["Hoechst", "MART1", ...]` - All biomarker names
- `hierarchy_levels`: `[{level, tile_size, aggregation}, ...]`
- `dilation_amounts`: `[0, 4, 8]` - Available dilations
- `volume_bounds`: `{x: [0, max], y: [0, max], z: [0, max]}`

#### Table: `combinations`

Each unique biomarker combination with total overlap count.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | Auto-increment ID |
| `channels` | TEXT | Pipe-separated sorted names: `"A\|B\|C"` |
| `channel_count` | INTEGER | Number of channels |
| `dilation` | INTEGER | Dilation amount (0, 4, 8, ...) |
| `hierarchy_level` | INTEGER | Detail level (0=finest) |
| `total_count` | INTEGER | Total overlapping voxels |

**Note:** Same combination appears multiple times (once per dilation × hierarchy).

#### Table: `tiles`

Spatial distribution of each combination.

| Column | Type | Description |
|--------|------|-------------|
| `combination_id` | INTEGER (FK) | References combinations.id |
| `tile_x0` | INTEGER | Tile left edge (voxels) |
| `tile_x1` | INTEGER | Tile right edge |
| `tile_y0` | INTEGER | Tile top edge |
| `tile_y1` | INTEGER | Tile bottom edge |
| `count` | INTEGER | Overlap voxels in this tile |

#### Indices

```sql
CREATE INDEX idx_combinations_channels ON combinations(channels);
CREATE INDEX idx_combinations_dilation ON combinations(dilation);
CREATE INDEX idx_combinations_level ON combinations(hierarchy_level);
CREATE INDEX idx_combinations_dilation_level ON combinations(dilation, hierarchy_level);
CREATE INDEX idx_tiles_combination ON tiles(combination_id);
CREATE INDEX idx_tiles_spatial ON tiles(tile_x0, tile_y0);
```

### Using AnalysisLoader

```python
from bioset.analysis import AnalysisLoader

# Initialize
loader = AnalysisLoader()

# Load from file
metadata = loader.load("/path/to/file.bioset")

# Or from bytes (file upload)
metadata = loader.load_from_bytes(file_bytes)

# Check if loaded
if loader.is_loaded:
    print(loader.metadata.channels)
    print(loader.metadata.dilation_amounts)
```

### Query Methods

#### Get Top Combinations

```python
# Global top N combinations
combos = loader.get_top_combinations(
    dilation=0,
    hierarchy_level=2,
    limit=50,
    min_channels=1,
)

for combo in combos:
    print(f"{combo.channels}: {combo.total_count}")
```

#### Get Filtered Combinations

```python
# Combinations containing specific channels
combos = loader.get_filtered_combinations(
    channel_filter=["Hoechst", "MART1"],
    dilation=0,
    hierarchy_level=1,
    limit=50,
    exact_match=False,  # Contains these channels
)

# Exact match only
combos = loader.get_filtered_combinations(
    channel_filter=["Hoechst", "MART1"],
    dilation=0,
    hierarchy_level=1,
    exact_match=True,  # Exactly these channels
)
```

#### Get Tiles for Heatmap

```python
# Get spatial tiles for a combination
tiles = loader.get_combination_tiles(
    channels=["Hoechst", "MART1"],
    dilation=0,
    hierarchy_level=1,
)

for tile in tiles:
    print(f"Tile ({tile.x0}-{tile.x1}, {tile.y0}-{tile.y1}): {tile.count}")
```

#### Get Combinations at Location

```python
# What combinations exist at a specific tile?
combos = loader.get_tile_combinations(
    tile_x0=2121,
    tile_y0=1836,
    dilation=0,
    hierarchy_level=0,
    limit=20,
)
```

#### Get Dilation Curve

```python
# How does overlap change with dilation?
curve = loader.get_dilation_curve(
    channels=["Hoechst", "MART1"],
    hierarchy_level=1,
)

for point in curve:
    print(f"Dilation {point['dilation']}: {point['count']}")
```

### Raw SQL Queries

For advanced queries, access the connection directly:

```python
if loader.is_loaded:
    cursor = loader._conn.execute('''
        SELECT channels, total_count 
        FROM combinations 
        WHERE dilation = ? AND hierarchy_level = ?
          AND channel_count BETWEEN ? AND ?
        ORDER BY total_count DESC
        LIMIT ?
    ''', (0, 1, 2, 4, 100))
    
    for row in cursor:
        print(row["channels"], row["total_count"])
```

---

## VTK Scene & Rendering

### Overview

The VTK scene manages 3D rendering including volumes, meshes, and the heatmap overlay.

**Location:** `src/bioset/scene/`

### VtkScene Dataclass

```python
@dataclass
class VtkScene:
    renderer: vtkRenderer          # Main VTK renderer
    render_window: vtkRenderWindow # Render window
    interactor: vtkRenderWindowInteractor  # Mouse/keyboard handling
    streamer: Optional[VolumeStreamer] = None  # Volume streaming
    heatmap: Optional[HeatmapRenderer] = None  # Tile heatmap
```

### HeatmapRenderer

The heatmap renders analysis tiles as semi-transparent VTK cubes.

```python
from bioset.scene.heatmap import HeatmapRenderer, HeatmapConfig

# Create with custom config
config = HeatmapConfig(
    base_color=(1.0, 0.3, 0.0),  # RGB 0-1
    min_opacity=0.1,
    max_opacity=0.8,
    z_height=194.0,
    edge_visibility=True,
)

heatmap = HeatmapRenderer(renderer, config)

# Update tiles
heatmap.update_tiles(
    tiles=tile_list,           # List[TileData]
    spacing=(0.325, 0.325, 1.0),  # Voxel spacing
    color=(1.0, 0.5, 0.0),     # Optional color override
)

# Control visibility
heatmap.set_visible(True)
heatmap.clear()

# Query
tile = heatmap.get_tile_at_position(world_x, world_y)
count = heatmap.tile_count
```

### Adding Custom Actors

To add custom VTK actors (meshes, glyphs, etc.):

```python
# In scene/builder.py or a new module

import vtk

def create_my_actor():
    # Create geometry
    source = vtk.vtkSphereSource()
    source.SetRadius(50.0)
    source.SetCenter(500.0, 500.0, 50.0)
    
    # Create mapper
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(source.GetOutputPort())
    
    # Create actor
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(0.0, 1.0, 0.0)  # Green
    actor.GetProperty().SetOpacity(0.5)
    
    return actor

# Add to renderer
renderer.AddActor(create_my_actor())
```

### Refreshing the View

After VTK changes, always update the view:

```python
# In callbacks
if _refs["view"]:
    _refs["view"].update()
```

---

## Custom JavaScript

### Overview

Custom JavaScript can be injected for client-side functionality not available through Trame/Vuetify.

**Location:** `src/bioset/ui/scripts/`

### Adding a Script

1. **Create the JS file:**

```javascript
// scripts/my_script.js

// Wait for Vue to be ready
window.addEventListener('load', function() {
    console.log('My script loaded');
    
    // Access Vue app
    // window.trame.state gives access to state
    
    // Define global function callable from Python
    window.myFunction = function(param) {
        console.log('Called with:', param);
        // Modify state
        window.trame.state.set('my_variable', param);
    };
});
```

2. **Register in `scripts/__init__.py`:**

```python
# scripts/__init__.py
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent


def register_scripts(client):
    """Register all custom scripts."""
    
    # Load script content
    upset_js = (SCRIPTS_DIR / "upset.js").read_text()
    my_script_js = (SCRIPTS_DIR / "my_script.js").read_text()
    
    # Inject into page
    client.Script(upset_js)
    client.Script(my_script_js)
```

3. **Call from Python (optional):**

```python
# In a callback
from trame.widgets import client

# Execute JS
client.JSEval(code="window.myFunction('hello')")
```

### External Libraries

Add external scripts in `state.py`:

```python
def init_state(state):
    # Load external libraries
    state.trame__scripts = list(state.trame__scripts or []) + [
        "https://unpkg.com/@upsetjs/bundle",
        "https://cdn.jsdelivr.net/npm/chart.js",
    ]
```

### Communicating JS → Python

Use `window.trame.state.set()` in JavaScript:

```javascript
// JS side
window.trame.state.set('my_variable', newValue);
```

Then handle in Python with a state change handler:

```python
# Python side
@state.change("my_variable")
def on_my_variable(my_variable, **kwargs):
    print(f"JS set my_variable to: {my_variable}")
```

---

## Custom CSS

### Overview

Custom styles can be added to customize the appearance beyond Vuetify defaults.

**Location:** `src/bioset/ui/styles/`

### Adding Styles

1. **Edit `styles/base.css`:**

```css
/* styles/base.css */

/* Custom scrollbar */
::-webkit-scrollbar {
    width: 6px;
}

::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.2);
    border-radius: 3px;
}

/* Custom component styles */
.my-custom-class {
    background: linear-gradient(135deg, #1a1a2e, #16213e);
    border-radius: 8px;
    padding: 16px;
}

/* Override Vuetify */
.v-application .v-btn {
    text-transform: none;  /* Disable uppercase */
}
```

2. **Register in `styles/__init__.py`:**

```python
# styles/__init__.py
from pathlib import Path

STYLES_DIR = Path(__file__).parent


def register_styles(client):
    """Register all custom styles."""
    
    base_css = (STYLES_DIR / "base.css").read_text()
    client.Style(base_css)
```

### Using Custom Classes

```python
# In components
html.Div(class_="my-custom-class")

vuetify.VCard(classes="my-custom-class elevation-4")
```

### Dynamic Styles

For dynamic styling based on state:

```python
# Computed class binding
html.Div(
    class_=("condition ? 'class-a' : 'class-b'",)
)

# Computed style binding
html.Div(
    style=("'background-color: ' + bg_color",)
)
```

---

## Common Tasks & Recipes

### Recipe 1: Add a New Control in Right Drawer

```python
# In right_drawer.py

def right_drawer(state, ctrl):
    with vuetify.VNavigationDrawer(...):
        # ... existing code ...
        
        with html.Div(class_="px-4 py-3"):
            # Add new control section
            with html.Div(class_="mb-4"):
                html.Div("My New Control", class_="text-overline mb-2")
                
                vuetify.VSlider(
                    v_model=("my_new_value",),
                    min=0,
                    max=100,
                    label="Value",
                )
```

Don't forget to add `my_new_value` to state in `state.py`!

### Recipe 2: Add a Button That Calls Python

```python
# 1. In state.py
state.setdefault("processing", False)
state.setdefault("result", None)

# 2. In callbacks.py
def process_data():
    state.processing = True
    try:
        # Do work
        result = expensive_computation()
        state.result = result
    finally:
        state.processing = False

ctrl.process_data = process_data

# 3. In component
vuetify.VBtn(
    "Process",
    click=ctrl.process_data,
    loading=("processing",),
    disabled=("processing",),
)

html.Div(v_if="result", v_text="result")
```

### Recipe 3: File Upload with Progress

```python
# In callbacks.py
def upload_file(file_info):
    state.upload_progress = 0
    state.uploading = True
    
    try:
        import base64
        content = file_info.get("content", "")
        if "," in content:
            content = content.split(",", 1)[1]
        
        data = base64.b64decode(content)
        state.upload_progress = 50
        
        # Process data...
        process(data)
        state.upload_progress = 100
        
    finally:
        state.uploading = False

ctrl.upload_file = upload_file

# In component
vuetify.VFileInput(
    __events=["change"],
    change=(ctrl.upload_file, "[$event]"),
    loading=("uploading",),
)

vuetify.VProgressLinear(
    v_if="uploading",
    value=("upload_progress",),
)
```

### Recipe 4: Sync VTK with State Changes

```python
# In state.py
@state.change("opacity_value")
def on_opacity_change(opacity_value, **kwargs):
    if hasattr(ctrl, 'update_opacity'):
        ctrl.update_opacity(opacity_value)

# In callbacks.py
def update_opacity(value):
    heatmap = _refs.get("heatmap")
    if heatmap:
        for actor in heatmap._actors.values():
            actor.GetProperty().SetOpacity(value)
        
        if _refs["view"]:
            _refs["view"].update()

ctrl.update_opacity = update_opacity
```

### Recipe 5: Add External Data Source

```python
# 1. Create new module: src/bioset/my_data/loader.py
class MyDataLoader:
    def __init__(self):
        self.data = None
    
    def load(self, url):
        import requests
        response = requests.get(url)
        self.data = response.json()
        return self.data

# 2. In callbacks.py
_refs["my_loader"] = None

def load_my_data(url):
    from bioset.my_data import MyDataLoader
    
    if _refs["my_loader"] is None:
        _refs["my_loader"] = MyDataLoader()
    
    data = _refs["my_loader"].load(url)
    state.my_data = data
    state.my_data_loaded = True

ctrl.load_my_data = load_my_data

# 3. In state.py
state.setdefault("my_data", None)
state.setdefault("my_data_loaded", False)
```

### Recipe 6: Create Reusable Component

```python
# components/info_card.py
from trame.widgets import html, vuetify


def info_card(title, items):
    """
    Reusable info card component.
    
    Args:
        title: Card title
        items: List of (label, value_expression) tuples
    """
    with vuetify.VCard(class_="ma-2", outlined=True):
        vuetify.VCardTitle(title, class_="text-subtitle-2")
        
        with vuetify.VCardText():
            for label, value_expr in items:
                with html.Div(class_="d-flex justify-space-between"):
                    html.Span(label, class_="text-caption")
                    html.Span(f"{{{{ {value_expr} }}}}", class_="text-caption font-weight-bold")


# Usage in another component
info_card("Statistics", [
    ("Total:", "total_count"),
    ("Active:", "active_count"),
    ("Ratio:", "(active_count / total_count * 100).toFixed(1) + '%'"),
])
```

---

## Debugging Tips

### Print State Changes

```python
@state.change("*")  # Watch all state changes
def debug_state_changes(**kwargs):
    for key, value in kwargs.items():
        if not key.startswith("_"):
            print(f"[DEBUG] {key} = {value}")
```

### Inspect VTK Scene

```python
def debug_vtk():
    streamer = _refs.get("streamer")
    if streamer:
        print(f"Actors: {streamer.renderer.GetActors().GetNumberOfItems()}")
        print(f"Volumes: {streamer.renderer.GetVolumes().GetNumberOfItems()}")

ctrl.debug_vtk = debug_vtk
```

### Browser Console

State is accessible in browser console:

```javascript
// In browser dev tools
window.trame.state.get('my_variable')
window.trame.state.set('my_variable', 'test')
```

---

## Further Resources

- [Trame Documentation](https://kitware.github.io/trame/)
- [Vuetify 2 Components](https://v2.vuetifyjs.com/en/components/buttons/)
- [VTK Python Documentation](https://vtk.org/doc/nightly/html/annotated.html)
- [SQLite Documentation](https://www.sqlite.org/docs.html)