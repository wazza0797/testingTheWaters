#!/usr/bin/env python3
"""Run the paper trading loop.

Thin wrapper around `trading-platform paper`. Implemented in Milestone 6.
"""

from __future__ import annotations

from trading_platform.main import app

if __name__ == "__main__":
    app(["paper"])
