"""Allow `python -m ig_metrics ...`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
