#!/usr/bin/env python3
"""One-off backfill: spot GBP/EUR history from Yahoo Finance -> the same
Parquet layout `download-data` would populate for `ig`/`CS.D.GBPEUR.CFD.IP`.

**Why this exists:** IG's *demo* `/prices` REST endpoint was found (see the
GBPEUR 15m long/short strategy research plan) to serve only a fixed ~20-bar
(~5 hour) window of history for `CS.D.GBPEUR.CFD.IP` — and every other IG
epic tested — regardless of the requested date range. That's nowhere near
enough for backtesting (`config/ig-gbpeur.yaml`'s own `analytics.min_bars` is
500). This script substitutes **spot GBP/EUR** from Yahoo Finance's public
(unofficial, no API key) chart endpoint as a stand-in.

**Known limitation — read before trusting results on this data:** spot
GBP/EUR is *not* guaranteed identical to IG's own CFD mid-price feed. CFDs on
major FX pairs track spot extremely closely, but IG's quote reflects their
own liquidity-provider aggregation and spread/skew, which can differ from
Yahoo's feed at the bar level (timing, momentary spread widening around
news, weekend/session-boundary handling). Treat conclusions from this data
as *indicative*, not as a substitute for validating a promising recipe
against IG's own feed once more real demo history has accumulated.

Yahoo's public intraday chart API caps 15-minute bars at roughly the
trailing 60-90 days; 1h bars go much deeper (empirically ~2 years with
`range=730d`).

Usage:
    uv run python scripts/backfill_gbpeur_external.py
    uv run python scripts/backfill_gbpeur_external.py --timeframe 1h --range 730d
    uv run python scripts/backfill_gbpeur_external.py --timeframe 15m --range 60d
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from trading_platform.config.settings import Settings
from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.gaps import find_gaps
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository

_YAHOO_TICKER = "GBPEUR=X"
_EXCHANGE = "ig"
_SYMBOL = "CS.D.GBPEUR.CFD.IP"
_CHART_URL = f"https://query1.finance.yahoo.com/v8/finance/chart/{_YAHOO_TICKER}"
# Yahoo's unofficial API 403s on the default httpx/requests User-Agent.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; trading-platform-research/1.0)"}

_TIMEFRAME_SECONDS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
}
_DEFAULT_RANGE = {
    "15m": "60d",
    "1h": "730d",
    "4h": "730d",
}


def _fetch_chart(timeframe: str, range_: str) -> dict[str, Any]:
    response = httpx.get(
        _CHART_URL,
        params={"interval": timeframe, "range": range_},
        headers=_HEADERS,
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        error = (payload.get("chart") or {}).get("error")
        raise RuntimeError(f"Yahoo chart API returned no result (error={error!r})")
    first_result: dict[str, Any] = result[0]
    return first_result


def _parse_bars(result: dict[str, Any], timeframe: str) -> list[Bar]:
    interval_seconds = _TIMEFRAME_SECONDS[timeframe]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or [0] * len(timestamps)

    bars: dict[datetime, Bar] = {}
    for i, ts in enumerate(timestamps):
        o, h, lo, c = opens[i], highs[i], lows[i], closes[i]
        if o is None or h is None or lo is None or c is None:
            continue  # session gap (weekend/holiday) — Yahoo pads with nulls

        open_d, high_d, low_d, close_d = (Decimal(str(x)) for x in (o, h, lo, c))
        # Defensive clamp: third-party feeds occasionally have float noise
        # that puts open/close a hair outside [low, high] — Bar validates
        # this strictly, so widen [low, high] to fit rather than crash.
        high_d = max(high_d, open_d, close_d, low_d)
        low_d = min(low_d, open_d, close_d, high_d)
        volume_raw = volumes[i] if i < len(volumes) else 0

        timestamp = datetime.fromtimestamp(ts, tz=UTC)
        bars[timestamp] = Bar(
            symbol=_SYMBOL,
            timeframe=timeframe,
            timestamp=timestamp,
            open=open_d,
            high=high_d,
            low=low_d,
            close=close_d,
            volume=Decimal(str(volume_raw or 0)),
        )
    ordered = [bars[ts] for ts in sorted(bars)]
    # Yahoo often appends a live / incomplete candle. Drop it when the gap
    # from the previous bar is shorter than the nominal timeframe — do *not*
    # filter on epoch alignment (`ts % interval`), which breaks after DST
    # shifts on 4h FX sessions (bars land on :00 UTC offsets that are not
    # multiples of 14400).
    if len(ordered) >= 2:
        last_gap = (ordered[-1].timestamp - ordered[-2].timestamp).total_seconds()
        if last_gap < interval_seconds:
            ordered = ordered[:-1]
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeframe",
        choices=sorted(_TIMEFRAME_SECONDS),
        default="15m",
        help="Bar size to request and persist (default: 15m).",
    )
    parser.add_argument(
        "--range",
        default=None,
        help=(f"Yahoo chart 'range' query value (e.g. '60d', '730d'). Defaults: {_DEFAULT_RANGE}."),
    )
    args = parser.parse_args()
    timeframe: str = args.timeframe
    range_: str = args.range or _DEFAULT_RANGE[timeframe]

    print(
        f"Fetching {_YAHOO_TICKER} @ {timeframe} (range={range_}) from Yahoo Finance "
        f"as a stand-in for {_EXCHANGE}/{_SYMBOL} (spot FX — see module docstring for caveats)..."
    )
    result = _fetch_chart(timeframe, range_)
    bars = _parse_bars(result, timeframe)
    if not bars:
        print("No bars parsed from the Yahoo response — nothing saved.", file=sys.stderr)
        raise SystemExit(1)

    settings = Settings()
    repository = ParquetMarketDataRepository(Path(settings.data_dir), exchange=_EXCHANGE)
    repository.save_bars(_SYMBOL, timeframe, bars)

    print(f"Saved {len(bars)} bar(s) for {_EXCHANGE}/{_SYMBOL}@{timeframe}.")
    print(f"Range: {bars[0].timestamp.isoformat()} -> {bars[-1].timestamp.isoformat()}")

    gaps = find_gaps(bars, timeframe)
    if gaps:
        total_missing = sum(gap.missing_count for gap in gaps)
        print(
            f"NOTE: {len(gaps)} gap(s) (~{total_missing} missing bar(s) total) — expected "
            "around weekends/market holidays for a spot FX series; large mid-week gaps would "
            "be worth double-checking."
        )


if __name__ == "__main__":
    main()
