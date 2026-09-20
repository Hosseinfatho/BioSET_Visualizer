"""
Biomni local-server client.

Talks to the Flask server started via:
    conda activate biomni_e1
    python Biomni/run_server.py --port 5000
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import requests


_DEFAULT_BASE_URL = "http://localhost:5000"
_DEFAULT_LLM = "claude-sonnet-4-6"
_DEFAULT_MODE = "minimal"
# First Biomni /init can download datasets and take several minutes.
_INIT_TIMEOUT_S = int(os.environ.get("BIOSET_BIOMNI_INIT_TIMEOUT", "600"))

_MODELS_FILE = Path(__file__).parent / "models.txt"


def resolve_biomni_base_url(port: int | str | None = None) -> str:
    """Resolve Biomni server URL.

    Priority:
    1. ``BIOSET_BIOMNI_URL`` / ``BIOMNI_URL`` (needed from Docker → sibling/host)
    2. ``http://localhost:{port}`` (local / same-network process)
    """
    for key in ("BIOSET_BIOMNI_URL", "BIOMNI_URL"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")
    p = int(port) if port not in (None, "") else 5000
    return f"http://localhost:{p}"


def load_models() -> tuple[list[str], str, str]:
    """Load models from models.txt.

    Returns (all_models, default_model, default_llm_model).
    Each line: "<model_id> [default|default_llm]"
    """
    models = []
    default_model = _DEFAULT_LLM
    default_llm = _DEFAULT_LLM

    if _MODELS_FILE.exists():
        for line in _MODELS_FILE.read_text().strip().splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            model_id = parts[0]
            models.append(model_id)
            if "default" in parts[1:]:
                default_model = model_id
            if "default_llm" in parts[1:]:
                default_llm = model_id

    return models, default_model, default_llm


def _check_response(resp: "requests.Response") -> dict:
    """Raise a descriptive RuntimeError on non-2xx, otherwise return JSON."""
    if not resp.ok:
        try:
            body = resp.json()
            msg = body.get("error") or body.get("message") or str(body)
        except Exception:
            msg = resp.text[:300] or f"HTTP {resp.status_code}"
        raise RuntimeError(f"[{resp.status_code}] {msg}")
    return resp.json()


def _is_missing_route(resp: "requests.Response") -> bool:
    """True when the Flask wrapper has no handler for this path."""
    if resp.status_code != 404:
        return False
    body = (resp.text or "").lower()
    return "<!doctype html>" in body or "not found" in body or not body.strip()


class BiomniLocalClient:
    """HTTP client for the local Biomni Flask server."""

    def __init__(self, base_url: str = _DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.initialized = False

    def _post_or_query(
        self,
        path: str,
        payload: dict,
        *,
        fallback_question: str,
        markers: Optional[list[str]] = None,
        channel_stats: Optional[dict] = None,
        mode: str = _DEFAULT_MODE,
        image: Optional[str] = None,
    ) -> dict:
        """POST `path`; older Arcade wrappers only have /query, so 404 falls back."""
        resp = requests.post(f"{self.base_url}{path}", json=payload, timeout=300)
        if _is_missing_route(resp):
            print(f"[biomni] {path} missing on server; falling back to /query")
            return self.query(
                markers or payload.get("markers") or [],
                fallback_question,
                channel_stats if channel_stats is not None else payload.get("channel_stats"),
                mode=mode,
                image=image if image is not None else payload.get("image"),
            )
        return _check_response(resp)

    def init(
        self,
        llm: str = _DEFAULT_LLM,
        db_llm: Optional[str] = None,
        mode: str = _DEFAULT_MODE,
        dataset: str = "melanoma CyCIF",
        api_key: Optional[str] = None,
    ) -> bool:
        """Call POST /init to start the A1 agent on the server."""
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        payload: dict = {"llm": llm, "mode": mode, "dataset": dataset}
        if db_llm:
            payload["db_llm"] = db_llm
        if api_key:
            payload["api_key"] = api_key

        print(f"[biomni] Initialising server at {self.base_url} (llm={llm}, db_llm={db_llm}, mode={mode})")
        try:
            resp = requests.post(
                f"{self.base_url}/init",
                json=payload,
                timeout=_INIT_TIMEOUT_S,
            )
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"Cannot reach Biomni at {self.base_url}. "
                "On Arcade start the biomni container on the same Docker network "
                "(http://biomni:5000) and set BIOSET_BIOMNI_URL."
            ) from e
        except requests.exceptions.Timeout as e:
            raise RuntimeError(
                f"Biomni /init timed out after {_INIT_TIMEOUT_S}s at {self.base_url}. "
                "First run can take several minutes while datasets download."
            ) from e
        data = _check_response(resp)
        if data.get("status") != "ok":
            raise RuntimeError(f"Server init failed: {data.get('message', data)}")

        self.initialized = True
        print("[biomni] Server initialised successfully")
        return True

    def upload(self, file_path: str, description: str) -> dict:
        """Call POST /upload to send a file with its description."""
        if not self.initialized:
            raise RuntimeError("Client not initialised. Call init() first.")

        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/upload",
                files={"file": (os.path.basename(file_path), f)},
                data={"description": description},
                timeout=120,
            )
        return _check_response(resp)

    def label(
        self,
        markers: list[str],
        channel_stats: dict,
        mode: str = _DEFAULT_MODE,
        image: Optional[str] = None,
    ) -> dict:
        """Call POST /label.

        Args:
            markers:       e.g. ["CD3:#00FF00", "FOXP3:#FF00FF"]
            channel_stats: full stats dict for the selected tile (all channels)
            mode:          "minimal" | "db" | "full"
            image:         Optional base64-encoded JPEG screenshot

        Returns dict with keys "labels" and "overall".
        """
        if not self.initialized:
            raise RuntimeError("Client not initialised. Call init() first.")

        payload: dict = {"markers": markers, "channel_stats": channel_stats, "mode": mode}
        if image:
            payload["image"] = image

        print(f"[biomni] POST /label  markers={len(markers)}  image={bool(image)}")
        resp = requests.post(f"{self.base_url}/label", json=payload, timeout=300)
        return _check_response(resp)

    # ------------------------------------------------------------------
    # /query
    # ------------------------------------------------------------------

    def query(
        self,
        markers: list[str],
        question: str,
        channel_stats: Optional[dict] = None,
        mode: str = _DEFAULT_MODE,
        image: Optional[str] = None,
    ) -> dict:
        """Call POST /query.

        Args:
            markers:       e.g. ["CD3:#00FF00", "FOXP3:#FF00FF"]
            question:      Free-form question about the markers / image
            channel_stats: Optional stats dict for the selected tile; omitted if None
            mode:          "minimal" | "db" | "full"
            image:         Optional base64-encoded JPEG screenshot

        Returns dict with key "answer".
        """
        if not self.initialized:
            raise RuntimeError("Client not initialised. Call init() first.")

        payload: dict = {
            "markers": markers,
            "query": question,
            "mode": mode,
        }
        if channel_stats:
            payload["channel_stats"] = channel_stats
        if image:
            payload["image"] = image

        print(f"[biomni] POST /query  markers={len(markers)}  image={bool(image)}")
        resp = requests.post(f"{self.base_url}/query", json=payload, timeout=300)
        return _check_response(resp)

    # ------------------------------------------------------------------
    # /suggest
    # ------------------------------------------------------------------

    def suggest(
        self,
        markers: list[str],
        channel_stats: dict,
        mode: str = _DEFAULT_MODE,
        image: Optional[str] = None,
    ) -> dict:
        """Call POST /suggest.

        Args:
            markers:       currently selected markers e.g. ["CD3:#00FF00"]
            channel_stats: full stats dict for the selected tile (all channels)
            mode:          "minimal" | "db" | "full"
            image:         Optional base64-encoded JPEG screenshot

        Returns dict with key "suggestions" (list of {channel, reason, priority}).
        """
        if not self.initialized:
            raise RuntimeError("Client not initialised. Call init() first.")

        payload: dict = {"markers": markers, "channel_stats": channel_stats, "mode": mode}
        if image:
            payload["image"] = image

        print(f"[biomni] POST /suggest  markers={len(markers)}  image={bool(image)}")
        result = self._post_or_query(
            "/suggest",
            payload,
            fallback_question=(
                "Suggest additional CyCIF channels to add alongside the current "
                "markers. Reply as JSON: {\"suggestions\": [{\"channel\": \"\", "
                "\"reason\": \"\", \"priority\": \"high|medium|low\"}]}"
            ),
            markers=markers,
            channel_stats=channel_stats,
            mode=mode,
            image=image,
        )
        if "suggestions" not in result and result.get("answer"):
            result = {"suggestions": [], "answer": result.get("answer")}
        return result

    def explain(
        self,
        markers: list[str],
        channel_stats: dict,
        mode: str = _DEFAULT_MODE,
        image: Optional[str] = None,
    ) -> dict:
        """Call POST /explain — describe what is in the current viewport.

        The unprompted counterpart to /query: same grounding, but the agent
        picks what is worth saying instead of answering a question.

        Args:
            markers:       currently selected markers e.g. ["CD3:#00FF00"]
            channel_stats: viewport statistics — per-channel coverage and
                           intensity, the measured overlaps in `combinations`,
                           and the region the numbers describe
            mode:          "minimal" | "db" | "full"
            image:         Optional base64-encoded JPEG screenshot

        Returns dict with key "answer".
        """
        if not self.initialized:
            raise RuntimeError("Client not initialised. Call init() first.")

        payload: dict = {"markers": markers, "channel_stats": channel_stats,
                         "mode": mode}
        if image:
            payload["image"] = image

        print(f"[biomni] POST /explain  markers={len(markers)}  image={bool(image)}")
        return self._post_or_query(
            "/explain",
            payload,
            fallback_question="Explain what is in this view.",
            markers=markers,
            channel_stats=channel_stats,
            mode=mode,
            image=image,
        )

    def plot(
        self,
        plot_payload: dict,
        markers: Optional[list[str]] = None,
        mode: str = _DEFAULT_MODE,
    ) -> dict:
        """Call POST /plot to explain the currently displayed UpSet or bar plot.

        Args:
            plot_payload: dict with type, view_mode, data, visible_data, etc.
            markers:      optional list of active markers with colors
            mode:         "minimal" | "db" | "full"

        Returns dict with key "answer".
        """
        if not self.initialized:
            raise RuntimeError("Client not initialised. Call init() first.")

        payload: dict = {"plot": plot_payload, "mode": mode}
        if markers:
            payload["markers"] = markers

        print(f"[biomni] POST /plot  type={plot_payload.get('type')}  view_mode={plot_payload.get('view_mode')}")
        kind = plot_payload.get("type") or "plot"
        visible = plot_payload.get("visible_data") or []
        preview = visible[:12]
        question = (
            f"Explain this {kind} plot. Scope={plot_payload.get('scope_mode')}, "
            f"channels={plot_payload.get('active_channels')}. "
            f"Visible rows: {preview}"
        )
        return self._post_or_query(
            "/plot",
            payload,
            fallback_question=question,
            markers=markers,
            mode=mode,
        )

    def suggest_bookmark(
        self,
        markers: list[str],
        mode: str = _DEFAULT_MODE,
        image: Optional[str] = None,
        channel_stats: Optional[dict] = None,
    ) -> dict:
        """Call POST /bookmark to suggest bookmark form text.

        Args:
            markers:       active marker list with colors
            mode:          "minimal" | "db" | "full"
            image:         optional base64-encoded screenshot
            channel_stats: optional region statistics payload

        Returns dict with keys "title", "category", and "description".
        """
        if not self.initialized:
            raise RuntimeError("Client not initialised. Call init() first.")

        payload: dict = {"markers": markers, "mode": mode}
        if image:
            payload["image"] = image
        if channel_stats is not None:
            payload["channel_stats"] = channel_stats

        print(f"[biomni] POST /bookmark  markers={len(markers)}  image={bool(image)}")
        result = self._post_or_query(
            "/bookmark",
            payload,
            fallback_question=(
                "Suggest bookmark text for this view. Reply as JSON: "
                "{\"title\": \"\", \"category\": \"\", \"description\": \"\"}"
            ),
            markers=markers,
            channel_stats=channel_stats,
            mode=mode,
            image=image,
        )
        if not any(result.get(k) for k in ("title", "category", "description")):
            answer = (result.get("answer") or "").strip()
            if answer:
                result = {
                    "title": answer.split("\n", 1)[0][:80],
                    "category": result.get("category") or "Uncategorized",
                    "description": answer,
                }
        return result
