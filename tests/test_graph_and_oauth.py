"""Graph client behaviour and the token lifecycle."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import IG_ID, TOKEN, FakeResponse

from ig_metrics.config import READ_ONLY_SCOPES
from ig_metrics.graph import GraphClient, GraphError, WriteAttempted
from ig_metrics.oauth import TokenService, build_login_url


# --- login URL ------------------------------------------------------------


def test_login_url_requests_read_only_scopes(settings):
    url, state = build_login_url(settings)
    query = parse_qs(urlparse(url).query)

    assert query["scope"][0].split(",") == list(READ_ONLY_SCOPES)
    assert query["client_id"] == ["1234567890"]
    assert query["response_type"] == ["code"]
    assert query["state"] == [state] and len(state) > 20


@pytest.mark.parametrize("forbidden", ["publish", "manage_posts", "write", "comments"])
def test_no_write_scope_is_ever_requested(settings, forbidden):
    url, _ = build_login_url(settings)
    assert forbidden not in url


def test_state_differs_between_calls(settings):
    assert build_login_url(settings)[1] != build_login_url(settings)[1]


# --- token exchange -------------------------------------------------------


def test_short_lived_token_is_exchanged_for_a_long_lived_one(settings, client):
    bundle = TokenService(settings, client).exchange_for_long_lived("SHORT_LIVED")

    assert bundle.status == "ok"
    assert bundle.access_token.startswith("EAAG_DEMO")
    days_valid = (bundle.expiry - datetime.now(timezone.utc)).days
    assert 55 <= days_valid <= 60


def test_redacted_bundle_never_exposes_the_token(settings, client):
    bundle = TokenService(settings, client).exchange_for_long_lived("SHORT_LIVED")
    redacted = bundle.redacted()

    assert bundle.access_token not in str(redacted)
    assert redacted["token"].startswith("…")
    assert redacted["scopes"] == list(READ_ONLY_SCOPES)


def test_refresh_is_due_only_near_expiry(settings, client):
    service = TokenService(settings, client)
    bundle = service.exchange_for_long_lived("SHORT_LIVED")

    assert service.needs_refresh(bundle) is False
    assert service.needs_refresh(bundle, datetime.now(timezone.utc) + timedelta(days=55)) is True


def test_failed_refresh_marks_the_account_for_reconnection(settings):
    def dead(url, params):
        return FakeResponse(400, {"error": {"code": 190, "message": "session expired"}}, text="OAuthException")

    live = replace(settings, demo_mode=False)
    service = TokenService(live, GraphClient(live, transport=dead))
    healthy = TokenService(settings, GraphClient(settings)).exchange_for_long_lived("SHORT")

    refreshed = service.refresh(healthy)
    assert refreshed.status == "needs_reconnect"
    assert "190" in refreshed.last_error
    # The old token is kept so the UI can still say when the data was from.
    assert refreshed.access_token == healthy.access_token


def test_missing_access_token_in_response_is_an_error(settings, tmp_path):
    (tmp_path / "oauth_access_token.json").write_text('{"unexpected": true}', encoding="utf-8")
    client = GraphClient(settings, fixtures_dir=tmp_path)
    with pytest.raises(GraphError, match="no access_token"):
        TokenService(settings, client).exchange_for_long_lived("SHORT")


# --- graph client ---------------------------------------------------------


def test_fixtures_are_replayed_in_demo_mode(client):
    profile = client.get(IG_ID, {"access_token": TOKEN, "fields": "followers_count"})
    assert profile["username"] == "lena.moves"
    assert client.stats.from_fixtures == 1


def test_missing_fixture_says_which_one(client):
    with pytest.raises(GraphError, match="no fixture"):
        client.get("does/not/exist")


def test_write_methods_are_not_implemented(client):
    with pytest.raises(WriteAttempted, match="read-only"):
        client.post(f"{IG_ID}/media", {"caption": "x"})
    with pytest.raises(WriteAttempted):
        client.delete(f"{IG_ID}/media/123")


def test_rate_limit_is_retried_then_succeeds(settings):
    calls = {"n": 0}

    def transport(url, params):
        calls["n"] += 1
        if calls["n"] < 3:
            return FakeResponse(429, {"error": {"code": 4, "message": "limit reached"}},
                                headers={"X-App-Usage": '{"call_count":98}'})
        return FakeResponse(200, {"id": IG_ID, "followers_count": 48210})

    live = replace(settings, demo_mode=False)
    client = GraphClient(live, transport=transport)

    assert client.get(IG_ID)["followers_count"] == 48210
    assert calls["n"] == 3
    assert client.stats.retries == 2
    assert client.stats.app_usage == {"call_count": 98}


def test_expired_token_is_not_retried(settings):
    calls = {"n": 0}

    def transport(url, params):
        calls["n"] += 1
        return FakeResponse(400, {"error": {"code": 190, "message": "session expired"}})

    live = replace(settings, demo_mode=False)
    with pytest.raises(GraphError) as raised:
        GraphClient(live, transport=transport).get(IG_ID)

    assert calls["n"] == 1              # retrying a dead token is pointless
    assert raised.value.is_auth is True
    assert raised.value.code == 190


def test_persistent_rate_limit_eventually_raises(settings):
    live = replace(settings, demo_mode=False, max_retries=2)
    client = GraphClient(live, transport=lambda url, params: FakeResponse(429, {"error": {"code": 4}}))

    with pytest.raises(GraphError, match="429"):
        client.get(IG_ID)
    assert client.stats.retries == 1
