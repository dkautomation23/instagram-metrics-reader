"""Fixtures: demo-mode settings, a Graph client replaying recorded responses."""

from __future__ import annotations

from pathlib import Path

import pytest

from ig_metrics.cache import Cache
from ig_metrics.config import Settings
from ig_metrics.graph import GraphClient
from ig_metrics.metrics import MetricsReader

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
IG_ID = "17841400000000001"
TOKEN = "TEST_TOKEN"  # pragma: allowlist secret - placeholder, no network is touched


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        app_id="1234567890",
        app_secret="test_app_secret",  # pragma: allowlist secret - placeholder
        demo_mode=True,
        cache_path=str(tmp_path / "cache.sqlite3"),
        cache_ttl_seconds=3600,
        stale_after_hours=48,
        max_retries=3,
    )


@pytest.fixture
def client(settings) -> GraphClient:
    return GraphClient(settings, fixtures_dir=FIXTURES)


@pytest.fixture
def cache(settings) -> Cache:
    return Cache(settings.cache_path, settings.cache_ttl_seconds)


@pytest.fixture
def reader(settings, client, cache) -> MetricsReader:
    return MetricsReader(settings, client, cache)


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code: int, body: dict | None = None, headers: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._body
