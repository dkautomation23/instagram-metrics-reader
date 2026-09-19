# Security Policy

## Supported Versions

There are no tagged releases yet. Only the latest commit on `main` is supported; security fixes land there.

## Reporting a Vulnerability

Report privately using GitHub's Private Vulnerability Reporting: open the repository's Security tab and select "Report a vulnerability". If that option is not available to you, email hello@dkautomation.dev instead.

Do not open a public issue for a suspected vulnerability.

Expect a first response within 3 business days.

A good report includes:

- Steps to reproduce.
- The affected file or function.
- The impact (what an attacker could read, change, or do with it).

## Scope

### Treated as a vulnerability here

- The Facebook/Instagram access token ending up written in plaintext somewhere it could be shared or committed by mistake: the SQLite cache (`Cache.set()` in `ig_metrics/cache.py`), the JSON produced by `read --json` (`MetricsSnapshot.as_dict()` in `ig_metrics/metrics.py`, printed from `ig_metrics/cli.py`), or any other saved report or export file. `TokenBundle` (`ig_metrics/oauth.py`) exists for this reason — anything printed or persisted should go through `TokenBundle.redacted()`, never the raw `access_token` field.
- A change that lets the Graph client write instead of read. `GraphClient.post()` and `delete` (`ig_metrics/graph.py`) currently only raise `WriteAttempted`; giving them a real implementation, or requesting scopes beyond `READ_ONLY_SCOPES` (`ig_metrics/config.py`) inside `build_login_url()` (`ig_metrics/oauth.py`), is in scope.
- Path handling in the fixture loader (`GraphClient._from_fixture()`) or the cache path (`Cache.__init__`) that allows reading or writing outside the intended `fixtures/` directory or cache file.

### Not treated as a vulnerability here

- The token or app secret sitting in your own local `.env` file. That is expected local configuration; `.env` is already git-ignored (see `.gitignore`) and is only loaded via `load_dotenv()` in `ig_metrics/config.py`.
- The Graph API rate-limiting your own requests. The retry/backoff handling for `RATE_LIMIT_CODES` and `RETRY_STATUS` in `ig_metrics/graph.py` is expected behavior, not a bug.
