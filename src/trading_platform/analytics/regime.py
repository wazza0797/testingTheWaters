from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from trading_platform.analytics.benchmark import buy_and_hold_return_pct
from trading_platform.analytics.metrics import max_drawdown_pct
from trading_platform.analytics.trades import RoundTrip
from trading_platform.backtesting.result import EquityPoint
from trading_platform.domain.models.bar import Bar
from trading_platform.indicators.sma import compute_sma

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class MarketRegime(StrEnum):
    BULL = "bull"
    BEAR = "bear"
    CHOP = "chop"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RegimePeriodRow:
    """One row in a regime-breakdown table (calendar or market)."""

    label: str
    return_pct: Decimal
    max_drawdown_pct: Decimal
    round_trip_count: int
    buy_and_hold_return_pct: Decimal | None


def calendar_quarter_label(ts: datetime) -> str:
    quarter = (ts.month - 1) // 3 + 1
    return f"{ts.year}-Q{quarter}"


def calendar_year_label(ts: datetime) -> str:
    return str(ts.year)


def calendar_splits(
    equity_curve: Sequence[EquityPoint],
    round_trips: Sequence[RoundTrip],
    bars: Sequence[Bar],
    *,
    by: str = "quarter",
) -> tuple[RegimePeriodRow, ...]:
    """Split performance by calendar period (`quarter` or `year`)."""
    if by not in {"quarter", "year"}:
        raise ValueError(f"by must be 'quarter' or 'year', got {by!r}")
    label_fn = calendar_quarter_label if by == "quarter" else calendar_year_label

    equity_by: dict[str, list[EquityPoint]] = defaultdict(list)
    for point in equity_curve:
        equity_by[label_fn(point.timestamp)].append(point)

    trips_by: dict[str, list[RoundTrip]] = defaultdict(list)
    for trip in round_trips:
        trips_by[label_fn(trip.exit_time)].append(trip)

    labels = sorted(set(equity_by) | set(trips_by))
    rows: list[RegimePeriodRow] = []
    for label in labels:
        points = equity_by.get(label, [])
        trips = trips_by.get(label, [])
        return_pct = _period_return(points)
        dd = max_drawdown_pct(points) if points else _ZERO
        period_start, period_end = _label_bounds(label, by)
        bh = buy_and_hold_return_pct(bars, start=period_start, end=period_end)
        rows.append(
            RegimePeriodRow(
                label=label,
                return_pct=return_pct,
                max_drawdown_pct=dd,
                round_trip_count=len(trips),
                buy_and_hold_return_pct=bh,
            )
        )
    return tuple(rows)


def market_regime_labels(
    bars: Sequence[Bar],
    *,
    sma_period: int = 200,
) -> tuple[MarketRegime, ...]:
    """Classify each bar as bull / bear / chop / unknown (warmup).

    Rule (single-symbol):
    - `bull`: close > SMA and SMA slope positive
    - `bear`: close < SMA and SMA slope negative
    - `chop`: everything else with a defined SMA
    - `unknown`: first `sma_period - 1` bars (insufficient history)
    """
    if sma_period < 2:
        raise ValueError(f"sma_period must be >= 2, got {sma_period}")
    if not bars:
        return ()

    import pandas as pd

    closes = pd.Series([float(b.close) for b in bars], dtype="float64")
    sma = compute_sma(closes, sma_period)
    labels: list[MarketRegime] = []
    for i in range(len(bars)):
        sma_i = sma.iloc[i]
        if sma_i != sma_i:  # NaN
            labels.append(MarketRegime.UNKNOWN)
            continue
        close = float(bars[i].close)
        if i == 0 or sma.iloc[i - 1] != sma.iloc[i - 1]:
            labels.append(MarketRegime.CHOP)
            continue
        slope = float(sma_i) - float(sma.iloc[i - 1])
        if close > float(sma_i) and slope > 0:
            labels.append(MarketRegime.BULL)
        elif close < float(sma_i) and slope < 0:
            labels.append(MarketRegime.BEAR)
        else:
            labels.append(MarketRegime.CHOP)
    return tuple(labels)


def market_regime_splits(
    equity_curve: Sequence[EquityPoint],
    round_trips: Sequence[RoundTrip],
    bars: Sequence[Bar],
    *,
    sma_period: int = 200,
) -> tuple[RegimePeriodRow, ...]:
    """Aggregate metrics by market-regime label on each bar.

    Regime `return_pct` / max drawdown are attributed from consecutive
    equity *steps* whose ending bar is in that regime (compounded), not
    first→last on a non-contiguous subsample — which would embed PnL from
    other regimes between visits.
    """
    if not bars:
        return ()
    labels = market_regime_labels(bars, sma_period=sma_period)
    ts_to_regime = {bar.timestamp: label for bar, label in zip(bars, labels, strict=True)}

    trips_by: dict[str, list[RoundTrip]] = defaultdict(list)
    for trip in round_trips:
        regime = _regime_at(ts_to_regime, trip.exit_time)
        trips_by[regime.value].append(trip)

    order = (
        MarketRegime.BULL.value,
        MarketRegime.BEAR.value,
        MarketRegime.CHOP.value,
        MarketRegime.UNKNOWN.value,
    )
    rows: list[RegimePeriodRow] = []
    for label in order:
        trips = trips_by.get(label, [])
        path = _regime_attributed_equity_path(equity_curve, ts_to_regime, label)
        bars_in_regime = [b for b, reg in zip(bars, labels, strict=True) if reg.value == label]
        if not path and not trips and len(bars_in_regime) < 2:
            continue
        bh = _regime_attributed_bar_return(bars, labels, label)
        return_pct = _ZERO
        if len(path) >= 2 and path[0].equity != 0:
            return_pct = (path[-1].equity - path[0].equity) / path[0].equity * _HUNDRED
        rows.append(
            RegimePeriodRow(
                label=label,
                return_pct=return_pct,
                max_drawdown_pct=max_drawdown_pct(path) if path else _ZERO,
                round_trip_count=len(trips),
                buy_and_hold_return_pct=bh,
            )
        )
    return tuple(rows)


def _regime_attributed_equity_path(
    equity_curve: Sequence[EquityPoint],
    ts_to_regime: dict[datetime, MarketRegime],
    target: str,
) -> tuple[EquityPoint, ...]:
    """Rebuild a relative equity path using only steps ending in `target`."""
    if len(equity_curve) < 2:
        return ()
    base = Decimal("100")
    path: list[EquityPoint] = [EquityPoint(equity_curve[0].timestamp, base)]
    level = base
    any_step = False
    for previous, current in zip(equity_curve, equity_curve[1:], strict=False):
        regime = _regime_at(ts_to_regime, current.timestamp)
        if regime.value != target:
            continue
        if previous.equity == 0:
            continue
        level = level * (current.equity / previous.equity)
        path.append(EquityPoint(current.timestamp, level))
        any_step = True
    if not any_step:
        return ()
    return tuple(path)


def _regime_attributed_bar_return(
    bars: Sequence[Bar],
    labels: Sequence[MarketRegime],
    target: str,
) -> Decimal | None:
    """Compound close-to-close returns for steps whose ending bar is in `target`."""
    if len(bars) < 2:
        return None
    compounded = Decimal("1")
    any_step = False
    for previous, current, label in zip(bars, bars[1:], labels[1:], strict=False):
        if label.value != target:
            continue
        if previous.close == 0:
            continue
        compounded *= current.close / previous.close
        any_step = True
    if not any_step:
        return None
    return (compounded - Decimal("1")) * _HUNDRED


def _period_return(points: Sequence[EquityPoint]) -> Decimal:
    if len(points) < 2:
        return _ZERO
    start = points[0].equity
    end = points[-1].equity
    if start == 0:
        return _ZERO
    return (end - start) / start * _HUNDRED


def _regime_at(ts_to_regime: dict[datetime, MarketRegime], ts: datetime) -> MarketRegime:
    if ts in ts_to_regime:
        return ts_to_regime[ts]
    # Nearest prior bar timestamp (fills may land between bar opens).
    prior: datetime | None = None
    for bar_ts in ts_to_regime:
        if bar_ts <= ts and (prior is None or bar_ts > prior):
            prior = bar_ts
    if prior is None:
        return MarketRegime.UNKNOWN
    return ts_to_regime[prior]


def _label_bounds(label: str, by: str) -> tuple[datetime, datetime]:
    """Inclusive start / exclusive end for a calendar label (naive UTC)."""
    from datetime import UTC

    if by == "year":
        year = int(label)
        return (
            datetime(year, 1, 1, tzinfo=UTC),
            datetime(year + 1, 1, 1, tzinfo=UTC),
        )
    year_str, q_str = label.split("-Q")
    year = int(year_str)
    quarter = int(q_str)
    start_month = (quarter - 1) * 3 + 1
    if quarter == 4:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, start_month + 3, 1, tzinfo=UTC)
    return datetime(year, start_month, 1, tzinfo=UTC), end
