#!/usr/bin/env python3
"""Connors-style RSI(2) short-term equity-index reversal (long-only).

Bull-regime filter (close > SMA200), enter when RSI(2) freshly drops below
an extreme threshold, exit when close reclaims SMA5, breaches SMA200, or
hits a bar-count time stop. ATR is attached on BUY signals for downstream
ATR-risk sizing (see `PassThroughRiskEngine`); it is not used as a stop.

Designed for daily cash-index CFDs (e.g. IG US 500) with
`use_next_bar_open: true` so the close signal fills on the next open.

Overnight financing / dividends are not modelled by this strategy.
"""

from __future__ import annotations

import math
from collections import deque

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.domain.ports.strategy import StrategyContext

_PLACEHOLDER_STRATEGY_NAME = "connors_rsi2"


class ConnorsRsi2Strategy:
    """Long-only Connors RSI(2) mean-reversion sleeve for equity indices."""

    def __init__(
        self,
        rsi_period: int = 2,
        rsi_threshold: float = 10.0,
        sma_regime_period: int = 200,
        sma_exit_period: int = 5,
        time_stop_bars: int = 10,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        risk_pct: float = 0.01,
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

        self._rsi_period = rsi_period
        self._rsi_threshold = float(rsi_threshold)
        self._sma_regime_period = sma_regime_period
        self._sma_exit_period = sma_exit_period
        self._time_stop_bars = time_stop_bars
        self._atr_period = atr_period
        self._atr_stop_mult = float(atr_stop_mult)
        self._risk_pct = float(risk_pct)
        need = max(sma_regime_period, atr_period, rsi_period + 2, sma_exit_period) + 2
        self._lookback = lookback if lookback is not None else need
        if self._lookback < need:
            raise ValueError(f"lookback ({self._lookback}) must be >= {need}")

        self._bars: deque[Bar] = deque(maxlen=self._lookback)
        self._prev_rsi: float | None = None
        self._bars_in_trade: int = 0
        self._was_long: bool = False

    def on_start(self, ctx: StrategyContext) -> None:
        self._bars.clear()
        self._prev_rsi = None
        self._bars_in_trade = 0
        self._was_long = False

    def on_stop(self, ctx: StrategyContext) -> None:
        return None

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]:
        self._bars.append(bar)
        bars = list(self._bars)
        if len(bars) < self._sma_regime_period:
            return []

        rsi = ctx.indicator("rsi", bars, period=self._rsi_period)
        sma200 = ctx.indicator("sma", bars, period=self._sma_regime_period)
        sma5 = ctx.indicator("sma", bars, period=self._sma_exit_period)
        atr = ctx.indicator("atr", bars, period=self._atr_period)
        close = float(bar.close)

        position = ctx.position_for(bar.symbol)
        is_long = position is not None and position.quantity > 0

        if is_long:
            if self._was_long:
                self._bars_in_trade += 1
            else:
                self._bars_in_trade = 1
        else:
            self._bars_in_trade = 0
        self._was_long = is_long

        signals: list[Signal] = []
        if is_long:
            exit_reason: str | None = None
            if not math.isnan(sma200) and close < sma200:
                exit_reason = "sma200_stop"
            elif not math.isnan(sma5) and close > sma5:
                exit_reason = "sma5_exit"
            elif self._bars_in_trade >= self._time_stop_bars:
                exit_reason = "time_stop"
            if exit_reason is not None:
                signals.append(
                    Signal(
                        symbol=bar.symbol,
                        signal_type=SignalType.CLOSE,
                        strategy_name=_PLACEHOLDER_STRATEGY_NAME,
                        timestamp=bar.timestamp,
                        metadata={"reason": exit_reason, "bars_in_trade": self._bars_in_trade},
                    )
                )
        else:
            in_bull = not math.isnan(sma200) and close > sma200
            rsi_ok = not math.isnan(rsi) and rsi < self._rsi_threshold
            prev = self._prev_rsi
            fresh = prev is None or math.isnan(prev) or prev >= self._rsi_threshold
            if in_bull and rsi_ok and fresh and not math.isnan(atr) and atr > 0:
                signals.append(
                    Signal(
                        symbol=bar.symbol,
                        signal_type=SignalType.BUY,
                        strategy_name=_PLACEHOLDER_STRATEGY_NAME,
                        timestamp=bar.timestamp,
                        metadata={
                            "sizing": "atr_risk",
                            "atr": atr,
                            "atr_period": self._atr_period,
                            "atr_stop_mult": self._atr_stop_mult,
                            "risk_pct": self._risk_pct,
                            "rsi": rsi,
                            "rsi_threshold": self._rsi_threshold,
                        },
                    )
                )

        self._prev_rsi = rsi
        return signals
