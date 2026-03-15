# snapshot_io.py
"""Load and save bookmark (saved view) snapshots.
   Recordings: bookmark/recordings/<category>/<name>.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

BOOKMARK_ROOT = Path(__file__).resolve().parent
RECORDINGS_BASE = BOOKMARK_ROOT / "recordings"
DEFAULT_DATASET = "default"


def _recordings_dir(dataset_id: str = DEFAULT_DATASET) -> Path:
    """Folder for recordings: bookmark/recordings/."""
    return RECORDINGS_BASE


def screenshot_dir(dataset_id: str = DEFAULT_DATASET) -> Path:
    """Folder for screenshots: recordings/Screenshot/."""
    return _recordings_dir(dataset_id) / "Screenshot"


def save_screenshot(png_bytes: bytes, filename: str, dataset_id: str = DEFAULT_DATASET) -> Path:
    """Save PNG bytes to recordings/<dataset_id>/Screenshot/<filename>.png. Returns path."""
    folder = screenshot_dir(dataset_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = (filename or "screenshot").strip() or "screenshot"
    name = re.sub(r'[^\w\s\-.]', '', name)
    name = re.sub(r'[\s\-]+', '_', name).strip('_') or "screenshot"
    if not name.lower().endswith(".png"):
        name += ".png"
    path = folder / name
    path.write_bytes(png_bytes)
    return path


def _safe_filename(name: str) -> str:
    """Safe filename from snapshot title (same ID/name as user set for the area)."""
    s = re.sub(r'[^\w\s\-]', '', name)
    s = re.sub(r'[\s\-]+', '_', s).strip('_')
    return (s[:80] or "unnamed") + ".json"


def _safe_folder_name(category: str) -> str:
    """Safe folder name from category (for recordings/<category>/)."""
    s = (category or "Uncategorized").strip() or "Uncategorized"
    s = re.sub(r'[^\w\s\-]', '', s)
    s = re.sub(r'[\s\-]+', '_', s).strip('_')
    return (s[:60] or "Uncategorized")


def _iter_snapshot_paths(rec: Path) -> List[Path]:
    """Yield all .json paths under rec (root level and category subfolders). Skip Screenshot. Deduplicate by resolved path."""
    seen: set[Path] = set()
    out = []
    if not rec.exists():
        return out
    for p in rec.glob("*.json"):
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            out.append(p)
    for sub in rec.iterdir():
        if sub.is_dir() and sub.name != "Screenshot":
            for p in sub.glob("*.json"):
                r = p.resolve()
                if r not in seen:
                    seen.add(r)
                    out.append(p)
    return out


def load_snapshots(dataset_id: str = DEFAULT_DATASET) -> List[Dict[str, Any]]:
    """Load all snapshots from this dataset (root and category subfolders). Store _folder from path for category list."""
    rec = _recordings_dir(dataset_id)
    out = []
    for path in _iter_snapshot_paths(rec):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and (data.get("title") is not None or data.get("id")):
                if path.parent != rec:
                    data = {**data, "_folder": path.parent.name}
                out.append(data)
        except Exception as e:
            print(f"[bookmark] Skip {path}: {e}")
    return out


def load_snapshot_by_name(name: str, dataset_id: str = DEFAULT_DATASET) -> Optional[Dict[str, Any]]:
    """Load one snapshot by title/name (search root and all category folders)."""
    rec = _recordings_dir(dataset_id)
    if not rec.exists():
        return None
    safe = _safe_filename(name)
    # Try root then each category folder
    to_try = [rec / safe]
    for sub in rec.iterdir():
        if sub.is_dir() and sub.name != "Screenshot":
            to_try.append(sub / safe)
    for path in to_try:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[bookmark] Failed to load {path}: {e}")
    for p in _iter_snapshot_paths(rec):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("title") == name or data.get("id") == name:
                return data
        except Exception:
            continue
    return None


def delete_snapshot_in_category(name: str, dataset_id: str, category: str) -> bool:
    """Remove snapshot file at recordings/<dataset_id>/<category>/<name>.json. Returns True if deleted."""
    rec = _recordings_dir(dataset_id)
    if not rec.exists():
        return False
    folder = rec / _safe_folder_name(category)
    safe = _safe_filename(name)
    path = folder / safe
    if path.exists():
        path.unlink()
        return True
    return False


def delete_snapshot_by_name(name: str, dataset_id: str = DEFAULT_DATASET) -> bool:
    """Remove snapshot file by title/name (from root or category folder). Returns True if deleted."""
    rec = _recordings_dir(dataset_id)
    if not rec.exists():
        return False
    safe = _safe_filename(name)
    if (rec / safe).exists():
        (rec / safe).unlink()
        return True
    for sub in rec.iterdir():
        if sub.is_dir() and sub.name != "Screenshot" and (sub / safe).exists():
            (sub / safe).unlink()
            return True
    for p in _iter_snapshot_paths(rec):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("title") == name or data.get("id") == name:
                p.unlink()
                return True
        except Exception:
            continue
    return False


def save_snapshot(snapshot: Dict[str, Any], dataset_id: str = DEFAULT_DATASET) -> None:
    """Save snapshot to recordings/<category>/<name>.json. Empty category → Uncategorized folder."""
    rec = _recordings_dir(dataset_id)
    rec.mkdir(parents=True, exist_ok=True)
    title = (snapshot.get("title") or snapshot.get("id") or "Unnamed").strip() or "Unnamed"
    category = (snapshot.get("category") or "").strip() or "Uncategorized"
    folder = rec / _safe_folder_name(category)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / _safe_filename(title)
    out = {k: v for k, v in snapshot.items() if k != "_folder"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


def snapshot_names(dataset_id: str = DEFAULT_DATASET) -> List[str]:
    """List of snapshot names for dropdown (from this dataset's recordings)."""
    return [s.get("title") or s.get("id") or "" for s in load_snapshots(dataset_id) if s.get("title") or s.get("id")]


def snapshot_categories(dataset_id: str = DEFAULT_DATASET) -> List[str]:
    """Unique categories: from snapshot 'category' field, from _folder (path), and from subfolder names under recordings. Excludes empty names."""
    rec = _recordings_dir(dataset_id)
    cats = set()
    if rec.exists():
        for sub in rec.iterdir():
            if sub.is_dir() and sub.name != "Screenshot" and (sub.name or "").strip():
                cats.add(sub.name)
    for s in load_snapshots(dataset_id):
        cat = (s.get("category") or "").strip() or "Uncategorized"
        if (cat or "").strip():
            cats.add(cat)
        folder = (s.get("_folder") or "").strip()
        if folder:
            cats.add(folder)
    filtered = [c for c in cats if (c or "").strip()]
    return sorted(filtered) if filtered else ["Uncategorized"]


def load_snapshots_by_category(dataset_id: str, category: str) -> List[Dict[str, Any]]:
    """Return snapshots for the selected category. If snapshot has _folder (from subfolder), match only by folder; else match by JSON category."""
    all_snapshots = load_snapshots(dataset_id)
    norm = (category or "Uncategorized").strip() or "Uncategorized"
    seen: set[tuple[Any, Any]] = set()
    out = []
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
