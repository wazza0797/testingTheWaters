from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.timeframe import timeframe_to_timedelta


@dataclass(frozen=True, slots=True)
class BarGap:
    """One stretch of missing bars in a chronologically-sorted series: `after`
    is the last bar seen before the gap, `before` is the first bar seen once
    data resumes, and `missing_count` is how many bars *should* have existed
    in between at the expected timeframe interval.
    """

    after: datetime
    before: datetime
    missing_count: int


def find_gaps(bars: Sequence[Bar], timeframe: str) -> list[BarGap]:
    """Scan chronologically-sorted `bars` for stretches where consecutive
    timestamps are spaced by more than one `timeframe` interval apart.

    A gap doesn't necessarily mean bad data — exchanges have brief outages,
    and some venues omit rather than zero-fill genuinely empty candles on
    thin pairs. But a *silent* gap in backtest input is exactly the kind of
    thing that can make an equity curve look better or worse than it should
    for reasons that have nothing to do with the strategy (see
    docs/architecture.md's "Known Limitations"). This makes gaps visible
    instead of silent — callers (`main.py`'s `download-data`/`backtest`
    commands) decide what to do with the result; this function deliberately
    never fails loud or attempts to fill/interpolate missing bars itself.

    `bars` is assumed already sorted ascending by timestamp (true of
    everything `ParquetMarketDataRepository.load_bars` yields).
    """
    if len(bars) < 2:
        return []

    interval = timeframe_to_timedelta(timeframe)
    gaps: list[BarGap] = []
    for previous, current in zip(bars, bars[1:], strict=False):
        delta = current.timestamp - previous.timestamp
        if delta > interval:
            missing_count = (delta // interval) - 1
            gaps.append(
                BarGap(
                    after=previous.timestamp, before=current.timestamp, missing_count=missing_count
                )
            )
    return gaps
