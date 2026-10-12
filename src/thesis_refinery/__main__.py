"""Allow ``python -m thesis_refinery`` as an alternative to the console script."""

from __future__ import annotations

from thesis_refinery.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
