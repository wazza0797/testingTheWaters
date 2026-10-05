#!/usr/bin/env python3
"""Backfill Yahoo daily bars for every market in the diversified-trend registry.

Usage:
    uv run python scripts/backfill_ig_diversified_trend.py
    uv run python scripts/backfill_ig_diversified_trend.py --ids us500,eurusd,gold
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        default="config/research/ig-diversified-trend.yaml",
        type=Path,
    )
    parser.add_argument("--ids", default="", help="Comma-separated market ids (default: all)")
    args = parser.parse_args()
    with args.registry.open(encoding="utf-8") as fh:
        reg: dict[str, Any] = yaml.safe_load(fh)
    markets = list(reg.get("markets") or [])
    if args.ids.strip():
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        markets = [m for m in markets if m["id"] in want]

    failures = 0
    for m in markets:
        yahoo = str(m["yahoo"])
        epic = str(m["epic"])
        cmd = [
            sys.executable,
            "scripts/backfill_ig_external.py",
            "--yahoo",
            yahoo,
            "--epic",
            epic,
            "--timeframe",
            "1d",
        ]
        print(f"\n=== {m['id']} ({yahoo} → {epic}) ===")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            failures += 1
            print(f"FAILED {m['id']}", file=sys.stderr)
    if failures:
        raise SystemExit(f"{failures} backfill(s) failed")


if __name__ == "__main__":
    main()
