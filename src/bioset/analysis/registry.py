"""Channel registry: index <-> display-name mapping and inclusion filtering.

Channel identity is the integer index into the analysis store's channel axis;
names are display labels only (the panel repeats "Hoechst" across rounds and
carries "(do not use)" acquisitions). All name<->index<->fingerprint-bit
translation for the analysis backend lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .constants import DO_NOT_USE_MARKER


@dataclass
class ChannelEntry:
    index: int
    raw_name: str
    display_name: str
    included: bool
    excluded_reason: Optional[str] = None  # "do_not_use" | "duplicate" | None


class ChannelRegistry:
    """Registry over the analysis store's channel list.

    Exclusion rules (current defaults; a future UI toggle can re-include):
      1. Channels whose name contains "(do not use)" are excluded.
      2. Of channels sharing the same remaining name (Hoechst x15), only the
         first occurrence (lowest index) is included.

    After filtering, display names are unique, so the existing name-keyed UI
    plumbing (plot data, selections, upset clicks) keeps working unchanged.
    """

    def __init__(self, raw_names: Sequence[str]):
        self.entries: list[ChannelEntry] = []
        seen: set[str] = set()
        for i, raw in enumerate(raw_names):
            name = str(raw)
            if DO_NOT_USE_MARKER in name:
                self.entries.append(ChannelEntry(i, name, name, False, "do_not_use"))
                continue
            if name in seen:
                self.entries.append(ChannelEntry(i, name, name, False, "duplicate"))
                continue
            seen.add(name)
            self.entries.append(ChannelEntry(i, name, name, True))
        self._rebuild_lookup()

    def _rebuild_lookup(self):
        self._index_by_name = {e.display_name: e.index for e in self.entries if e.included}

    # ── basics ──────────────────────────────────────────────

    @property
    def n_channels(self) -> int:
        return len(self.entries)

    def display_names(self) -> list[str]:
        """Display names of included channels, in index order (unique)."""
        return [e.display_name for e in self.entries if e.included]

    def included_indices(self) -> list[int]:
        return [e.index for e in self.entries if e.included]

    def index_of(self, name: str) -> int:
        return self._index_by_name[name]

    def name_of(self, index: int) -> str:
        return self.entries[index].display_name

    def indices_of(self, names: Sequence[str]) -> list[int]:
        return [self._index_by_name[n] for n in names]

    def set_included(self, index: int, flag: bool) -> None:
        """Future UI hook; caller must invalidate tally-derived caches."""
        self.entries[index].included = flag
        self._rebuild_lookup()

    # ── ordering / keys ─────────────────────────────────────

    def sort_names(self, names: Sequence[str]) -> list[str]:
        return sorted(names, key=lambda n: self._index_by_name.get(n, 10**6))

    def combo_key(self, names: Sequence[str]) -> str:
        """Canonical '|'-joined key, sorted by channel index."""
        return "|".join(self.sort_names(names))

    # ── fingerprint bit masks ───────────────────────────────
    # Channel c lives at word c // 64, bit c % 64 of the (fp_0, fp_1) pair.

    def fp_masks(self, indices: Sequence[int]) -> tuple[np.uint64, np.uint64]:
        m0 = np.uint64(0)
        m1 = np.uint64(0)
        for c in indices:
            if c // 64 == 0:
                m0 |= np.uint64(1) << np.uint64(c % 64)
            else:
                m1 |= np.uint64(1) << np.uint64(c % 64)
        return m0, m1

    def included_fp_masks(self) -> tuple[np.uint64, np.uint64]:
        return self.fp_masks(self.included_indices())
