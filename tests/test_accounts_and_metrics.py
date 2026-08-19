"""Page -> Instagram resolution, metric maths, caching and staleness."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from conftest import IG_ID, TOKEN, FakeResponse

from ig_metrics.accounts import LinkageError, resolve_accounts, safe_resolve
from ig_metrics.cache import Cache
from ig_metrics.graph import GraphClient, GraphError
from ig_metrics.metrics import MetricsReader


# --- account resolution ---------------------------------------------------


def test_accounts_are_resolved_through_their_pages(client):
    accounts = resolve_accounts(client, TOKEN)

    assert [a.username for a in accounts] == ["lena.moves", "nordic.diaries"]
    assert accounts[0].page_name == "Lena Fitness Studio"
    assert accounts[0].followers == 48210


def test_a_page_without_a_linked_instagram_account_is_skipped(client):
    """The fixture has three Pages; only two have Instagram linked."""
    assert len(resolve_accounts(client, TOKEN)) == 2


def test_no_pages_at_all_explains_what_to_do(settings, tmp_path):
    (tmp_path / "me_accounts.json").write_text('{"data": []}', encoding="utf-8")
    client = GraphClient(settings, fixtures_dir=tmp_path)

    with pytest.raises(LinkageError, match="connected to a Facebook Page"):
        resolve_accounts(client, TOKEN)


def test_pages_without_any_linked_instagram_explain_the_fix(settings, tmp_path):
    (tmp_path / "me_accounts.json").write_text(
        json.dumps({"data": [{"id": "1", "name": "Just a Page"}]}), encoding="utf-8"
    )
    client = GraphClient(settings, fixtures_dir=tmp_path)

    with pytest.raises(LinkageError, match="Business/Creator"):
        resolve_accounts(client, TOKEN)


def test_safe_resolve_reports_a_dead_token_as_such(settings):
    live = replace(settings, demo_mode=False)
    client = GraphClient(live, transport=lambda url, params: FakeResponse(400, {"error": {"code": 190}}))

    result = safe_resolve(client, "EXPIRED")
    assert result["ok"] is False
    assert result["reason"] == "token_expired"
    assert result["accounts"] == []


# --- metrics --------------------------------------------------------------


def test_snapshot_reads_followers_reach_and_engagement(reader):
    snapshot = reader.snapshot(IG_ID, "lena.moves", TOKEN)

    assert snapshot.followers == 48210
    assert snapshot.reach == 187432
    assert snapshot.profile_views == 5124
    assert snapshot.posts_sampled == 25
    assert 0 < snapshot.engagement_rate < 100
    assert snapshot.is_stale is False
    assert snapshot.source == "live"


def test_daily_insight_values_are_summed(reader):
    """The second fixture returns per-day values instead of total_value."""
    snapshot = reader.snapshot("17841400000000002", "nordic.diaries", TOKEN)
    assert snapshot.reach == 4120 + 5310 + 3890


def test_engagement_rate_formula(settings, cache, tmp_path):
    """(avg likes + avg comments) / followers, as a percentage."""
    for name, payload in {
        "17841400000000003.json": {"id": "17841400000000003", "username": "tiny", "followers_count": 1000},
        "17841400000000003_insights_reach_day.json": {"data": [{"name": "reach", "total_value": {"value": 500}}]},
        "17841400000000003_media.json": {
            "data": [
                {"id": "1", "like_count": 90, "comments_count": 10},
                {"id": "2", "like_count": 110, "comments_count": 10},
            ]
        },
    }.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    reader = MetricsReader(settings, GraphClient(settings, fixtures_dir=tmp_path), cache)
    snapshot = reader.snapshot("17841400000000003", "tiny", TOKEN)

    assert snapshot.avg_likes == 100.0
    assert snapshot.avg_comments == 10.0
    assert snapshot.engagement_rate == 11.0        # 110 per post / 1000 followers


def test_an_account_with_no_posts_is_flagged_not_divided_by_zero(settings, cache, tmp_path):
    for name, payload in {
        "17841400000000004.json": {"id": "17841400000000004", "username": "empty", "followers_count": 500},
        "17841400000000004_insights_reach_day.json": {"data": []},
        "17841400000000004_media.json": {"data": []},
    }.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    reader = MetricsReader(settings, GraphClient(settings, fixtures_dir=tmp_path), cache)
    snapshot = reader.snapshot("17841400000000004", "empty", TOKEN)

    assert snapshot.engagement_rate == 0.0
    assert any("no recent posts" in warning for warning in snapshot.warnings)


# --- caching and freshness ------------------------------------------------


def test_second_read_comes_from_cache_without_touching_graph(reader, client):
    reader.snapshot(IG_ID, "lena.moves", TOKEN)
    calls_after_first = client.stats.calls

    second = reader.snapshot(IG_ID, "lena.moves", TOKEN)

    assert second.source == "cache"
    assert client.stats.calls == calls_after_first    # no additional Graph traffic


def test_cache_expires_after_its_ttl(settings, client, tmp_path):
    reader = MetricsReader(settings, client, Cache(tmp_path / "c.sqlite3", ttl_seconds=0))
    reader.snapshot(IG_ID, "lena.moves", TOKEN)
    calls_after_first = client.stats.calls

    reader.snapshot(IG_ID, "lena.moves", TOKEN)
    assert client.stats.calls > calls_after_first     # TTL 0 -> always refetched


def test_dead_token_serves_the_last_values_marked_stale(settings, client, cache):
    MetricsReader(settings, client, cache).snapshot(IG_ID, "lena.moves", TOKEN)   # warm the cache

    live = replace(settings, demo_mode=False)
    broken = GraphClient(live, transport=lambda url, params: FakeResponse(400, {"error": {"code": 190}}))
    stale_reader = MetricsReader(live, broken, Cache(settings.cache_path, ttl_seconds=0))

    snapshot = stale_reader.snapshot(IG_ID, "lena.moves", "EXPIRED")

    assert snapshot.source == "cache_stale"
    assert snapshot.is_stale is True
    assert snapshot.followers == 48210                 # last known value, not zero
    assert any("live read failed" in warning for warning in snapshot.warnings)


def test_cached_data_past_the_freshness_threshold_is_marked_stale(settings, client, tmp_path):
    """Cache TTL and "fresh enough to show" are two different questions.

    A long TTL keeps Graph traffic down; `stale_after_hours` decides whether the
    number is still honest to display. Here the entry is served from cache and
    still flagged, because it is older than the display threshold.
    """
    long_lived_cache = Cache(tmp_path / "long.sqlite3", ttl_seconds=7 * 86400)
    reader = MetricsReader(replace(settings, stale_after_hours=1), client, long_lived_cache)
    reader.snapshot(IG_ID, "lena.moves", TOKEN)

    later = datetime.now(timezone.utc) + timedelta(hours=5)
    snapshot = reader.snapshot(IG_ID, "lena.moves", TOKEN, now=later)

    assert snapshot.source == "cache"
    assert snapshot.is_stale is True
    assert snapshot.age_hours >= 5
    assert any("old" in warning for warning in snapshot.warnings)


def test_nothing_is_invented_when_there_is_no_cached_value(settings):
    live = replace(settings, demo_mode=False)
    broken = GraphClient(live, transport=lambda url, params: FakeResponse(400, {"error": {"code": 190}}))
    reader = MetricsReader(live, broken, Cache(settings.cache_path, ttl_seconds=0))

    with pytest.raises(GraphError):
        reader.snapshot("17841400000000099", "never.read", "EXPIRED")
