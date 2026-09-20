# datasets.py
"""Named dataset presets read from `datasets.json`.

Each preset carries the three paths the Data Sources panel would otherwise be
given by hand: the zarr, an optional separate OME-XML, and the analysis results
directory. Selecting one in the UI fills the fields; loading stays manual.

The file is optional — with none present the dropdown simply does not appear.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_FILENAME = "datasets.json"


@dataclass(frozen=True)
class DatasetPreset:
    name: str
    zarr_url: str
    separate_metadata: bool = False
    metadata_url: str = ""
    analysis_dir: str = ""


_cache: list[DatasetPreset] | None = None


def _warn(msg: str) -> None:
    # Real stderr: the app redirects sys.stderr to devnull unless --logs is on,
    # and a broken presets file is worth surfacing either way.
    print(f"[datasets] {msg}", file=sys.__stderr__, flush=True)


def _presets_file() -> Path | None:
    """First existing candidate: $BIOSET_DATASETS, ./datasets.json, repo root."""
    candidates = []
    env = os.environ.get("BIOSET_DATASETS")
    if env:
        env_path = Path(env).expanduser()
        if not env_path.is_file():
            # Say so rather than quietly falling through to another file and
            # leaving the user wondering why their presets did not take.
            _warn(f"BIOSET_DATASETS={env} does not exist; falling back")
        candidates.append(env_path)
    candidates.append(Path.cwd() / _FILENAME)
    candidates.append(Path(__file__).resolve().parents[2] / _FILENAME)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _parse_entry(entry, path: Path) -> DatasetPreset | None:
    if not isinstance(entry, dict):
        _warn(f"{path}: skipping non-object entry {entry!r}")
        return None
    name = str(entry.get("name") or "").strip()
    zarr_url = str(entry.get("zarr_url") or "").strip()
    if not name or not zarr_url:
        _warn(f"{path}: skipping entry without a name and zarr_url: {entry!r}")
        return None
    return DatasetPreset(
        name=name,
        zarr_url=zarr_url,
        separate_metadata=bool(entry.get("separate_metadata", False)),
        metadata_url=str(entry.get("metadata_url") or "").strip(),
        analysis_dir=str(entry.get("analysis_dir") or "").strip(),
    )


def load_dataset_presets() -> list[DatasetPreset]:
    """Presets from datasets.json, in file order. Never raises."""
    global _cache
    if _cache is not None:
        return _cache

    _cache = []
    path = _presets_file()
    if path is None:
        return _cache

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _warn(f"could not read {path}: {e}")
        return _cache

    entries = raw.get("datasets") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        _warn(f"{path}: expected a 'datasets' list")
        return _cache

    for entry in entries:
        preset = _parse_entry(entry, path)
        if preset is not None:
            _cache.append(preset)
    return _cache


def find_dataset_preset(name: str) -> DatasetPreset | None:
    for preset in load_dataset_presets():
        if preset.name == name:
            return preset
    return None


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def default_source_from_env() -> dict:
    """Optional Arcade/Docker defaults from environment.

    BIOSET_AUTOLOAD=1 loads zarr + analysis when a worker starts.
    Paths (container): BIOSET_ZARR_URL, BIOSET_ANALYSIS_DIR, BIOSET_METADATA_URL.
    """
    zarr = (
        (os.environ.get("BIOSET_ZARR_URL") or os.environ.get("BIOSET_ZARR_LOCAL") or "")
        .strip()
    )
    analysis = (
        (os.environ.get("BIOSET_ANALYSIS_DIR") or os.environ.get("BIOSET_DEFAULT_ANALYSIS") or "")
        .strip()
    )
    metadata = (os.environ.get("BIOSET_METADATA_URL") or "").strip()
    preset = (os.environ.get("BIOSET_AUTOLOAD_PRESET") or "").strip()
    if preset:
        found = find_dataset_preset(preset)
        if found is not None:
            zarr = zarr or found.zarr_url
            analysis = analysis or found.analysis_dir
            if found.separate_metadata:
                metadata = metadata or found.metadata_url
    return {
        "zarr_url": zarr,
        "analysis_dir": analysis,
        "metadata_url": metadata,
        "separate_metadata": _env_flag("BIOSET_METADATA_SEPARATE") or bool(metadata),
        "autoload": _env_flag("BIOSET_AUTOLOAD"),
        "preset": preset or "MIS",
    }
