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
    """Remove snapshot file at recordings/<category>/<name>.json or recordings/<name>.json. Returns True if deleted."""
    rec = _recordings_dir(dataset_id)
    if not rec.exists():
        return False
    safe = _safe_filename(name)
    # Try category subfolder first
    folder = rec / _safe_folder_name(category)
    path = folder / safe
    if path.exists():
        path.unlink()
        return True
    # Fall back to root recordings directory (for legacy/root-level snapshots)
    root_path = rec / safe
    if root_path.exists():
        root_path.unlink()
        return True
    # Last resort: search by title/id match across all snapshot files
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


def delete_thumbnail_in_category(category: str, title: str, dataset_id: str = DEFAULT_DATASET) -> bool:
    """Remove thumbnail PNG for a bookmark: recordings/<category>/<safe_title>.png or recordings/<safe_title>.png.

    Note: we intentionally do NOT delete any fallback thumbnail (e.g., <category>.png).
    Returns True if the specific thumbnail file was deleted.
    """
    try:
        path = thumbnail_path(category, title, dataset_id)
        if path.exists():
            path.unlink()
            return True
        # Fall back to root recordings directory
        rec = _recordings_dir(dataset_id)
        base = _safe_filename((title or "").strip() or "unnamed").replace(".json", "").strip(".")
        root_path = rec / (base + ".png")
        if root_path.exists():
            root_path.unlink()
            return True
    except Exception:
        return False
    return False


def delete_category(category: str, dataset_id: str = DEFAULT_DATASET) -> int:
    """Delete all snapshots and thumbnails in a category folder, then remove the folder.
    Returns the number of files deleted."""
    rec = _recordings_dir(dataset_id)
    folder = rec / _safe_folder_name(category)
    if not folder.exists() or not folder.is_dir():
        return 0
    count = 0
    for p in list(folder.iterdir()):
        if p.is_file():
            p.unlink()
            count += 1
    # Remove the now-empty folder
    try:
        folder.rmdir()
    except OSError:
        pass  # folder not empty (unexpected nested dirs) — leave it
    return count


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


def thumbnail_path(category: str, title: str, dataset_id: str = DEFAULT_DATASET) -> Path:
    """Path to thumbnail image for a bookmark: recordings/<category>/<safe_title>.png (same base name as JSON)."""
    rec = _recordings_dir(dataset_id)
    folder = rec / _safe_folder_name(category)
    base = _safe_filename((title or "").strip() or "unnamed").replace(".json", "").strip(".")
    return folder / (base + ".png")


def thumbnail_path_or_fallback(category: str, title: str, dataset_id: str = DEFAULT_DATASET) -> Optional[Path]:
    """Return path to thumbnail: first recordings/<category>/<title>.png; if missing, use <category>.png in that folder."""
    path = thumbnail_path(category, title, dataset_id)
    if path.exists():
        return path
    fallback = path.parent / (_safe_folder_name(category) + ".png")
    return fallback if fallback.exists() else None


def save_thumbnail(png_bytes: bytes, category: str, title: str, dataset_id: str = DEFAULT_DATASET) -> Path:
    """Save thumbnail PNG to recordings/<category>/<safe_title>.png. Returns path."""
    path = thumbnail_path(category, title, dataset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes)
    return path


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
