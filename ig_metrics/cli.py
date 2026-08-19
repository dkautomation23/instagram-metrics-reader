"""Command line: print the login URL, or read metrics for every linked account.

    python -m ig_metrics login-url
    python -m ig_metrics read --token <user_access_token>
    python -m ig_metrics read --json          # demo mode needs no token
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .accounts import safe_resolve
from .cache import Cache
from .config import READ_ONLY_SCOPES, get_settings
from .graph import GraphClient
from .metrics import MetricsReader
from .oauth import build_login_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ig_metrics", description="Read-only Instagram Business metrics.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login-url", help="print the Facebook Login URL with read-only scopes")

    read = subparsers.add_parser("read", help="resolve accounts and read their metrics")
    read.add_argument("--token", default="DEMO_TOKEN", help="user access token (not needed in demo mode)")
    read.add_argument("--period-days", type=int, default=28, help="insight window (default: 28)")
    read.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    settings = get_settings()

    if args.command == "login-url":
        if settings.missing_for_live():
            print("warning: set", ", ".join(settings.missing_for_live()), "for a working URL\n")
        url, state = build_login_url(settings)
        print("scopes:", ", ".join(READ_ONLY_SCOPES))
        print("state :", state)
        print(url)
        return 0

    client = GraphClient(settings)
    cache = Cache(settings.cache_path, settings.cache_ttl_seconds)
    reader = MetricsReader(settings, client, cache)

    resolved = safe_resolve(client, args.token)
    if not resolved["ok"]:
        print(f"cannot read accounts ({resolved['reason']}): {resolved['detail']}")
        return 1

    snapshots = [
        reader.snapshot(account.ig_id, account.username, args.token, args.period_days)
        for account in resolved["accounts"]
    ]

    if args.json:
        print(json.dumps([snapshot.as_dict() for snapshot in snapshots], indent=2))
        return 0

    print(f"{'account':<20}{'followers':>10}{'reach':>10}{'eng.rate':>10}{'as of':>22}  freshness")
    for snapshot in snapshots:
        freshness = "STALE" if snapshot.is_stale else "fresh"
        print(
            f"@{snapshot.username:<19}{snapshot.followers:>10,}{snapshot.reach:>10,}"
            f"{snapshot.engagement_rate:>9.2f}%{snapshot.as_of:>22}  {freshness} ({snapshot.source})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
