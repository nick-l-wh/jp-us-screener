"""Same command as `python -m jp_us_screener update`. This file does not schedule a run."""

from __future__ import annotations

import sys

from jp_us_screener.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["update", *sys.argv[1:]]))
