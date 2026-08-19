"""Configuration, read from the environment at call time (see .env.example).

DEMO_MODE replays recorded Graph API responses from `fixtures/`, so the whole
flow runs without a Meta app, a Facebook Page or a single token.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

# Read-only permissions. Nothing here can publish, delete or modify anything -
# that is deliberate and it is also what makes App Review straightforward.
READ_ONLY_SCOPES = (
    "instagram_basic",
    "instagram_manage_insights",
    "pages_read_engagement",
    "pages_show_list",
)


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_id: str = ""
    app_secret: str = ""
    redirect_uri: str = "https://localhost:8000/oauth/callback"
    graph_version: str = "v21.0"

    demo_mode: bool = True
    cache_path: str = "cache.sqlite3"
    # How long a cached metric is considered fresh. Insights move slowly and
    # Graph rate limits do not, so minutes here save hours of throttling.
    cache_ttl_seconds: int = 3600
    # Refresh a long-lived token once it is this close to its 60-day expiry.
    token_refresh_margin_days: int = 7
    # Data older than this is reported as stale instead of being shown as current.
    stale_after_hours: int = 48
    request_timeout: float = 20.0
    max_retries: int = 3

    @property
    def graph_url(self) -> str:
        return f"https://graph.facebook.com/{self.graph_version}"

    def missing_for_live(self) -> list[str]:
        required = {"META_APP_ID": self.app_id, "META_APP_SECRET": self.app_secret}
        return [name for name, value in required.items() if not value]


def load_settings() -> Settings:
    return Settings(
        app_id=os.getenv("META_APP_ID", ""),
        app_secret=os.getenv("META_APP_SECRET", ""),
        redirect_uri=os.getenv("OAUTH_REDIRECT_URI", "https://localhost:8000/oauth/callback"),
        graph_version=os.getenv("META_GRAPH_VERSION", "v21.0"),
        demo_mode=_flag("DEMO_MODE", True),
        cache_path=os.getenv("CACHE_PATH", "cache.sqlite3"),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
        token_refresh_margin_days=int(os.getenv("TOKEN_REFRESH_MARGIN_DAYS", "7")),
        stale_after_hours=int(os.getenv("STALE_AFTER_HOURS", "48")),
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", "20")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
