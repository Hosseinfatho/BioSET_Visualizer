# NOV (Next Best View)

Scores five front viewpoints on a sphere around the focal point. Only channels currently visible in the scene are used. Views are ranked by a mesh-based visibility score.

**Ranking:** **1/5** = highest score (best view), **5/5** = lowest score. Use the arrows to step through the list.

---

**Plane:** For each candidate view, a *tangent plane* is placed at the focal point: it is perpendicular to the view direction (camera → focal) and is a square of side **2 × radius** (radius = distance from camera to focal). The plane is discretized into an **N×N mesh** (e.g. 100×100).

**Projection:** The volume’s 3D bounding box (AABB) is projected orthographically onto this plane along the view direction. For each active channel, every mesh cell that lies inside the projected footprint is marked as “hit”. So each cell gets a count: how many channels project onto it.

**Score:**  
- **Visibility** (weight 0.8): fraction of mesh cells that are hit by at least one channel → higher is better.  
- **Occlusion** (weight 0.2): fraction of cells hit by two or more channels → penalized.  
- **Score = 0.8 × (filled / total) − 0.2 × (occluded / total)**. Views with more visible area and less overlap rank higher.
