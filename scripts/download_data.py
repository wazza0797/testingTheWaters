#!/usr/bin/env python3
"""Download historical OHLCV data.

Thin wrapper around `trading-platform download-data` for direct invocation
(`python scripts/download_data.py ...`) without requiring the package's
console-script entry point to be installed. Implemented in Milestone 1.
"""

from __future__ import annotations

from trading_platform.main import app

if __name__ == "__main__":
    app(["download-data"])
