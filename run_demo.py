"""End-to-end demo on recorded Graph API responses. No Meta app required.

    python run_demo.py

Shows the whole read path a creator marketplace needs: login URL with read-only
scopes, Page -> Instagram resolution, metrics, caching, rate-limit retries,
the write guard, and what happens when a token dies (stale, not silently old).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEMO_CACHE = Path("demo_cache.sqlite3")
os.environ.setdefault("DEMO_MODE", "true")
os.environ["CACHE_PATH"] = str(DEMO_CACHE)
if DEMO_CACHE.exists():
    DEMO_CACHE.unlink()  # deterministic output on every run

from ig_metrics.accounts import safe_resolve  # noqa: E402
from ig_metrics.cache import Cache  # noqa: E402
from ig_metrics.config import READ_ONLY_SCOPES, get_settings  # noqa: E402
from ig_metrics.graph import GraphClient, GraphError, WriteAttempted  # noqa: E402
from ig_metrics.metrics import MetricsReader  # noqa: E402
from ig_metrics.oauth import TokenService, build_login_url  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
RULE = "-" * 78
TOKEN = "DEMO_USER_TOKEN"  # pragma: allowlist secret - placeholder, fixtures are replayed


def head(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


class ThrottledOnce:
    """Transport that answers 429 twice, then succeeds - proves the backoff."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def __call__(self, url: str, params: dict):
        self.calls += 1

        class Response:
            status_code = 429 if self.calls < 3 else 200
            headers = {"X-App-Usage": '{"call_count":97,"total_time":23,"total_cputime":18}'}
            text = "rate limited"

            def json(_self):
                return {"error": {"code": 4, "message": "Application request limit reached"}} \
                    if _self.status_code == 429 else self.payload

        return Response()


def main() -> int:
    settings = get_settings()
    print("instagram-metrics-reader demo | DEMO_MODE =", settings.demo_mode)

    client = GraphClient(settings)
    cache = Cache(settings.cache_path, settings.cache_ttl_seconds)
    reader = MetricsReader(settings, client, cache)

    head("1. Facebook Login URL - read permissions only")
    url, state = build_login_url(replace(settings, app_id="1234567890"))
    print("   scopes:", ", ".join(READ_ONLY_SCOPES))
    print("   state :", state[:12] + "...")
    print("  ", url[:118] + "...")
    print("   No publishing scope is requested, so this app can never post.")

    head("2. Token exchange: code -> short-lived -> long-lived (60 days)")
    tokens = TokenService(settings, client)
    long_lived = tokens.exchange_for_long_lived("SHORT_LIVED_FROM_LOGIN")
    print("   ", json.dumps(long_lived.redacted()))
    print("   needs refresh now?      ", tokens.needs_refresh(long_lived))
    print("   needs refresh in 55 days?", tokens.needs_refresh(long_lived, datetime.now(timezone.utc) + timedelta(days=55)))

    head("3. Resolve Instagram Business accounts through Facebook Pages")
    resolved = safe_resolve(client, TOKEN)
    for account in resolved["accounts"]:
        print(f"   page '{account.page_name}' -> @{account.username} "
              f"(ig_id {account.ig_id}, {account.followers:,} followers)")
    print("   the third Page has no linked Instagram account and is skipped, not failed")

    head("4. Metrics (followers, reach, engagement)")
    snapshots = [reader.snapshot(a.ig_id, a.username, TOKEN) for a in resolved["accounts"]]
    print(f"   {'account':<18}{'followers':>10}{'reach':>10}{'views':>8}{'eng.rate':>10}  freshness")
    for snapshot in snapshots:
        print(f"   @{snapshot.username:<17}{snapshot.followers:>10,}{snapshot.reach:>10,}"
              f"{snapshot.profile_views:>8,}{snapshot.engagement_rate:>9.2f}%  "
              f"{'STALE' if snapshot.is_stale else 'fresh'} ({snapshot.source})")
    print("\n   full record for the first account:")
    print("  ", json.dumps(snapshots[0].as_dict(), indent=2)[:520] + " ...")

    head("5. Caching keeps the app off Meta's rate limits")
    calls_before = client.stats.calls
    again = [reader.snapshot(a.ig_id, a.username, TOKEN) for a in resolved["accounts"]]
    print(f"   second read: {client.stats.calls - calls_before} Graph calls, "
          f"sources = {[s.source for s in again]}")
    print("   cache:", json.dumps(cache.stats()))

    head("6. Rate limiting is retried with backoff, not crashed on")
    live = replace(settings, demo_mode=False, max_retries=3)
    throttled = ThrottledOnce({"id": "17841400000000001", "followers_count": 48210})
    throttled_client = GraphClient(live, transport=throttled)
    payload = throttled_client.get("17841400000000001", {"access_token": TOKEN})
    print(f"   answered after {throttled.calls} attempts (2x HTTP 429), "
          f"retries counted: {throttled_client.stats.retries}")
    print("   X-App-Usage seen:", json.dumps(throttled_client.stats.app_usage))
    print("   followers read:", payload["followers_count"])

    head("7. Read-only is enforced in code, not in a promise")
    try:
        throttled_client.post("17841400000000001/media", {"caption": "nope"})
    except WriteAttempted as exc:
        print("   client.post(...) ->", exc)

    head("8. A dead token makes data STALE - it never passes for current")
    def dead_token(url, params):
        class Response:
            status_code = 400
            headers = {}
            text = "OAuthException"

            def json(_self):
                return {"error": {"code": 190, "message": "Error validating access token: session expired"}}

        return Response()

    broken = GraphClient(replace(settings, demo_mode=False), transport=dead_token)
    expired_view = Cache(settings.cache_path, ttl_seconds=0)   # same file, nothing counts as fresh
    broken_reader = MetricsReader(
        replace(settings, demo_mode=False, cache_ttl_seconds=0), broken, expired_view
    )
    account = resolved["accounts"][0]
    stale = broken_reader.snapshot(account.ig_id, account.username, "EXPIRED_TOKEN")
    print(f"   @{stale.username}: followers={stale.followers:,} reach={stale.reach:,}")
    print(f"   is_stale={stale.is_stale}  source={stale.source}  as_of={stale.as_of}  age={stale.age_hours}h")
    for warning in stale.warnings:
        print("   warning:", warning)

    print("\n   what the marketplace does with this: grey out the card, show 'reconnect Instagram',")
    print("   and keep the last known numbers labelled with their date - never as today's.")

    head("9. An account that was never read has nothing to show")
    try:
        broken_reader.snapshot("17841400000000009", "unknown.creator", "EXPIRED_TOKEN")
    except GraphError as exc:
        print("   no cache + dead token ->", exc)
        print("   (zeros are not invented; the caller decides what to display)")

    print(f"\nDone. Cache written to {DEMO_CACHE.resolve()}")
    print("Live mode needs:", ", ".join(settings.missing_for_live()) or "nothing - all variables set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
