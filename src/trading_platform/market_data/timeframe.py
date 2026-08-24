from __future__ import annotations

from datetime import timedelta

from trading_platform.domain.errors import MarketDataError

_UNIT_TO_TIMEDELTA_KWARG = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def timeframe_to_timedelta(timeframe: str) -> timedelta:
    """Parse an exchange-style timeframe string (`'1m'`, `'5m'`, `'1h'`, `'4h'`,
    `'1d'`, `'1w'`) into the `timedelta` between consecutive bar open times.

    `Bar.timeframe` is otherwise an opaque label passed straight through to
    ccxt (see `exchanges/binance/adapter.py`) — this is the one place that
    gives it an actual duration, so `market_data/gaps.py` has something to
    compare consecutive bar timestamps against.
    """
    if len(timeframe) < 2:
        raise MarketDataError(f"Malformed timeframe string: {timeframe!r}")

    unit = timeframe[-1]
    if unit not in _UNIT_TO_TIMEDELTA_KWARG:
        raise MarketDataError(
            f"Unsupported timeframe unit {unit!r} in {timeframe!r}; expected one of "
            f"{sorted(_UNIT_TO_TIMEDELTA_KWARG)}"
        )

    try:
        amount = int(timeframe[:-1])
    except ValueError as exc:
        raise MarketDataError(f"Malformed timeframe string: {timeframe!r}") from exc
    if amount <= 0:
        raise MarketDataError(f"Timeframe amount must be positive: {timeframe!r}")

    return timedelta(**{_UNIT_TO_TIMEDELTA_KWARG[unit]: amount})
