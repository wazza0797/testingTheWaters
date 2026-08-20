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
