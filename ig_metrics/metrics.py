"""Reading the numbers a creator marketplace actually shows: followers, reach,
engagement - with an explicit freshness verdict on every response.

The rule that matters: **never present old numbers as current.** If the token
died or Graph is unreachable, the reader returns the cached value together with
`is_stale`, `as_of` and `age_hours`, so the UI can grey the card out instead of
quietly showing last week's reach next to today's date.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .cache import Cache
from .config import Settings
from .graph import GraphClient, GraphError

log = logging.getLogger(__name__)

# Account-level insights available on IG Business accounts.
ACCOUNT_METRICS = ("reach", "profile_views", "accounts_engaged")
MEDIA_FIELDS = "id,caption,media_type,permalink,timestamp,like_count,comments_count"


@dataclass(slots=True)
class MetricsSnapshot:
    """One account's numbers plus how much they can be trusted."""

    ig_id: str
    username: str
    followers: int
    reach: int
    profile_views: int
    accounts_engaged: int
    posts_sampled: int
    avg_likes: float
    avg_comments: float
    engagement_rate: float          # (likes + comments) / followers, per post, %
    period_days: int
    as_of: str
    age_hours: float
    is_stale: bool
    source: str                      # "live" | "cache" | "cache_stale"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class MetricsReader:
    """Read-only metrics with caching and an honest freshness flag."""

    def __init__(self, settings: Settings, client: GraphClient, cache: Cache) -> None:
        self.settings = settings
        self.client = client
        self.cache = cache

    # -- public ------------------------------------------------------------

    def snapshot(
        self, ig_id: str, username: str, access_token: str, period_days: int = 28, now: datetime | None = None
    ) -> MetricsSnapshot:
        """Followers + reach + engagement for one account.

        Order of preference: fresh cache -> live call -> stale cache. The third
        branch is the one that keeps a marketplace honest.
        """
        now = now or _now()
        key = f"snapshot:{ig_id}:{period_days}"

        cached = self.cache.get(key, now)
        if cached is not None:
            payload, fetched_at = cached
            return self._to_snapshot(payload, fetched_at, now, source="cache")

        try:
            payload = self._fetch(ig_id, username, access_token, period_days)
        except GraphError as exc:
            return self._stale_or_raise(key, ig_id, username, period_days, now, exc)

        fetched_at = self.cache.set(key, payload, now)
        return self._to_snapshot(payload, fetched_at, now, source="live")

    # -- internals ---------------------------------------------------------

    def _fetch(self, ig_id: str, username: str, access_token: str, period_days: int) -> dict[str, Any]:
        profile = self.client.get(
            ig_id, {"access_token": access_token, "fields": "id,username,followers_count,media_count"}
        )
        followers = int(profile.get("followers_count", 0) or 0)

        insights = self.client.get(
            f"{ig_id}/insights",
            {
                "access_token": access_token,
                "metric": ",".join(ACCOUNT_METRICS),
                "period": "day",
                "metric_type": "total_value",
            },
        )
        totals = self._sum_insights(insights)

        media = self.client.get(
            f"{ig_id}/media", {"access_token": access_token, "fields": MEDIA_FIELDS, "limit": 25}
        )
        posts = media.get("data", []) or []
        likes = sum(int(post.get("like_count", 0) or 0) for post in posts)
        comments = sum(int(post.get("comments_count", 0) or 0) for post in posts)

        return {
            "ig_id": ig_id,
            "username": profile.get("username", username),
            "followers": followers,
            "reach": totals.get("reach", 0),
            "profile_views": totals.get("profile_views", 0),
            "accounts_engaged": totals.get("accounts_engaged", 0),
            "posts_sampled": len(posts),
            "likes": likes,
            "comments": comments,
            "period_days": period_days,
        }

    @staticmethod
    def _sum_insights(payload: dict[str, Any]) -> dict[str, int]:
        """Graph returns either total_value or a list of daily values."""
        totals: dict[str, int] = {}
        for item in payload.get("data", []) or []:
            name = item.get("name", "")
            if "total_value" in item:
                totals[name] = int((item["total_value"] or {}).get("value", 0) or 0)
            else:
                totals[name] = sum(int(value.get("value", 0) or 0) for value in item.get("values", []) or [])
        return totals

    def _to_snapshot(
        self, payload: dict[str, Any], fetched_at: datetime, now: datetime, source: str
    ) -> MetricsSnapshot:
        followers = int(payload.get("followers", 0) or 0)
        posts = int(payload.get("posts_sampled", 0) or 0)
        likes = int(payload.get("likes", 0) or 0)
        comments = int(payload.get("comments", 0) or 0)

        avg_likes = round(likes / posts, 1) if posts else 0.0
        avg_comments = round(comments / posts, 1) if posts else 0.0
        engagement_rate = (
            round(((likes + comments) / posts) / followers * 100, 2) if posts and followers else 0.0
        )

        age = now - fetched_at
        age_hours = round(age.total_seconds() / 3600, 1)
        is_stale = age > timedelta(hours=self.settings.stale_after_hours) or source == "cache_stale"

        warnings: list[str] = []
        if is_stale:
            warnings.append(
                f"data is {age_hours}h old; reconnect the account to refresh"
                if source == "cache_stale"
                else f"data is {age_hours}h old"
            )
        if not posts:
            warnings.append("no recent posts in the sample - engagement rate is not meaningful")

        return MetricsSnapshot(
            ig_id=payload.get("ig_id", ""),
            username=payload.get("username", ""),
            followers=followers,
            reach=int(payload.get("reach", 0) or 0),
            profile_views=int(payload.get("profile_views", 0) or 0),
            accounts_engaged=int(payload.get("accounts_engaged", 0) or 0),
            posts_sampled=posts,
            avg_likes=avg_likes,
            avg_comments=avg_comments,
            engagement_rate=engagement_rate,
            period_days=int(payload.get("period_days", 28) or 28),
            as_of=_iso(fetched_at),
            age_hours=age_hours,
            is_stale=is_stale,
            source=source,
            warnings=warnings,
        )

    def _stale_or_raise(
        self, key: str, ig_id: str, username: str, period_days: int, now: datetime, exc: GraphError
    ) -> MetricsSnapshot:
        """Graph failed. Serve the last known values, clearly marked as stale."""
        last = self.cache.last_fetched(key)
        if last is None:
            raise exc  # nothing was ever read - do not invent zeros
        payload, _ = self._raw(key)
        snapshot = self._to_snapshot(payload, last, now, source="cache_stale")
        snapshot.warnings.insert(0, f"live read failed: {exc}")
        log.warning("serving stale metrics for %s (%s)", username or ig_id, exc)
        return snapshot

    def _raw(self, key: str) -> tuple[dict[str, Any], datetime]:
        """Read a cache entry ignoring the TTL."""
        import json
        import sqlite3

        connection = sqlite3.connect(self.cache.path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute("SELECT value, fetched_at FROM cache WHERE key = ?", (key,)).fetchone()
        finally:
            connection.close()
        return json.loads(row["value"]), datetime.fromisoformat(row["fetched_at"])
