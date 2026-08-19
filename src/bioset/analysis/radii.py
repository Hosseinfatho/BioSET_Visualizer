"""The tallied radii, read from the dataset instead of hardcoded.

A radius reaches the data as an **EDT code**, never as a float comparison:
``mask(r) = edt <= floor(r / quant_um)``. Each tallied radius therefore has
three faces, and mixing them up is the sharpest edge in this package:

``requested_um``
    What the pipeline was asked for, and the only value safe to feed back
    through ``GridInfo.code_for``. This is what the public µm API carries.

``codes``
    ``floor(requested / quant_um)`` — the actual cutoff the tally rows describe.

``effective_um``
    ``(code + 1) * quant_um`` — the radius those codes really resolve to, and
    the honest number to *label* a plot with. It must never be used as a query
    value: it floors to ``code + 1``, selecting a strictly larger mask than the
    row it came from. At the last radius it floors to 255, the saturation flag
    ("at least clamp_um"), which would select most of the volume.

Older runs (``mis_full``) predate the pipeline writing any of this, hence the
fallback constant.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

# Radii of runs made before the pipeline recorded them. Only used when neither
# the zarr attrs nor meta.json carry the mapping.
FALLBACK_RADII_UM: tuple[float, ...] = (0.0, 0.5, 1.0, 1.5, 2.0)

# Snap half-window as a fraction of the median gap between adjacent radii.
# 0.15 reproduces the previous hardcoded +/-0.08 um on the old 0.5 um spacing,
# and scales to the ~1.66 um spacing of newer runs instead of becoming a dead
# zone the slider can barely hit.
SNAP_FRACTION_OF_GAP: float = 0.15


@dataclass(frozen=True)
class RadiusTable:
    """The tallied radii of one dataset, indexed by ``radius_idx``."""

    requested_um: tuple[float, ...]
    effective_um: tuple[float, ...]
    codes: tuple[int, ...]
    quant_um: float
    source: str = "unknown"          # where the mapping came from, for logging

    def __len__(self) -> int:
        return len(self.requested_um)

    @property
    def max_um(self) -> float:
        return max(self.requested_um) if self.requested_um else 0.0

    def idx_for(self, r_um: float, eps: float = 1e-6) -> Optional[int]:
        """``radius_idx`` if `r_um` is (within eps of) a tallied radius, else None."""
        for i, d in enumerate(self.requested_um):
            if abs(float(r_um) - d) <= eps:
                return i
        return None

    def nearest_idx(self, r_um: float) -> int:
        """Index of the tallied radius closest to `r_um`."""
        return min(range(len(self.requested_um)),
                   key=lambda i: abs(self.requested_um[i] - float(r_um)))

    def label_um(self, idx: int) -> float:
        """The radius to *display* for `idx` — never to query with."""
        return self.effective_um[idx]

    def snap_window_um(self) -> float:
        """Half-width of the slider's magnetic snap around each radius."""
        if len(self.requested_um) < 2:
            return 0.05
        gaps = np.diff(np.asarray(self.requested_um, dtype=float))
        return float(np.median(gaps) * SNAP_FRACTION_OF_GAP)

    # ── construction ───────────────────────────────────────

    @classmethod
    def build(
        cls,
        requested: Sequence[float],
        quant_um: float,
        levels: int = 256,
        effective: Optional[Sequence[float]] = None,
        source: str = "unknown",
    ) -> "RadiusTable":
        req = tuple(float(r) for r in requested)
        codes = tuple(int(min(np.floor(r / quant_um), levels - 1)) for r in req)
        if effective is not None and len(effective) == len(req):
            eff = tuple(float(e) for e in effective)
        else:
            eff = tuple((c + 1) * quant_um for c in codes)
        table = cls(req, eff, codes, float(quant_um), source)
        table._validate()
        return table

    def _validate(self) -> None:
        if len(set(self.codes)) != len(self.codes):
            print(f"[radii] WARNING: two radii share an EDT code ({self.codes}) — "
                  f"they select identical masks and cannot be told apart")
        if list(self.codes) != sorted(self.codes):
            print(f"[radii] WARNING: radii are not in increasing code order: {self.codes}")

    @classmethod
    def from_dataset(
        cls,
        attrs,
        results_dir: Optional[Path],
        quant_um: float,
        levels: int = 256,
    ) -> "RadiusTable":
        """Read the mapping from the dataset, preferring the zarr attrs.

        Order: zarr root attrs -> meta.json (root or tally/) -> fallback constant.
        """
        req = _seq(attrs, "dilate_um")
        if req:
            return cls.build(req, quant_um, levels,
                             effective=_seq(attrs, "dilate_um_effective"),
                             source="zarr attrs")

        meta = _read_meta(results_dir)
        if meta:
            req = _seq(meta, "dilate_um") or _indexed(meta.get("radius_index"))
            if req:
                eff = (_seq(meta, "dilate_um_effective")
                       or _indexed(meta.get("radius_index_effective")))
                return cls.build(req, quant_um, levels, effective=eff, source="meta.json")

        print("[radii] dataset records no radius mapping — falling back to "
              f"{FALLBACK_RADII_UM}; verify this matches the run")
        return cls.build(FALLBACK_RADII_UM, quant_um, levels, source="fallback constant")


def _seq(src, key) -> Optional[list]:
    """A list-valued entry from zarr attrs or a dict, or None."""
    try:
        v = src[key]
    except (KeyError, TypeError):
        return None
    if v is None or isinstance(v, (str, bytes)):
        return None
    try:
        out = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    return out or None


def _indexed(v) -> Optional[list]:
    """meta.json's ``radius_index`` shape: {"0": 0.0, "1": 1.66, ...}."""
    if not isinstance(v, dict) or not v:
        return None
    try:
        return [float(v[k]) for k in sorted(v, key=int)]
    except (KeyError, TypeError, ValueError):
        return None


def _read_meta(results_dir: Optional[Path]) -> Optional[dict]:
    """meta.json from the results root, or the copy inside tally/."""
    if results_dir is None:
        return None
    for candidate in (results_dir / "meta.json", results_dir / "tally" / "meta.json"):
        if candidate.exists():
            try:
                with open(candidate) as f:
                    return json.load(f)
            except (OSError, ValueError) as exc:
                print(f"[radii] could not read {candidate}: {exc}")
    return None
