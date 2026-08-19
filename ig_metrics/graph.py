"""Graph API client: GET only, rate-limit aware, replayable from fixtures.

Two design decisions worth pointing at:

* **The client physically cannot write.** There is no post/delete method, and
  the one transport call hard-codes GET. A read-only integration should be
  provable by reading the code, not by trusting a comment.
* **Rate limits are handled, not hoped away.** Meta answers 4/17/32 (or 429)
  when an app or a user is throttled; those are retried with backoff and the
  `X-App-Usage` header is surfaced so a caller can slow down before it happens.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from .config import Settings

log = logging.getLogger(__name__)

# Meta's throttling codes plus the plain HTTP ones.
RATE_LIMIT_CODES = frozenset({4, 17, 32, 613})
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
# Token problems: retrying is pointless, the user has to reconnect.
AUTH_ERROR_CODES = frozenset({102, 190, 463, 467})


class GraphError(RuntimeError):
    """A Graph call failed for a reason the caller has to know about."""

    def __init__(self, message: str, code: int | None = None, is_auth: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.is_auth = is_auth


class WriteAttempted(RuntimeError):
    """Raised if anything tries to make this client change data."""


@dataclass
class GraphStats:
    calls: int = 0
    retries: int = 0
    from_fixtures: int = 0
    app_usage: dict[str, Any] = field(default_factory=dict)


class GraphClient:
    """Minimal read-only Graph client."""

    def __init__(
        self,
        settings: Settings,
        fixtures_dir: str | Path | None = None,
        transport: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else Path(__file__).resolve().parents[1] / "fixtures"
        self.transport = transport or self._http_get
        self.stats = GraphStats()

    # -- public API --------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a Graph edge. `path` is like 'me/accounts' or '17841400000000000'."""
        params = dict(params or {})
        self.stats.calls += 1

        if self.settings.demo_mode:
            return self._from_fixture(path, params)

        url = f"{self.settings.graph_url}/{path.lstrip('/')}"
        return self._live_get(url, params)

    def post(self, *_args, **_kwargs):  # pragma: no cover - guard, never used
        raise WriteAttempted(
            "this integration is read-only: it requests no write permissions and implements no writes"
        )

    delete = post

    # -- internals ---------------------------------------------------------

    def _live_get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error = "unknown error"
        for attempt in range(1, self.settings.max_retries + 1):
            response = self.transport(url, params)
            self._remember_usage(response)

            if response.status_code < 300:
                return response.json()

            body = self._safe_json(response)
            error = body.get("error", {}) if isinstance(body, dict) else {}
            code = error.get("code")
            message = error.get("message", response.text[:200])
            last_error = f"HTTP {response.status_code} code={code}: {message}"

            if code in AUTH_ERROR_CODES:
                # The token is dead - no amount of retrying fixes that.
                raise GraphError(last_error, code=code, is_auth=True)

            if response.status_code in RETRY_STATUS or code in RATE_LIMIT_CODES:
                if attempt < self.settings.max_retries:
                    pause = (2 ** (attempt - 1)) + random.uniform(0, 0.3)
                    self.stats.retries += 1
                    log.warning("throttled (%s); retry %s in %.1fs", last_error, attempt, pause)
                    time.sleep(pause)
                    continue
            raise GraphError(last_error, code=code)

        raise GraphError(last_error)

    def _from_fixture(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Replay a recorded response. Fixture names mirror the Graph path."""
        name = self._fixture_name(path, params)
        candidate = self.fixtures_dir / f"{name}.json"
        if not candidate.exists():
            raise GraphError(f"no fixture for '{path}' (looked for {candidate.name})")
        self.stats.from_fixtures += 1
        return json.loads(candidate.read_text(encoding="utf-8"))

    @staticmethod
    def _fixture_name(path: str, params: dict[str, Any]) -> str:
        slug = path.strip("/").replace("/", "_")
        metric = params.get("metric")
        if metric:
            first = str(metric).split(",")[0]
            period = params.get("period", "")
            return f"{slug}_{first}_{period}".rstrip("_")
        return slug

    def _remember_usage(self, response: Any) -> None:
        raw = getattr(response, "headers", {}).get("X-App-Usage")
        if raw:
            try:
                self.stats.app_usage = json.loads(raw)
            except ValueError:
                pass

    @staticmethod
    def _safe_json(response: Any) -> Any:
        try:
            return response.json()
        except ValueError:
            return {}

    def _http_get(self, url: str, params: dict[str, Any]):
        return requests.get(url, params=params, timeout=self.settings.request_timeout)
