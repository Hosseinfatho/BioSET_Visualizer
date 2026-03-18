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
_DEFAULT_MODE = "full"

_MODELS_FILE = Path(__file__).parent / "models.txt"


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


class BiomniLocalClient:
    """HTTP client for the local Biomni Flask server."""

    def __init__(self, base_url: str = _DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.initialized = False

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
        resp = requests.post(f"{self.base_url}/init", json=payload, timeout=60)
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
        mode: str = _DEFAULT_MODE,
        image: Optional[str] = None,
    ) -> dict:
        """Call POST /label.

        Args:
            markers: e.g. ["CD3:#00FF00", "FOXP3:#FF00FF"]
            mode:    "minimal" | "db" | "full"
            image:   Optional base64-encoded PNG screenshot

        Returns dict with keys "labels" and "overall".
        """
        if not self.initialized:
            raise RuntimeError("Client not initialised. Call init() first.")

        payload: dict = {"markers": markers, "mode": mode}
        if image:
            payload["image"] = image

        print(f"[biomni] POST /label  markers={len(markers)}  image={bool(image)}")
        resp = requests.post(f"{self.base_url}/label", json=payload, timeout=300)
        return _check_response(resp)

    def query(
        self,
        markers: list[str],
        question: str,
        mode: str = _DEFAULT_MODE,
        image: Optional[str] = None,
    ) -> dict:
        """Call POST /query.

        Args:
            markers:  e.g. ["CD3:#00FF00", "FOXP3:#FF00FF"]
            question: Free-form question about the markers / image
            mode:     "minimal" | "db" | "full"
            image:    Optional base64-encoded PNG screenshot

        Returns dict with key "answer".
        """
        if not self.initialized:
            raise RuntimeError("Client not initialised. Call init() first.")

        payload: dict = {"markers": markers, "query": question, "mode": mode}
        if image:
            payload["image"] = image

        print(f"[biomni] POST /query  markers={len(markers)}  image={bool(image)}")
        resp = requests.post(f"{self.base_url}/query", json=payload, timeout=300)
        return _check_response(resp)

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
        resp = requests.post(f"{self.base_url}/plot", json=payload, timeout=300)
        return _check_response(resp)

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
        resp = requests.post(f"{self.base_url}/bookmark", json=payload, timeout=300)
        return _check_response(resp)
