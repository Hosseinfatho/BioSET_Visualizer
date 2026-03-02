# NOV.py – Function list: inputs and outputs

## Module-level functions (top of file)

| # | Function | Input | Output | Usage |
|---|----------|--------|--------|--------|
| 1 | `_make_nov_no_right_style()` | — | `vtkInteractorStyle` or `None` | Internal: style that ignores right-click while box is visible |
| 2 | `min_box_side_for_component(component)` | `component: int` (LOD level) | `float` min box side `(comp+1)*3` | Internal + possibly from outside |
| 3 | `box_circum_radius(length, width, depth)` | Box dimensions | `float` distance from center to corner; diameter = 2× this | Internal (apply_cam, run_nov_for_box, recompute) |
| 4 | `cube_size_from_circum_radius(radius)` | `radius: float` | `float` cube side length for given circum_radius | Internal (nov_toggle initial box size) |
| 5 | `box_corners(center, length, width, depth)` | Center + dimensions | `List[Tuple[float,float,float]]` 8 corners | Internal (pins and corner-drag) |
| 7 | `_rad(d)` | Angle in degrees | Angle in radians | Internal (degree→radian) |
| 8 | `_norm3(v)` | 3D vector | Unit 3D vector | Internal (math) |
| 9 | `_cross(a, b)` | Two 3D vectors | 3D cross product | Internal (compute_view_score_mesh) |
| 10 | `_dot(a, b)` | Two 3D vectors | Dot product (scalar) | Internal (compute_view_score_mesh) |
| 11 | `_hull2(points)` | List of 2D points | Convex hull 2D points | Internal (compute_view_score_mesh) |
| 12 | `_in_poly(q, poly)` | Point 2D, polygon 2D | `bool` inside/outside | Internal (compute_view_score_mesh) |
| 13 | `_aabb_corners(b)` | AABB (x0,x1,y0,y1,z0,z1) | List of 8 3D corners | Internal (compute_view_score_mesh) |
| 14 | `aabb_from_center_radius(center, radius)` | Center, radius | `Tuple` (xmin,xmax,ymin,ymax,zmin,zmax) | Internal + export |
| 15 | `bounds_intersect(vol, box)` | Two AABBs | Intersection AABB or (0,0,0,0,0,0) | Internal + export |
| 16 | `camera_position_at_radius(center, radius, theta_deg, phi_deg)` | Center, radius, two angles (deg) | `List[float]` camera position [x,y,z] | Internal + export |
| 17 | `view_up_for_angle(theta_deg, phi_deg)` | Two angles (deg) | `List[float]` viewUp vector [x,y,z] | Internal + export |
| 18 | `compute_view_score_mesh(...)` | camera_pos, center, view_up, view_radius, bounds_world, num_channels + optional | `float` score 0–1 | Internal (only in compute_best_views) + export |
| 19 | `normalize_scores(scores)` | `list[float]` | `list[float]` normalized with max=1 | Internal + export |
| 20 | `compute_best_views(center, view_radius, volume_bounds, num_channels, ...)` | Center, radius, volume bounds, channel count + optional | `List[dict]` camera candidates with camera/score/... | Internal + export |
| 21 | `build_nov_sphere_svg(sphere_xy, current_index)` | List of (x,y) for 10 points, current index | `str` SVG sphere mini-map | Internal (state UI) + possibly from outside |

---

## Functions inside `register_nov_callbacks(ctrl, state, _refs)` (internal and assigned to ctrl)

| # | Function | Input | Output | Role |
|---|----------|--------|--------|------|
| 22 | `_ensure_nov_box_actor()` | — | `(source, actor)` or (None, None) | Create/return green box VTK source and actor |
| 23 | `_ensure_nov_corner_pins()` | — | List of 8 (sphere_source, actor) | Create/return corner pins |
| 24 | `_update_nov_box(center, length, width, depth, visible)` | Center, dimensions, visible | — | Show/hide box and pins, clamp to bounds |
| 25 | `nov_hide_box()` | — | — | Remove box and close NOV |
| 26 | `apply_cam(cam_dict)` | Candidate dict with camera key | — | Apply camera to NOV popup (2× circum_radius = diameter) |
| 27 | `display_to_display_coords(renderer, x, y)` | renderer, x,y (e.g. 0–1) | `(dx, dy)` or (None, None) | Convert to VTK display coords |
| 28 | `pick_box_or_pin(renderer, x, y)` | renderer, x,y | `("pin", i)` or `("box",)` or None | Detect click on pin or box |
| 29 | `ray_plane_intersection(renderer, x, y, plane_origin, plane_normal)` | renderer, x,y, plane point and normal | 3D point or None | Box move/resize via drag |
| 30 | `get_lod(s)` | streamer | `(component, active_channels)` | LOD and active channels |
| 31 | `run_nov_for_box(center, length, width, depth)` | Center and box dimensions | — | Compute candidates, update state and box and popup camera |
| 32 | `_volume_center_bounds(streamer)` | streamer | `(center_list, bounds_tuple)` | Volume center and bounds for clamping |
| 33 | `_clear_nov_panel_state()` | — | — | Clear NOV panel state |
| 34 | `nov_toggle()` | — | — | Turn NOV/box on or off |
| 35 | `_parse_xy(*args)` | Click/drag args | `(x, y)` or (None, None) | Extract x,y from event |
| 36 | `nov_handle_click(*args)` | Click event | — | Start pin drag or box move |
| 37 | `nov_handle_drag(*args)` | Drag event | — | Move box or resize via pin |
| 38 | `nov_handle_wheel(*args)` | Scroll delta | — | Move box center along Z |
| 39 | `nov_handle_release(*args)` | — | — | End drag and optionally run_nov_for_box |
| 40 | `recompute()` | — | — | Recompute candidates after box change |
| 41 | `switch(step)` | +1 or -1 | — | Previous/next candidate and apply camera |
| 42 | `nov_set()` | — | — | Open popup and sync NOV volumes |
| 43 | `nov_reset()` | — | — | Close popup and clear box |
| 44 | `nov_refresh_box_display()` | — | — | Refresh box on scene (e.g. after bookmark restore) |

---

## Dependency overview

- **Scoring and candidates:** `compute_best_views` → `compute_view_score_mesh` → `_norm3`, `_cross`, `_dot`, `_aabb_corners`, `_hull2`, `_in_poly`; (geometry-only path) does not use `sample_channels_at_world`.
- **Box and pins:** `_update_nov_box` → `_ensure_nov_box_actor`, `_ensure_nov_corner_pins`, `box_corners`, `min_box_side_for_component`, `get_lod`, `_volume_center_bounds`.
- **User interaction:** `nov_handle_click` → `pick_box_or_pin`, `box_corners`.  
  `nov_handle_drag` → `ray_plane_intersection`, `_volume_center_bounds`, `_update_nov_box`, `get_lod`, `min_box_side_for_component`.  
  `nov_handle_release` → `run_nov_for_box`.
- **Popup camera:** `apply_cam` uses `2 * box_circum_radius(L,W,D)` and box dimensions from state.

---

## Functions used in only one place (candidates for removal if desired)

- `cube_size_from_circum_radius` — only in `nov_toggle`. Can be removed or simplified if initial box size logic is simplified.
- `display_to_display_coords` — only in `pick_box_or_pin` and `ray_plane_intersection`. Needed if both are kept.
- `_volume_center_bounds` — used in several places for clamping. Needed unless clamp logic is removed or simplified.

If you specify which group to trim (e.g. only exported functions, or only math/geometry helpers), a more precise remove/merge plan can be suggested.
