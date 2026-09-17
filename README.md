# instagram-metrics-reader

[![CI](https://github.com/dkautomation23/instagram-metrics-reader/actions/workflows/ci.yml/badge.svg)](https://github.com/dkautomation23/instagram-metrics-reader/actions/workflows/ci.yml)

Reads Instagram Business/Creator metrics — followers, reach, engagement —
through the Meta Graph API, **strictly read-only**. Built for the case a creator
marketplace has: dozens of connected creators, shared API rate limits, tokens
that expire every 60 days, and numbers on a profile card that must never be
older than they claim to be.

Runs end to end with `python run_demo.py` — no Meta app, no Facebook Page, no
tokens.

---

## The flow

```
  creator clicks "Connect Instagram"
              |
              v
   Facebook Login dialog          scopes: instagram_basic
   (read permissions only)                instagram_manage_insights
              |                           pages_read_engagement
              v                           pages_show_list
        ?code=... redirect
              |
              v
   code -> short-lived token (1h) -> long-lived token (60 days)
              |                              |
              |                     refreshed before expiry;
              |                     failure => needs_reconnect
              v
      GET /me/accounts            (Facebook Pages the user admins)
              |
              v
   page.instagram_business_account -> IG user id
              |
              v
   GET /{ig-id}?fields=followers_count,...     \
   GET /{ig-id}/insights?metric=reach,...       > cached (TTL), rate-limit aware
   GET /{ig-id}/media?fields=like_count,...    /
              |
              v
      MetricsSnapshot { followers, reach, engagement_rate,
                        as_of, age_hours, is_stale, source }
```

## What it gets right

| Problem | Handling |
| --- | --- |
| An integration that could post as the creator | no write scopes requested; the client has **no** post/delete methods — `client.post(...)` raises `WriteAttempted` |
| "I have a business account but nothing shows up" | the Page → Instagram link is resolved explicitly; a Page with no linked account is skipped, and a login with none gets a message saying exactly what to fix |
| Shared app rate limits | SQLite TTL cache per (account, window); `X-App-Usage` is surfaced; 429 / codes 4, 17, 32 retried with exponential backoff |
| Token dies after 60 days | expiry stored, `needs_refresh()` checked, refresh performed ahead of time; a failed refresh sets `needs_reconnect` |
| Old numbers displayed as current | every snapshot carries `as_of`, `age_hours`, `is_stale` and `source` (`live` / `cache` / `cache_stale`); a dead token serves the last known values **flagged**, never silently |
| Fabricated zeros | an account that was never read raises instead of returning `0 followers` |
| Insights arriving in two shapes | both `total_value` and per-day `values` arrays are summed correctly |

## Run the demo

```bash
git clone https://github.com/dkautomation23/instagram-metrics-reader.git
cd instagram-metrics-reader
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run_demo.py
```

Python 3.11. The demo replays the recorded Graph responses in `fixtures/`
(two creators plus a Page with no Instagram linked).

### Real output (trimmed)

```console
1. Facebook Login URL - read permissions only
   scopes: instagram_basic, instagram_manage_insights, pages_read_engagement, pages_show_list
   No publishing scope is requested, so this app can never post.

2. Token exchange: code -> short-lived -> long-lived (60 days)
   {"token": "…real", "expires_at": "2026-10-18T12:09:18Z", "status": "ok", ...}
   needs refresh now?       False
   needs refresh in 55 days? True

3. Resolve Instagram Business accounts through Facebook Pages
   page 'Lena Fitness Studio' -> @lena.moves (ig_id 17841400000000001, 48,210 followers)
   page 'Nordic Travel Diaries' -> @nordic.diaries (ig_id 17841400000000002, 12,940 followers)
   the third Page has no linked Instagram account and is skipped, not failed

4. Metrics (followers, reach, engagement)
   account            followers     reach   views  eng.rate  freshness
   @lena.moves           48,210   187,432   5,124     3.92%  fresh (live)
   @nordic.diaries       12,940    13,320     673     3.77%  fresh (live)

5. Caching keeps the app off Meta's rate limits
   second read: 0 Graph calls, sources = ['cache', 'cache']
   cache: {"hits": 2, "misses": 2}

6. Rate limiting is retried with backoff, not crashed on
   answered after 3 attempts (2x HTTP 429), retries counted: 2
   X-App-Usage seen: {"call_count": 97, "total_time": 23, "total_cputime": 18}

7. Read-only is enforced in code, not in a promise
   client.post(...) -> this integration is read-only: it requests no write permissions
                       and implements no writes

8. A dead token makes data STALE - it never passes for current
   @lena.moves: followers=48,210 reach=187,432
   is_stale=True  source=cache_stale  as_of=2026-08-19T12:09:46Z  age=0.0h
   warning: live read failed: HTTP 400 code=190: Error validating access token: session expired
   warning: data is 0.0h old; reconnect the account to refresh

9. An account that was never read has nothing to show
   no cache + dead token -> HTTP 400 code=190: Error validating access token: session expired
   (zeros are not invented; the caller decides what to display)
```

### CLI

```bash
python -m ig_metrics login-url                 # the URL to send a creator to
python -m ig_metrics read                      # demo mode: no token needed
python -m ig_metrics read --token <user_token> --json
```

## Meta App Review — start this early

Nothing above works for accounts outside your own until Meta approves the app.
This is the part that delays launches by weeks, so plan it before writing the
integration, not after.

**What needs review.** Every permission this project uses is review-gated for
accounts you do not own:

| Permission | Why this project needs it |
| --- | --- |
| `instagram_basic` | read the account's id, username, follower count |
| `instagram_manage_insights` | read reach, profile views, accounts engaged |
| `pages_read_engagement` | read the Page the Instagram account is linked to |
| `pages_show_list` | list the Pages the logged-in user administers |

Before approval you can only read Instagram accounts whose Facebook users have
a role on the app (admin, developer, tester) — enough to build and demo, not
enough to onboard customers.

**What Meta asks for.** A Business Verification of the legal entity (documents,
usually the slowest step); a Data Use Checkup and a privacy policy URL that
actually describes what you store; a screencast showing the full flow — a real
person logging in, granting each permission, and the data appearing in your
product; and a written justification per permission. Reviews commonly take one
to three weeks, and a rejection restarts the queue, so a rushed screencast is
expensive.

**Practical consequences for the build:** request the minimum scopes (adding one
later means another review); make the login flow work end to end before
recording; keep the app in a state a reviewer can reproduce; and treat "we get
approved next week" as optimistic when quoting a deadline.

## Project layout

```
ig_metrics/
  config.py     settings + the read-only scope list
  oauth.py      login URL, code -> short -> long-lived token, refresh, reconnect flag
  graph.py      GET-only Graph client: fixtures, retries, rate limits, write guard
  accounts.py   Pages -> Instagram Business accounts, with actionable errors
  metrics.py    followers/reach/engagement + freshness verdict
  cache.py      SQLite TTL cache
  cli.py        login-url / read
fixtures/       recorded Graph API responses (two creators, one unlinked Page)
tests/          31 tests
run_demo.py     the whole read path, offline
```

## Tests

```bash
pytest
```

```console
...............................                                          [100%]
31 passed in 4.62s
```

Covers scope contents (and that no write scope ever appears), token exchange and
refresh including a failed one, fixture replay, retry on 429 versus no retry on
a dead token, Page resolution and its three failure messages, the engagement
formula, the divide-by-zero case, cache hits, TTL expiry, and both staleness
paths. No network calls.

## License

MIT — see [LICENSE](LICENSE).
