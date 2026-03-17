from __future__ import annotations

"""Optimal View (OV) bookmark I/O.

OV snapshots stored in same category folders as main bookmarks: recordings/<category>/ov_<name>.json
No separate OV folder. Filename always has ov_ prefix.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

BOOKMARK_ROOT = Path(__file__).resolve().parent
RECORDINGS_BASE = BOOKMARK_ROOT / "recordings"
DEFAULT_DATASET = "default"


def _ov_safe_folder_name(category: str) -> str:
    """Safe folder name from category (recordings/<category>/)."""
    s = (category or "Uncategorized").strip() or "Uncategorized"
    s = re.sub(r"[^\w\s\-]", "", s)
    s = re.sub(r"[\s\-]+", "_", s).strip("_")
    return (s[:60] or "Uncategorized")


def ov__recordings_dir(dataset_id: str = DEFAULT_DATASET) -> Path:
    """Base folder for recordings (same as main bookmark; OV files are ov_*.json inside category folders)."""
    return RECORDINGS_BASE


def ov__safe_filename(name: str) -> str:
    """Safe filename for OV snapshot: always ov_<name>.json."""
    s = re.sub(r"[^\w\s\-]", "", name)
    s = re.sub(r"[\s\-]+", "_", s).strip("_")
    base = (s[:80] or "OV_view").strip() or "OV_view"
    stem = base if base.lower().startswith("ov_") else ("ov_" + base)
    return stem + ".json"


def _iter_ov_snapshot_paths(rec: Path) -> List[Path]:
    """All ov_*.json paths under recordings (root and category subfolders). Deduplicate."""
    seen: set[Path] = set()
    out: List[Path] = []
    if not rec.exists():
        return out
    for p in rec.glob("ov_*.json"):
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            out.append(p)
    for sub in rec.iterdir():
        if sub.is_dir() and sub.name != "Screenshot":
            for p in sub.glob("ov_*.json"):
                r = p.resolve()
                if r not in seen:
                    seen.add(r)
                    out.append(p)
    return out


def ov_load_snapshots(dataset_id: str = DEFAULT_DATASET) -> List[Dict[str, Any]]:
    """Load all OV snapshots (ov_*.json in recordings root and recordings/<category>/). Add _folder when from subfolder."""
    rec = ov__recordings_dir(dataset_id)
    out: List[Dict[str, Any]] = []
    for path in _iter_ov_snapshot_paths(rec):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and (data.get("title") is not None or data.get("id")):
                if path.parent != rec:
                    data = {**data, "_folder": path.parent.name}
                out.append(data)
        except Exception as e:
            print(f"[ov_bookmark] Skip {path}: {e}")
    return out


def ov_snapshot_categories(dataset_id: str = DEFAULT_DATASET) -> List[str]:
    """Unique OV categories: subfolders that contain ov_*.json, plus Uncategorized for root ov_*.json. Excludes empty names."""
    rec = ov__recordings_dir(dataset_id)
    cats: set[str] = set()
    if rec.exists():
        if any(rec.glob("ov_*.json")):
            cats.add("Uncategorized")
        for sub in rec.iterdir():
            if sub.is_dir() and sub.name != "Screenshot" and (sub.name or "").strip():
                if any(sub.glob("ov_*.json")):
                    cats.add(sub.name)
    for s in ov_load_snapshots(dataset_id):
        cat = (s.get("category") or "").strip() or "Uncategorized"
        if (cat or "").strip():
            cats.add(cat)
        folder = (s.get("_folder") or "").strip()
        if folder:
            cats.add(folder)
    filtered = [c for c in cats if (c or "").strip()]
    return sorted(filtered) if filtered else ["Uncategorized"]


def ov_load_snapshots_by_category(dataset_id: str, category: str) -> List[Dict[str, Any]]:
    """OV snapshots for the selected category. Match by _folder first, then JSON category for root."""
    all_snapshots = ov_load_snapshots(dataset_id)
    norm = (category or "Uncategorized").strip() or "Uncategorized"
    seen: set[tuple[Any, Any]] = set()
    out: List[Dict[str, Any]] = []
    for s in all_snapshots:
        key = (s.get("title"), s.get("id"))
        if key in seen:
            continue
        folder = s.get("_folder")
        if folder:
            if folder == norm:
                seen.add(key)
                out.append(s)
        else:
            cat = (s.get("category") or "").strip() or "Uncategorized"
            if cat == norm:
                seen.add(key)
                out.append(s)
    return out


def ov_load_snapshot_by_name(name: str, dataset_id: str = DEFAULT_DATASET) -> Optional[Dict[str, Any]]:
    """Load one OV snapshot by title/name (search recordings root and all category folders for ov_*.json)."""
    rec = ov__recordings_dir(dataset_id)
    if not rec.exists():
        return None
    safe = ov__safe_filename(name)
    to_try: List[Path] = [rec / safe]
    for sub in rec.iterdir():
        if sub.is_dir() and sub.name != "Screenshot":
            to_try.append(sub / safe)
    for path in to_try:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ov_bookmark] Failed to load {path}: {e}")
    for p in _iter_ov_snapshot_paths(rec):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("title") == name or data.get("id") == name:
                return data
        except Exception:
            continue
    return None


def ov_delete_snapshot_by_name(name: str, dataset_id: str = DEFAULT_DATASET) -> bool:
    """Remove OV snapshot file by title/name (search all category folders)."""
    rec = ov__recordings_dir(dataset_id)
    if not rec.exists():
        return False
    safe = ov__safe_filename(name)
    if (rec / safe).exists():
        (rec / safe).unlink()
        return True
    for sub in rec.iterdir():
        if sub.is_dir() and (sub / safe).exists():
            (sub / safe).unlink()
            return True
    for p in _iter_ov_snapshot_paths(rec):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("title") == name or data.get("id") == name:
                p.unlink()
                return True
        except Exception:
            continue
    return False


def ov_save_snapshot(snapshot: Dict[str, Any], dataset_id: str = DEFAULT_DATASET) -> None:
    """Save OV snapshot to recordings/<category>/ov_<name>.json. Creates category folder if needed. Empty category → Uncategorized."""
    rec = ov__recordings_dir(dataset_id)
    rec.mkdir(parents=True, exist_ok=True)
    title = (snapshot.get("title") or snapshot.get("id") or "OV_view").strip() or "OV_view"
    category = (snapshot.get("category") or "").strip() or "Uncategorized"
    folder = rec / _ov_safe_folder_name(category)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ov__safe_filename(title)
    out = {k: v for k, v in snapshot.items() if k != "_folder"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


def ov_snapshot_names(dataset_id: str = DEFAULT_DATASET, category: Optional[str] = None) -> List[str]:
    """OV snapshot names for dropdown. If category given, only names in that category."""
    if category is not None:
        snapshots = ov_load_snapshots_by_category(dataset_id, category)
    else:
        snapshots = ov_load_snapshots(dataset_id)
    return [
        s.get("title") or s.get("id") or ""
        for s in snapshots
        if s.get("title") or s.get("id")
    ]
