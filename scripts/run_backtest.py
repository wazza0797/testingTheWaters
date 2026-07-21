#!/usr/bin/env python3
"""Run a strategy backtest.

Thin wrapper around `trading-platform backtest`. Implemented in Milestone 4.
"""

from __future__ import annotations

from trading_platform.main import app

if __name__ == "__main__":
    app(["backtest"])
