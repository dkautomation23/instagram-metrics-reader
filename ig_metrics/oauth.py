"""Facebook Login for a read-only Instagram integration, and token lifetime.

The three-step token dance is where most creator-marketplace integrations rot:

    code  ->  short-lived token (1 hour)
          ->  long-lived token  (60 days)
          ->  refreshed long-lived token (another 60 days)

Miss the last step and the connection dies silently two months after the
creator signed up. Here the expiry is stored, `needs_refresh()` is checked
before every read, and a token that cannot be refreshed marks the account
`stale` rather than letting old numbers pass for current ones.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .config import READ_ONLY_SCOPES, Settings
from .graph import GraphClient, GraphError

log = logging.getLogger(__name__)

LOGIN_BASE = "https://www.facebook.com"
LONG_LIVED_DAYS = 60


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class TokenBundle:
    """What we keep about a connected account's access token."""

    access_token: str
    expires_at: str
    obtained_at: str
    scopes: tuple[str, ...] = READ_ONLY_SCOPES
    status: str = "ok"          # ok | needs_reconnect
    last_error: str = ""

    @property
    def expiry(self) -> datetime:
        return datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))

    def redacted(self) -> dict[str, object]:
        """Safe to log or return in an API response."""
        return {
            "token": f"…{self.access_token[-4:]}" if self.access_token else "",
            "expires_at": self.expires_at,
            "status": self.status,
            "scopes": list(self.scopes),
            "last_error": self.last_error,
        }


def build_login_url(settings: Settings, state: str | None = None) -> tuple[str, str]:
    """Return (url, state). Send the user here; Meta redirects back with a code.

    Only read permissions are requested - see READ_ONLY_SCOPES.
    """
    state = state or secrets.token_urlsafe(24)
    query = {
        "client_id": settings.app_id,
        "redirect_uri": settings.redirect_uri,
        "state": state,
        "response_type": "code",
        "scope": ",".join(READ_ONLY_SCOPES),
    }
    return f"{LOGIN_BASE}/{settings.graph_version}/dialog/oauth?{urlencode(query)}", state


class TokenService:
    """Exchanges and refreshes tokens through the Graph client."""

    def __init__(self, settings: Settings, client: GraphClient) -> None:
        self.settings = settings
        self.client = client

    def exchange_code(self, code: str) -> TokenBundle:
        """Step 1: authorization code -> short-lived user token."""
        payload = self.client.get(
            "oauth/access_token",
            {
                "client_id": self.settings.app_id,
                "client_secret": self.settings.app_secret,
                "redirect_uri": self.settings.redirect_uri,
                "code": code,
            },
        )
        return self._bundle(payload, default_seconds=3600)

    def exchange_for_long_lived(self, short_lived_token: str) -> TokenBundle:
        """Step 2: short-lived token -> 60-day token."""
        payload = self.client.get(
            "oauth/access_token",
            {
                "grant_type": "fb_exchange_token",
                "client_id": self.settings.app_id,
                "client_secret": self.settings.app_secret,
                "fb_exchange_token": short_lived_token,
            },
        )
        return self._bundle(payload, default_seconds=LONG_LIVED_DAYS * 86400)

    def needs_refresh(self, bundle: TokenBundle, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        margin = timedelta(days=self.settings.token_refresh_margin_days)
        return bundle.expiry - now <= margin

    def refresh(self, bundle: TokenBundle) -> TokenBundle:
        """Step 3: extend before expiry; on failure mark it for reconnection."""
        try:
            refreshed = self.exchange_for_long_lived(bundle.access_token)
            log.info("token refreshed, valid until %s", refreshed.expires_at)
            return refreshed
        except GraphError as exc:
            log.error("token refresh failed: %s", exc)
            return TokenBundle(
                access_token=bundle.access_token,
                expires_at=bundle.expires_at,
                obtained_at=bundle.obtained_at,
                scopes=bundle.scopes,
                status="needs_reconnect",
                last_error=str(exc),
            )

    def _bundle(self, payload: dict, default_seconds: int) -> TokenBundle:
        token = payload.get("access_token")
        if not token:
            raise GraphError(f"no access_token in response: {str(payload)[:200]}")
        seconds = int(payload.get("expires_in", default_seconds))
        now = datetime.now(timezone.utc)
        return TokenBundle(
            access_token=token,
            expires_at=iso(now + timedelta(seconds=seconds)),
            obtained_at=iso(now),
        )
