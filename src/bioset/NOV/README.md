# NOV (Next Best View)

We maximize **Score = α × Visible_ROI_Area − β × Occlusion** over 18 candidate camera positions on a sphere; the view with highest score is chosen first (**α = β = 0.5**). Occlusion = total XY voxel area minus visible area (hidden region). You can step through all candidates with the prev/next arrows in Settings.

**Sphere (viewpoints):** Centered at the camera focal point, radius = camera distance. **Theta** in **3** steps (45°, 90°, 135°); **phi** in **6** steps (60°). Total **18** viewpoints—these are the candidate camera positions we score and sort.

**How we compute score:** (1) *Visible_ROI_Area*: for **each** of the 18 viewpoints we place the camera, then cast rays from a **5×5 grid** on the screen (25 rays per view) through the volume, intersect with front/back z-planes, get the visible XY rectangle in voxel space → area = width×height. (2) *Occlusion*: hidden voxel area = total volume XY area − visible ROI area; penalized with β=0.5.
