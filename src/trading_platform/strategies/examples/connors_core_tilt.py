#!/usr/bin/env python3
"""Connors RSI(2) core+tilt runner — tilt sleeve only.

Core is a separate static long (seeded outside the daily loop). This strategy
only opens/closes the **tilt** add-on:

  - Entry: RSI(2) freshly < threshold, close > SMA200, vol throttle pass
  - Exit: close < SMA200 (regime — intentional; no mid-hold vol exit),
          close > SMA5 (reversion), or time stop

Fills are next-open (`use_next_bar_open` / latency_bars=1). Every signal is
tagged with `leg` / `reason` for the order engine and Discord.

Locked research inputs — do not retune here:
  rsi_threshold=15, vol_throttle=1.5, bare SMA200 exit (no SMA200∧vol /
  vol-only mid-hold — tested decision), Sharpe-weighted risk_pct from config.

DAX 1.5× throttle is precautionary (n=1 covid fill) — documented, not silently
hardened beyond the shared gate.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from decimal import Decimal

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.domain.ports.strategy import StrategyContext

logger = logging.getLogger(__name__)

_PLACEHOLDER_STRATEGY_NAME = "connors_core_tilt"


class ConnorsCoreTiltStrategy:
    """Tilt add-on for the Connors core+tilt runner (one market instance)."""

    def __init__(
        self,
        rsi_period: int = 2,
        rsi_threshold: float = 15.0,
        sma_regime_period: int = 200,
        sma_exit_period: int = 5,
        time_stop_bars: int = 10,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        risk_pct: float = 0.00585,
        vol_fast: int = 10,
        vol_slow: int = 60,
        vol_throttle_ratio: float = 1.5,
        lookback: int | None = None,
    ) -> None:
        if rsi_period < 1:
            raise ValueError(f"rsi_period must be >= 1, got {rsi_period}")
        if not 0.0 < rsi_threshold < 100.0:
            raise ValueError(f"rsi_threshold must be in (0, 100), got {rsi_threshold}")
        if sma_regime_period < 2 or sma_exit_period < 1:
            raise ValueError("sma_regime_period >= 2 and sma_exit_period >= 1 required")
        if time_stop_bars < 1:
            raise ValueError(f"time_stop_bars must be >= 1, got {time_stop_bars}")
        if atr_period < 1:
            raise ValueError(f"atr_period must be >= 1, got {atr_period}")
        if atr_stop_mult <= 0:
            raise ValueError(f"atr_stop_mult must be > 0, got {atr_stop_mult}")
        if not 0.0 < risk_pct <= 1.0:
            raise ValueError(f"risk_pct must be in (0, 1], got {risk_pct}")
        if vol_fast < 2 or vol_slow < vol_fast:
            raise ValueError("vol_fast >= 2 and vol_slow >= vol_fast required")
        if vol_throttle_ratio <= 0:
            raise ValueError(f"vol_throttle_ratio must be > 0, got {vol_throttle_ratio}")

        self._rsi_period = rsi_period
        self._rsi_threshold = float(rsi_threshold)
        self._sma_regime_period = sma_regime_period
        self._sma_exit_period = sma_exit_period
        self._time_stop_bars = time_stop_bars
        self._atr_period = atr_period
        self._atr_stop_mult = float(atr_stop_mult)
        self._risk_pct = float(risk_pct)
        self._vol_fast = vol_fast
        self._vol_slow = vol_slow
        self._vol_throttle_ratio = float(vol_throttle_ratio)
        need = max(
            sma_regime_period,
            atr_period,
            rsi_period + 2,
            sma_exit_period,
            vol_fast + vol_slow + 2,
        )
        self._lookback = lookback if lookback is not None else need
        if self._lookback < need:
            raise ValueError(f"lookback ({self._lookback}) must be >= {need}")

        self._bars: deque[Bar] = deque(maxlen=self._lookback)
        self._prev_rsi: float | None = None
        self._bars_in_tilt: int = 0
        self._tilt_open: bool = False
        self._tilt_qty: Decimal = Decimal("0")
        self._pending_tilt_entry: bool = False
        self._pending_tilt_exit: bool = False
        self._pending_bars: int = 0

    def on_start(self, ctx: StrategyContext) -> None:
        self._bars.clear()
        self._prev_rsi = None
        self._bars_in_tilt = 0
        self._tilt_open = False
        self._tilt_qty = Decimal("0")
        self._pending_tilt_entry = False
        self._pending_tilt_exit = False
        self._pending_bars = 0
        # Rehydrate tilt state from leg book when available (demo/live resume).
        tilt_qty = _leg_qty(ctx, ctx.symbol, "tilt")
        if tilt_qty > 0:
            self._tilt_open = True
            self._tilt_qty = tilt_qty

    def on_stop(self, ctx: StrategyContext) -> None:
        return None

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]:
        self._bars.append(bar)
        bars = list(self._bars)
        if len(bars) < self._sma_regime_period:
            return []

        # Optimistic reconcile: once a bar arrives after we queued entry/exit,
        # assume the next-open fill landed and sync from leg book if present.
        if self._pending_tilt_entry or self._pending_tilt_exit:
            self._pending_bars += 1
        self._reconcile_pending(ctx)

        rsi = ctx.indicator("rsi", bars, period=self._rsi_period)
        sma200 = ctx.indicator("sma", bars, period=self._sma_regime_period)
        sma5 = ctx.indicator("sma", bars, period=self._sma_exit_period)
        atr = ctx.indicator("atr", bars, period=self._atr_period)
        vol_ratio = _vol_ratio(
            bars,
            vol_fast=self._vol_fast,
            vol_slow=self._vol_slow,
        )
        close = float(bar.close)

        signals: list[Signal] = []

        if self._tilt_open and not self._pending_tilt_exit:
            self._bars_in_tilt += 1
            exit_reason: str | None = None
            if not math.isnan(sma200) and close < sma200:
                exit_reason = "regime_exit"
            elif not math.isnan(sma5) and close > sma5:
                exit_reason = "reversion_exit"
            elif self._bars_in_tilt >= self._time_stop_bars:
                exit_reason = "time_stop"
            if exit_reason is not None:
                meta: dict[str, object] = {
                    "leg": "tilt",
                    "reason": exit_reason,
                    "bars_in_tilt": self._bars_in_tilt,
                    "vol_ratio": vol_ratio,
                }
                if self._tilt_qty > 0:
                    meta["close_qty"] = str(self._tilt_qty)
                signals.append(
                    Signal(
                        symbol=bar.symbol,
                        signal_type=SignalType.CLOSE,
                        strategy_name=_PLACEHOLDER_STRATEGY_NAME,
                        timestamp=bar.timestamp,
                        metadata=meta,
                    )
                )
                self._pending_tilt_exit = True
                self._pending_bars = 0
                if exit_reason == "regime_exit":
                    logger.warning(
                        "tilt_regime_exit_queued",
                        extra={
                            "symbol": bar.symbol,
                            "close": close,
                            "sma200": sma200,
                            "vol_ratio": vol_ratio,
                        },
                    )
        elif (
            not self._tilt_open
            and not self._pending_tilt_entry
            and not math.isnan(sma200)
            and close > sma200
        ):
            rsi_ok = not math.isnan(rsi) and rsi < self._rsi_threshold
            prev = self._prev_rsi
            fresh = prev is None or math.isnan(prev) or prev >= self._rsi_threshold
            if rsi_ok and fresh and not math.isnan(atr) and atr > 0:
                if not math.isnan(vol_ratio) and vol_ratio >= self._vol_throttle_ratio:
                    logger.info(
                        "tilt_skipped_vol_throttle",
                        extra={
                            "symbol": bar.symbol,
                            "vol_ratio": vol_ratio,
                            "cap": self._vol_throttle_ratio,
                            "rsi": rsi,
                        },
                    )
                    # No order — low-priority log only (Discord can subscribe later).
                else:
                    signals.append(
                        Signal(
                            symbol=bar.symbol,
                            signal_type=SignalType.BUY,
                            strategy_name=_PLACEHOLDER_STRATEGY_NAME,
                            timestamp=bar.timestamp,
                            metadata={
                                "leg": "tilt",
                                "reason": "entry",
                                "sizing": "atr_risk",
                                "atr": atr,
                                "atr_period": self._atr_period,
                                "atr_stop_mult": self._atr_stop_mult,
                                "risk_pct": self._risk_pct,
                                "rsi": rsi,
                                "rsi_threshold": self._rsi_threshold,
                                "vol_ratio": vol_ratio,
                            },
                        )
                    )
                    self._pending_tilt_entry = True
                    self._pending_bars = 0

        self._prev_rsi = rsi
        return signals

    def _reconcile_pending(self, ctx: StrategyContext) -> None:
        """Sync optimistic pending flags from the leg book after fills."""
        book_tilt = _leg_qty(ctx, ctx.symbol, "tilt")
        has_leg_book = getattr(ctx, "leg_book", None) is not None

        if self._pending_tilt_entry:
            if book_tilt > 0:
                self._tilt_open = True
                self._tilt_qty = book_tilt
                self._bars_in_tilt = 0
                self._pending_tilt_entry = False
                self._pending_bars = 0
            elif not has_leg_book and self._pending_bars >= 1:
                # Tilt-only account (no leg book): assume next-open fill landed.
                # close_qty stays 0 → CLOSE flats the whole symbol (safe iff no core).
                self._tilt_open = True
                self._bars_in_tilt = 0
                self._pending_tilt_entry = False
                self._pending_bars = 0

        if self._pending_tilt_exit:
            cleared = (has_leg_book and book_tilt <= 0) or (
                not has_leg_book and self._pending_bars >= 1
            )
            if cleared:
                self._tilt_open = False
                self._tilt_qty = Decimal("0")
                self._bars_in_tilt = 0
                self._pending_tilt_exit = False
                self._pending_bars = 0


def _leg_qty(ctx: StrategyContext, symbol: str, leg: str) -> Decimal:
    """Read leg quantity when the context exposes `leg_qty`; else 0."""
    getter = getattr(ctx, "leg_qty", None)
    if getter is None:
        return Decimal("0")
    try:
        return Decimal(str(getter(symbol, leg)))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal("0")


def _vol_ratio(bars: list[Bar], *, vol_fast: int, vol_slow: int) -> float:
    """rv(fast) / mean(rv_fast, slow) — matches research throttle definition."""
    need = vol_fast + vol_slow
    if len(bars) < need + 1:
        return float("nan")
    closes = [float(b.close) for b in bars]
    rets: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        rets.append((closes[i] / prev - 1.0) if prev else 0.0)
    if len(rets) < need:
        return float("nan")

    def _std(window: list[float]) -> float:
        n = len(window)
        if n < 2:
            return float("nan")
        mean = sum(window) / n
        var = sum((x - mean) ** 2 for x in window) / n  # ddof=0, like research
        return math.sqrt(var)

    rv_series: list[float] = []
    for i in range(vol_fast - 1, len(rets)):
        rv_series.append(_std(rets[i - vol_fast + 1 : i + 1]))
    if len(rv_series) < vol_slow:
        return float("nan")
    rv_fast_now = rv_series[-1]
    rv_slow_mean = sum(rv_series[-vol_slow:]) / vol_slow
    if rv_slow_mean <= 0 or math.isnan(rv_fast_now):
        return float("nan")
    return rv_fast_now / rv_slow_mean
