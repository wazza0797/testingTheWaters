#!/usr/bin/env python3
"""Backfill OHLCV from Yahoo Finance into the platform Parquet layout for an
IG epic (stand-in when IG demo `/prices` only returns ~20 bars).

Same caveats as the GBPEUR backfill: Yahoo's series tracks the underlying
closely but is **not** IG's own CFD mid. Treat research results as indicative.

Usage:
    uv run python scripts/backfill_ig_external.py \\
        --yahoo '^FTSE' --epic IX.D.FTSE.DAILY.IP --timeframe 1h --range 730d
    uv run python scripts/backfill_ig_external.py \\
        --yahoo GBPEUR=X --epic CS.D.GBPEUR.CFD.IP --timeframe 15m
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from trading_platform.config.settings import Settings
from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.gaps import find_gaps
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository

_EXCHANGE = "ig"
_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; trading-platform-research/1.0)"}

_TIMEFRAME_SECONDS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}
_DEFAULT_RANGE = {
    "15m": "60d",
    "1h": "730d",
    "4h": "730d",
    "1d": "max",
}


def _fetch_chart(
    yahoo: str,
    timeframe: str,
    range_: str | None = None,
    *,
    period1: int | None = None,
    period2: int | None = None,
) -> dict[str, Any]:
    url = _CHART_BASE + quote(yahoo, safe="^=.")
    params: dict[str, str | int] = {"interval": timeframe}
    # Yahoo's chart API quietly downsamples `range=max` + `1d` to ~monthly.
    # Prefer explicit period1/period2 for daily history.
    if period1 is not None and period2 is not None:
        params["period1"] = period1
        params["period2"] = period2
    elif range_ is not None:
        params["range"] = range_
    else:
        params["range"] = "1y"
    response = httpx.get(
        url,
        params=params,
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


def _parse_bars(result: dict[str, Any], *, epic: str, timeframe: str) -> list[Bar]:
    interval_seconds = _TIMEFRAME_SECONDS[timeframe]
    timestamps = result.get("timestamp") or []
    quote_block = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote_block.get("open") or []
    highs = quote_block.get("high") or []
    lows = quote_block.get("low") or []
    closes = quote_block.get("close") or []
    volumes = quote_block.get("volume") or [0] * len(timestamps)

    bars: dict[datetime, Bar] = {}
    for i, ts in enumerate(timestamps):
        o, h, lo, c = opens[i], highs[i], lows[i], closes[i]
        if o is None or h is None or lo is None or c is None:
            continue
        open_d, high_d, low_d, close_d = (Decimal(str(x)) for x in (o, h, lo, c))
        high_d = max(high_d, open_d, close_d, low_d)
        low_d = min(low_d, open_d, close_d, high_d)
        volume_raw = volumes[i] if i < len(volumes) else 0
        timestamp = datetime.fromtimestamp(ts, tz=UTC)
        bars[timestamp] = Bar(
            symbol=epic,
            timeframe=timeframe,
            timestamp=timestamp,
            open=open_d,
            high=high_d,
            low=low_d,
            close=close_d,
            volume=Decimal(str(volume_raw or 0)),
        )
    ordered = [bars[ts] for ts in sorted(bars)]
    if len(ordered) >= 2:
        last_gap = (ordered[-1].timestamp - ordered[-2].timestamp).total_seconds()
        if last_gap < interval_seconds:
            ordered = ordered[:-1]
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yahoo", required=True, help="Yahoo ticker, e.g. '^FTSE' or GBPEUR=X")
    parser.add_argument(
        "--epic", required=True, help="IG epic to store under, e.g. IX.D.FTSE.DAILY.IP"
    )
    parser.add_argument("--timeframe", choices=sorted(_TIMEFRAME_SECONDS), default="1h")
    parser.add_argument("--range", default=None, help=f"Yahoo range (defaults: {_DEFAULT_RANGE})")
    parser.add_argument(
        "--period1",
        type=int,
        default=None,
        help="Unix seconds start (for 1d prefer this over range=max).",
    )
    parser.add_argument(
        "--period2",
        type=int,
        default=None,
        help="Unix seconds end (default: now when --period1 set or timeframe=1d).",
    )
    args = parser.parse_args()
    timeframe: str = args.timeframe
    range_: str | None = args.range
    period1 = args.period1
    period2 = args.period2
    # Daily default: explicit epoch window (Yahoo downsamples range=max on 1d).
    if timeframe == "1d" and period1 is None and (range_ is None or range_ == "max"):
        period1 = 315532800  # 1980-01-01
        period2 = period2 or int(datetime.now(tz=UTC).timestamp())
        range_ = None
    elif period1 is not None and period2 is None:
        period2 = int(datetime.now(tz=UTC).timestamp())
    elif range_ is None and period1 is None:
        range_ = _DEFAULT_RANGE[timeframe]

    label = (
        f"period1={period1}&period2={period2}"
        if period1 is not None
        else f"range={range_}"
    )
    print(
        f"Fetching {args.yahoo} @ {timeframe} ({label}) -> "
        f"{_EXCHANGE}/{args.epic} (Yahoo stand-in for IG CFD — see docstring)..."
    )
    result = _fetch_chart(
        args.yahoo, timeframe, range_, period1=period1, period2=period2
    )
    bars = _parse_bars(result, epic=args.epic, timeframe=timeframe)
    if not bars:
        print("No bars parsed — nothing saved.", file=sys.stderr)
        raise SystemExit(1)

    settings = Settings()
    repository = ParquetMarketDataRepository(Path(settings.data_dir), exchange=_EXCHANGE)
    repository.save_bars(args.epic, timeframe, bars)
    print(f"Saved {len(bars)} bar(s) for {_EXCHANGE}/{args.epic}@{timeframe}.")
    print(f"Range: {bars[0].timestamp.isoformat()} -> {bars[-1].timestamp.isoformat()}")

    gaps = find_gaps(bars, timeframe)
    if gaps:
        total_missing = sum(gap.missing_count for gap in gaps)
        print(
            f"NOTE: {len(gaps)} gap(s) (~{total_missing} missing bar(s) total) — "
            "weekends/holidays expected for cash equity indices."
        )


if __name__ == "__main__":
    main()
