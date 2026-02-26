# snapshot_io.py
"""Load and save bookmark (saved view) snapshots per dataset.
   For each dataset link, use a folder under bookmark/default/recordings/<dataset_id>/.
   One JSON file per area/snapshot, same ID/name as user set. Supports agreements and updates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

BOOKMARK_ROOT = Path(__file__).resolve().parent
RECORDINGS_BASE = BOOKMARK_ROOT / "default" / "recordings"
DEFAULT_DATASET = "default"


def _recordings_dir(dataset_id: str) -> Path:
    """Folder for this dataset's recordings: bookmark/default/recordings/<dataset_id>/."""
    safe_id = re.sub(r'[^\w\-]', '_', (dataset_id or DEFAULT_DATASET).strip()) or DEFAULT_DATASET
    return RECORDINGS_BASE / safe_id


def screenshot_dir(dataset_id: str = DEFAULT_DATASET) -> Path:
    """Folder for screenshots: recordings/<dataset_id>/Screenshot/."""
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


def load_snapshots(dataset_id: str = DEFAULT_DATASET) -> List[Dict[str, Any]]:
    """Load all snapshots from this dataset's recordings folder."""
    rec = _recordings_dir(dataset_id)
    if not rec.exists():
        return []
    out = []
    for path in rec.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and (data.get("title") is not None or data.get("id")):
                out.append(data)
        except Exception as e:
            print(f"[bookmark] Skip {path}: {e}")
    return out


def load_snapshot_by_name(name: str, dataset_id: str = DEFAULT_DATASET) -> Optional[Dict[str, Any]]:
    """Load one snapshot by title/name (same ID as user set)."""
    rec = _recordings_dir(dataset_id)
    if not rec.exists():
        return None
    safe = _safe_filename(name)
    path = rec / safe
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[bookmark] Failed to load {path}: {e}")
    for p in rec.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("title") == name or data.get("id") == name:
                return data
        except Exception:
            continue
    return None


def delete_snapshot_by_name(name: str, dataset_id: str = DEFAULT_DATASET) -> bool:
    """Remove snapshot file by title/name. Returns True if deleted."""
    rec = _recordings_dir(dataset_id)
    path = rec / _safe_filename(name)
    if path.exists():
        path.unlink()
        return True
    return False


def save_snapshot(snapshot: Dict[str, Any], dataset_id: str = DEFAULT_DATASET) -> None:
    """Save one snapshot to recordings/<dataset_id>/<name>.json (same ID as user set)."""
    rec = _recordings_dir(dataset_id)
    rec.mkdir(parents=True, exist_ok=True)
    title = (snapshot.get("title") or snapshot.get("id") or "Unnamed").strip() or "Unnamed"
    path = rec / _safe_filename(title)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)


def snapshot_names(dataset_id: str = DEFAULT_DATASET) -> List[str]:
    """List of snapshot names for dropdown (from this dataset's recordings)."""
    return [s.get("title") or s.get("id") or "" for s in load_snapshots(dataset_id) if s.get("title") or s.get("id")]
