from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from trading_platform.domain.models.bar import Bar


def closes_from_bars(bars: Sequence[Bar]) -> pd.Series:
    """Extract closing prices from a chronological bar sequence as a
    `float64` Series indexed by bar timestamp — the boundary where `Decimal`
    domain values are deliberately converted to `float64` for indicator math
    (see `indicators/__init__.py` docstring for why).

    Callers are responsible for passing bars already sorted by timestamp;
    this function does not sort or validate ordering.
    """
    return pd.Series(
        [float(bar.close) for bar in bars],
        index=[bar.timestamp for bar in bars],
        dtype="float64",
        name="close",
    )


def ohlc_from_bars(bars: Sequence[Bar]) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Extract high/low/close Series from bars for OHLC indicators (ATR).

    Same float64 boundary as `closes_from_bars`; returns `(high, low, close)`
    aligned on bar timestamps.
    """
    index = [bar.timestamp for bar in bars]
    high = pd.Series([float(bar.high) for bar in bars], index=index, dtype="float64", name="high")
    low = pd.Series([float(bar.low) for bar in bars], index=index, dtype="float64", name="low")
    close = pd.Series(
        [float(bar.close) for bar in bars], index=index, dtype="float64", name="close"
    )
    return high, low, close
