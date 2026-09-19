# Contributing

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pulls in `requests`, `python-dotenv`, and `pytest`.

Each repository gets its own virtual environment. Do not reuse one `.venv` across repos — a shared environment can hide a dependency this project actually needs, and it will only surface later, in CI.

## Tests

```bash
python -m compileall -q .
python -m pytest -q
```

This is the exact sequence CI runs (`.github/workflows/ci.yml`), on Python 3.10 and 3.12.

A new check or bug fix starts with a failing test added first, under `tests/` (pytest), then the code change that makes it pass.

## Commit style

Short, lowercase. Usually `type: description` (`feat` / `test` / `docs` / `chore`), sometimes a plain descriptive sentence. There is no enforced conventional-commits check, but stay consistent with the existing log, for example:

```
feat: read-only Graph client
test: 31 tests over scopes, tokens, retries, resolution, metrics, staleness
docs: README with the flow, real demo output and the App Review section
```

## Pull requests

- Keep the diff scoped to one change.
- CI must pass: byte-compile plus the full test suite, on Python 3.10 and 3.12.
- No secrets or credentials in the diff — no access tokens, app secrets, or `.env` contents.
