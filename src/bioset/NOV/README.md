# NOV — Algorithm

Short description of how **entropy** and **occlusion** are used to score viewing angles.

## Overview

For a box ROI we sample many camera directions on a sphere. Each direction is scored; the top 10 (with ≥30° separation) are returned as optimal views.

## Occlusion

- For each candidate view we shoot a **grid of rays** (64×64) from the camera through the ROI.
- For each ray, **front-to-back ray marching** computes per-channel visibility: intensity is sampled along the ray; opacity (alpha) and transmittance (T) are updated so that **closer tissue blocks** contributions from behind.
- The result is a **visibility vector Vis(o)** per view: how much each channel is visible along all rays, with occlusion taken into account.

So **occlusion is not a separate weight**—it is built into how Vis(o) is computed. Without it, visibility would be wrong and entropy would be computed on incorrect values.

**How occlusion affects the score:**  
Vis(o) is computed with occlusion (front blocks back). Then **p(o|v) = Vis(o) / Σ Vis**, and **H** and **max_p** (and thus the **score**) are computed from p(o|v). So occlusion directly shapes which views get high or low scores: a view where one channel is hidden behind another will have lower visibility for the hidden channel, so p(o|v) and H change, and the score reflects that. We therefore **select views that are both balanced (high H) and based on occlusion-aware visibility**.

## Entropy

- From Vis(o) we get a distribution **p(o|v) = Vis(o) / Σ Vis** (per-channel share of visibility).
- **Entropy**  
  **H = −Σ p·log(p)**  
  is high when channels are **balanced** (many visible equally), low when one dominates.
- **max_p** = max over channels of p(o|v) (dominance of one channel).

## Score

**score = entropy_weight × H − min_intensity_weight × max_p**

- Default: **entropy_weight = 1.0**, **min_intensity_weight = 0.3**.
- We **maximise** score: favour high entropy (balanced view) and penalise single-channel dominance.

### Why we use this score

We want a **“next best view”** where the user can see **all channels in balance** instead of one channel dominating the image. So we:

- **Maximise H (entropy)** → prefer views where the visibility is spread across channels (many channels contribute similarly). Low H would mean one channel takes most of the visibility.
- **Minimise max_p** (via the minus sign) → penalise views where a single channel has a very large share of visibility. That avoids “best” views that are effectively single-channel.

So we **select** the camera directions that **maximise this score**: they are the angles from which the multi-channel content in the box looks most balanced and informative.

Views are sorted by score descending; the top 10 with ≥30° angular separation are kept.
