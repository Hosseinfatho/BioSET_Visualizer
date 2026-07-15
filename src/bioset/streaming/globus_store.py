"""Globus Connect Server HTTPS interface as a **zarr v3** read-only store.

BioSET runs on zarr v3 (the disk CacheStore and the chunk-streaming reader are
v3 APIs), so — unlike a plain `MutableMapping` (zarr v2) store — this implements
the zarr v3 `Store` ABC. Consolidated **zarr v2 format** stores served over the
Globus HTTPS interface are read fine by zarr v3 (`open_consolidated` works on the
v2 `.zmetadata`), so no zarr downgrade is needed.

The HTTPS interface serves one file per zarr chunk over authenticated HTTPS and
honours `Range` requests. It does **not** support directory listing, so the
target store must be **consolidated** (`.zmetadata`); this store advertises
`supports_listing = False` and the source is opened with `open_consolidated`.

Concurrency: chunk reads run on many background threads (`_chunk_pool`). zarr v3
bridges those sync reads onto one event loop, so `get()` must not block that loop
— it offloads each HTTP GET to a dedicated `ThreadPoolExecutor` over a pooled
`requests.Session` (`HTTPAdapter(pool_maxsize=64)`), which is what gives true
concurrent fetching.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from zarr.abc.store import ByteRequest, Store


# --------------------------------------------------------------------------
# URL convention
# --------------------------------------------------------------------------
def is_globus_url(url: Optional[str]) -> bool:
    """True for our `globus://<path>` scheme or a raw Globus HTTPS host."""
    if not url:
        return False
    return url.startswith("globus://") or "gaccess.io" in url


def resolve_globus_url(url: str, https_base: Optional[str]) -> str:
    """Resolve a store URL to the concrete HTTPS base for the store root.

    - `globus://<abs/path>`  -> `{https_base}/<abs/path>`  (host from config)
    - a full `https://...gaccess.io/<path>` is returned unchanged.
    """
    if url.startswith("globus://"):
        if not https_base:
            raise ValueError(
                "globus:// URL requires BIOSET_GLOBUS_HTTPS_BASE (cfg.globus_https_base)")
        path = url[len("globus://"):]
        return f"{https_base.rstrip('/')}/{path.lstrip('/')}"
    return url


# --------------------------------------------------------------------------
# Auth (native-app OAuth2, refresh-token cached; console flow once)
# --------------------------------------------------------------------------
def make_globus_authorizer(client_id: str, collection_id: str,
                           token_file: str = "~/.bioset/globus_token.json"):
    """Return a globus_sdk RefreshTokenAuthorizer for the collection's HTTPS +
    data_access scopes. First run does the interactive console flow (paste the
    auth code) and caches the refresh token; later runs are non-interactive."""
    import globus_sdk

    token_file = os.path.expanduser(token_file)
    https_scope = f"https://auth.globus.org/scopes/{collection_id}/https"
    data_scope = f"https://auth.globus.org/scopes/{collection_id}/data_access"
    client = globus_sdk.NativeAppAuthClient(client_id)

    if os.path.exists(token_file):
        try:
            saved = json.load(open(token_file))
            return globus_sdk.RefreshTokenAuthorizer(saved["refresh_token"], client)
        except Exception as e:
            print(f"[globus] cached token unusable ({e}); re-authenticating",
                  file=sys.__stderr__)

    client.oauth2_start_flow(requested_scopes=[https_scope, data_scope],
                             refresh_tokens=True)
    # Print to the real stderr so it shows even when app.py redirects stdout.
    print("\n[globus] Authorize BioSET access, then paste the code below:",
          file=sys.__stderr__)
    print("[globus] " + client.oauth2_get_authorize_url(), file=sys.__stderr__)
    code = input("[globus] paste auth code: ").strip()
    tokens = client.oauth2_exchange_code_for_tokens(code)
    rs = tokens.by_resource_server[collection_id]
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    with open(token_file, "w") as fh:
        json.dump({"refresh_token": rs["refresh_token"]}, fh)
    return globus_sdk.RefreshTokenAuthorizer(
        rs["refresh_token"], client,
        access_token=rs["access_token"],
        expires_at=rs["expires_at_seconds"])


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------
def _range_header(byte_range: Optional[ByteRequest]) -> Optional[str]:
    """Map a zarr v3 ByteRequest to an HTTP Range header (or None for full GET)."""
    if byte_range is None:
        return None
    suffix = getattr(byte_range, "suffix", None)
    if suffix is not None:
        return f"bytes=-{suffix}"
    offset = getattr(byte_range, "offset", None)
    if offset is not None:
        return f"bytes={offset}-"
    start = getattr(byte_range, "start", None)
    end = getattr(byte_range, "end", None)
    if start is not None:
        return f"bytes={start}-{end - 1}" if end is not None else f"bytes={start}-"
    return None


class GlobusHTTPStore(Store):
    """Read-only zarr v3 store over the Globus HTTPS interface."""

    def __init__(self, base_url: str, authorizer, *, pool: int = 64,
                 timeout: float = 60.0):
        super().__init__(read_only=True)
        self.base = base_url.rstrip("/")
        self.authorizer = authorizer
        self._timeout = timeout
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        # Offload blocking HTTP off zarr's single sync-bridge loop so concurrent
        # chunk reads actually run in parallel.
        self._io_pool = ThreadPoolExecutor(
            max_workers=pool, thread_name_prefix="globus_http")
        # Instrumentation (not correctness).
        self.n_requests = 0
        self.bytes_read = 0

    # --- identity ---
    def __eq__(self, other) -> bool:
        return isinstance(other, GlobusHTTPStore) and other.base == self.base

    def __hash__(self) -> int:
        return hash(("GlobusHTTPStore", self.base))

    def __repr__(self) -> str:
        return f"GlobusHTTPStore({self.base!r})"

    # --- capabilities ---
    @property
    def supports_writes(self) -> bool:
        return False

    @property
    def supports_deletes(self) -> bool:
        return False

    @property
    def supports_partial_writes(self) -> bool:
        return False

    @property
    def supports_listing(self) -> bool:
        return False

    # --- auth header (auto-refreshes) ---
    def _auth_header(self) -> str:
        try:
            return self.authorizer.get_authorization_header()
        except AttributeError:
            # Older API: ensure a valid token then read it directly.
            ensure = getattr(self.authorizer, "ensure_valid_token", None)
            if callable(ensure):
                ensure()
            return f"Bearer {self.authorizer.access_token}"

    # --- blocking IO (run in the io pool) ---
    def _blocking_get(self, key: str, byte_range: Optional[ByteRequest]) -> Optional[bytes]:
        headers = {"Authorization": self._auth_header()}
        rng = _range_header(byte_range)
        if rng:
            headers["Range"] = rng
        r = self.session.get(f"{self.base}/{key}", headers=headers,
                             timeout=self._timeout)
        self.n_requests += 1
        if r.status_code == 404:
            return None
        if r.status_code in (401, 403):
            raise PermissionError(
                f"Globus {r.status_code} on {key!r}; token/permissions issue")
        r.raise_for_status()
        self.bytes_read += len(r.content)
        return r.content

    def _blocking_exists(self, key: str) -> bool:
        headers = {"Authorization": self._auth_header()}
        r = self.session.head(f"{self.base}/{key}", headers=headers,
                              timeout=self._timeout)
        return r.status_code == 200

    # --- zarr v3 Store API ---
    async def get(self, key: str, prototype, byte_range: Optional[ByteRequest] = None):
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(self._io_pool, self._blocking_get, key, byte_range)
        if data is None:
            return None
        return prototype.buffer.from_bytes(data)

    async def get_partial_values(self, prototype,
                                 key_ranges: Iterable[tuple[str, Optional[ByteRequest]]]):
        loop = asyncio.get_running_loop()

        async def _one(key, br):
            data = await loop.run_in_executor(self._io_pool, self._blocking_get, key, br)
            return None if data is None else prototype.buffer.from_bytes(data)

        return await asyncio.gather(*[_one(k, br) for k, br in key_ranges])

    async def exists(self, key: str) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._io_pool, self._blocking_exists, key)

    # read-only: writes/deletes not supported
    async def set(self, key: str, value) -> None:
        raise NotImplementedError("GlobusHTTPStore is read-only")

    async def delete(self, key: str) -> None:
        raise NotImplementedError("GlobusHTTPStore is read-only")

    # non-listing (consolidated metadata avoids the need to list)
    async def list(self):
        if False:
            yield  # pragma: no cover

    async def list_dir(self, prefix: str):
        if False:
            yield  # pragma: no cover

    async def list_prefix(self, prefix: str):
        if False:
            yield  # pragma: no cover
