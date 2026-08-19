"""Finding the Instagram Business account behind a Facebook login.

The chain trips up almost everyone the first time:

    user token -> /me/accounts (Pages) -> page.instagram_business_account -> IG id

A creator who "has an Instagram business account" but never linked it to a Page
produces an empty result here, not an error - so the message shown to them has
to say exactly that, otherwise support tickets follow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .graph import GraphClient, GraphError

log = logging.getLogger(__name__)

PROFILE_FIELDS = "id,username,name,followers_count,follows_count,media_count,profile_picture_url,biography"


@dataclass(slots=True)
class InstagramAccount:
    ig_id: str
    username: str
    name: str
    page_id: str
    page_name: str
    followers: int = 0
    follows: int = 0
    media_count: int = 0
    profile_picture_url: str = ""
    biography: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class LinkageError(RuntimeError):
    """The account cannot be read, with a message meant for the creator."""


def list_pages(client: GraphClient, access_token: str) -> list[dict[str, Any]]:
    """Pages the logged-in user administers (needs pages_show_list)."""
    payload = client.get(
        "me/accounts",
        {"access_token": access_token, "fields": "id,name,instagram_business_account"},
    )
    return payload.get("data", []) or []


def resolve_accounts(client: GraphClient, access_token: str) -> list[InstagramAccount]:
    """Every Instagram Business/Creator account reachable from this login."""
    accounts: list[InstagramAccount] = []
    pages = list_pages(client, access_token)

    if not pages:
        raise LinkageError(
            "No Facebook Pages found for this login. An Instagram Business or Creator "
            "account must be connected to a Facebook Page before its insights can be read."
        )

    linked = 0
    for page in pages:
        ig_ref = page.get("instagram_business_account") or {}
        ig_id = ig_ref.get("id")
        if not ig_id:
            log.info("page '%s' has no linked Instagram account - skipped", page.get("name"))
            continue
        linked += 1
        profile = client.get(ig_id, {"access_token": access_token, "fields": PROFILE_FIELDS})
        accounts.append(
            InstagramAccount(
                ig_id=str(profile.get("id", ig_id)),
                username=profile.get("username", ""),
                name=profile.get("name", ""),
                page_id=str(page.get("id", "")),
                page_name=page.get("name", ""),
                followers=int(profile.get("followers_count", 0) or 0),
                follows=int(profile.get("follows_count", 0) or 0),
                media_count=int(profile.get("media_count", 0) or 0),
                profile_picture_url=profile.get("profile_picture_url", ""),
                biography=profile.get("biography", ""),
                raw=profile,
            )
        )

    if not linked:
        raise LinkageError(
            f"Found {len(pages)} Facebook Page(s) but none has an Instagram account linked. "
            "In Instagram: Settings -> Account type and tools -> switch to Business/Creator, "
            "then link the Page in Meta Business Suite."
        )
    return accounts


def safe_resolve(client: GraphClient, access_token: str) -> dict[str, Any]:
    """resolve_accounts without exceptions - handy for a dashboard row."""
    try:
        accounts = resolve_accounts(client, access_token)
        return {"ok": True, "accounts": accounts}
    except LinkageError as exc:
        return {"ok": False, "reason": "not_linked", "detail": str(exc), "accounts": []}
    except GraphError as exc:
        reason = "token_expired" if exc.is_auth else "graph_error"
        return {"ok": False, "reason": reason, "detail": str(exc), "accounts": []}
