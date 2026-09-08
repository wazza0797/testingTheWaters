from __future__ import annotations

import pandas as pd

from trading_platform.indicators.roc import compute_roc


def compute_volume_roc(volume: pd.Series, period: int = 10) -> pd.Series:
    """Volume momentum: `compute_roc` applied to `volume` instead of price —
    percentage change in volume over `period` bars. Optional, secondary
    confirmation (see `rel_volume`, the platform's primary volume signal);
    only meaningful on venues where volume itself is meaningful (see the
    asset-class-agnostic note in the composable-strategies milestone doc).

    Returns a `float64` Series aligned to `volume`'s index; same warmup /
    zero-guard behaviour as `compute_roc`.
    """
    return compute_roc(volume, period=period)
