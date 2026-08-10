"""
Lightweight profiling helpers for the volume streaming pipeline.

Profiling is OFF by default. It is enabled only when the app is started with
the ``--profile`` command-line flag, which calls :func:`enable_profiling`.

When enabled, every profile line is appended (with a timestamp) to a log file
and flushed immediately, so the trace is preserved continuously even if the
process is killed mid-session.

What gets logged, per load, lets you see how long each stage takes
(tile assembly / numpy->vtk / gpu upload) and how the two cache layers behave:

  * in-memory decoded-chunk cache (VolumeStreamer._chunk_cache) — a byte-budget
    LRU keyed by chunk identity (comp, ch, cyi, cxi); overlapping ROIs reuse
    tiles. See VolumeStreamer._chunk_cache_summary.
  * on-disk zarr CacheStore — chunks pulled from remote are stored locally;
    a CacheStore "miss" means we went to the remote, a "hit" means local disk.
"""
from __future__ import annotations

import datetime as _dt
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple

# --- Module state (toggled by enable_profiling) ---
_enabled: bool = False
_log_fh = None  # open text file handle
_log_path: Optional[Path] = None
_lock = threading.Lock()


def enable_profiling(path: Optional[str] = None) -> Path:
    """Turn profiling on and open the log file for continuous appending.

    Called once from the app entrypoint when ``--profile`` is passed.
    If ``path`` is None, a timestamped file is created under ./profiling/.
    Returns the resolved log file path.
    """
    global _enabled, _log_fh, _log_path

    if path:
        log_path = Path(path).expanduser()
        if log_path.is_dir():
            log_path = log_path / _default_name()
    else:
        log_dir = Path.cwd() / "profiling"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / _default_name()

    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Line-buffered text append; we also flush explicitly after every write.
    _log_fh = open(log_path, "a", buffering=1, encoding="utf-8")
    _log_path = log_path
    _enabled = True
    _log_fh.write(
        f"# BioSET profiling started {_dt.datetime.now().isoformat(timespec='seconds')}\n"
    )
    _log_fh.flush()
    return log_path


def is_enabled() -> bool:
    return _enabled


def log_path() -> Optional[Path]:
    return _log_path


def _default_name() -> str:
    return "bioset_profile_" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"


def _emit(line: str) -> None:
    """Write one profile line to the log file (timestamped) and flush."""
    if not _enabled or _log_fh is None:
        return
    ts = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    with _lock:
        try:
            _log_fh.write(f"{ts} {line}\n")
            _log_fh.flush()
        except Exception:
            pass


def fmt_bytes(n: float) -> str:
    """Human-readable byte count."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"


def fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f}ms"


class StageTimer:
    """Collects named stage durations and emits one summary line.

    Usage:
        t = StageTimer("load", comp=6, ch=0)
        with t.stage("compute"):
            ...
        t.log(bytes=arr.nbytes)

    All methods are no-ops (aside from cheap timekeeping) when profiling is
    disabled, so they're safe to leave in hot paths.
    """

    def __init__(self, tag: str, **context):
        self.tag = tag
        self.context = context
        self._stages: List[Tuple[str, float]] = []
        self._t0 = time.perf_counter()

    @contextmanager
    def stage(self, name: str):
        if not _enabled:
            yield
            return
        t = time.perf_counter()
        try:
            yield
        finally:
            self._stages.append((name, time.perf_counter() - t))

    def add(self, name: str, seconds: float) -> None:
        self._stages.append((name, seconds))

    def total(self) -> float:
        return time.perf_counter() - self._t0

    def log(self, **extra) -> None:
        if not _enabled:
            return
        ctx = " ".join(f"{k}={v}" for k, v in self.context.items())
        stages = " ".join(f"{n}={fmt_ms(d)}" for n, d in self._stages)
        extras = " ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
        _emit(f"[{self.tag}] {ctx} total={fmt_ms(self.total())} | {stages} | {extras}")


def log(tag: str, msg: str) -> None:
    if _enabled:
        _emit(f"[{tag}] {msg}")
