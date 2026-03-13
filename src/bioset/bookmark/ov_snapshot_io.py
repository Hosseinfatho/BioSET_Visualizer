from __future__ import annotations

"""Optimal View (OV) bookmark I/O.

Stores OV-only snapshots under bookmark/default/recordings/<dataset_id>/OV/
with filenames starting with ov_.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

BOOKMARK_ROOT = Path(__file__).resolve().parent
OV_RECORDINGS_BASE = BOOKMARK_ROOT / "default" / "recordings"
DEFAULT_DATASET = "default"


def ov__recordings_dir(dataset_id: str) -> Path:
    """Folder for this dataset's OV recordings: .../recordings/<dataset_id>/OV/."""
    safe_id = re.sub(r"[^\w\-]", "_", (dataset_id or DEFAULT_DATASET).strip()) or DEFAULT_DATASET
    return OV_RECORDINGS_BASE / safe_id / "OV"


def ov__safe_filename(name: str) -> str:
    """Safe filename for OV snapshot title, always starting with 'ov_' (lowercase)."""
    s = re.sub(r"[^\w\s\-]", "", name)
    s = re.sub(r"[\s\-]+", "_", s).strip("_")
    base = s[:80] or "unnamed"
    # Always enforce ov_ prefix in the actual filename
    base = "ov_" + base
    return base + ".json"


def ov_load_snapshots(dataset_id: str = DEFAULT_DATASET) -> List[Dict[str, Any]]:
    """Load all OV snapshots from this dataset's OV recordings folder."""
    rec = ov__recordings_dir(dataset_id)
    if not rec.exists():
        return []
    out: List[Dict[str, Any]] = []
    # Accept any JSON file in OV folder (including older ones like 'OV_view.json')
    for path in rec.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and (data.get("title") is not None or data.get("id")):
                out.append(data)
        except Exception as e:
            print(f"[ov_bookmark] Skip {path}: {e}")
    return out


def ov_load_snapshot_by_name(name: str, dataset_id: str = DEFAULT_DATASET) -> Optional[Dict[str, Any]]:
    """Load one OV snapshot by title/name."""
    rec = ov__recordings_dir(dataset_id)
    if not rec.exists():
        return None
    safe = ov__safe_filename(name)
    path = rec / safe
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[ov_bookmark] Failed to load {path}: {e}")
    # Fallback: search all JSON files in OV folder by title/id
    for p in rec.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("title") == name or data.get("id") == name:
                return data
        except Exception:
            continue
    return None


def ov_delete_snapshot_by_name(name: str, dataset_id: str = DEFAULT_DATASET) -> bool:
    """Remove OV snapshot file by title/name. Returns True if deleted."""
    rec = ov__recordings_dir(dataset_id)
    path = rec / ov__safe_filename(name)
    if path.exists():
        path.unlink()
        return True
    return False


def ov_save_snapshot(snapshot: Dict[str, Any], dataset_id: str = DEFAULT_DATASET) -> None:
    """Save one OV snapshot under OV subfolder with ov_ prefix in filename.

    Uses the exact title from the snapshot (the name the user typed).
    Saving again with the same name overwrites the existing file.
    """
    rec = ov__recordings_dir(dataset_id)
    rec.mkdir(parents=True, exist_ok=True)
    title = (snapshot.get("title") or snapshot.get("id") or "Unnamed").strip() or "Unnamed"

    snapshot["title"] = title
    path = rec / ov__safe_filename(title)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)


def ov_snapshot_names(dataset_id: str = DEFAULT_DATASET) -> List[str]:
    """List of OV snapshot names for dropdown (from OV recordings)."""
    return [
        s.get("title") or s.get("id") or ""
        for s in ov_load_snapshots(dataset_id)
        if s.get("title") or s.get("id")
    ]

