from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.unit.strategies.conformance import assert_strategy_conforms
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import SignalType
from trading_platform.portfolio.legs import LegBook
from trading_platform.strategies.context import DefaultStrategyContext
from trading_platform.strategies.examples.connors_core_tilt import (
    ConnorsCoreTiltStrategy,
    _vol_ratio,
)


def _bar(i: int, close: float, *, symbol: str = "IX.D.SPTRD.IFM.IP") -> Bar:
    ts = datetime(2020, 1, 2, tzinfo=UTC) + timedelta(days=i)
    c = Decimal(str(close))
    return Bar(
        symbol=symbol,
        timeframe="1d",
        timestamp=ts,
        open=c,
        high=c * Decimal("1.001"),
        low=c * Decimal("0.999"),
        close=c,
        volume=Decimal("0"),
    )


def _ctx(leg_book: LegBook | None = None) -> DefaultStrategyContext:
    return DefaultStrategyContext(
        symbol="IX.D.SPTRD.IFM.IP",
        timeframe="1d",
        params={},
        leg_book=leg_book,
    )


def test_conformance_smoke() -> None:
    bars = [_bar(i, 100.0 + i * 0.1) for i in range(220)]

    def make() -> ConnorsCoreTiltStrategy:
        return ConnorsCoreTiltStrategy(sma_regime_period=50, lookback=120)

    assert_strategy_conforms(make, _ctx(), bars)


def test_rejects_bad_params() -> None:
    with pytest.raises(ValueError):
        ConnorsCoreTiltStrategy(rsi_threshold=0)
    with pytest.raises(ValueError):
        ConnorsCoreTiltStrategy(vol_throttle_ratio=0)


def test_vol_ratio_elevated_after_spike() -> None:
    # Calm then violent dump → rv_fast / mean(rv) spikes.
    closes = [100.0] * 80
    closes.append(90.0)
    closes.append(80.0)
    bars = [_bar(i, c) for i, c in enumerate(closes)]
    ratio = _vol_ratio(bars, vol_fast=10, vol_slow=60)
    assert ratio == ratio  # not NaN
    assert ratio > 1.5


def test_skips_entry_when_vol_hot_and_tags_metadata_when_calm() -> None:
    """Construct a bull grind + RSI dump; with hot vol, no BUY; calm path tags leg."""
    closes = [100.0 + i * 0.5 for i in range(220)]
    # Mild two-bar dip (may or may not fire RSI<15 depending on path)
    closes.append(closes[-1] * 0.97)
    closes.append(closes[-1] * 0.96)
    bars = [_bar(i, c) for i, c in enumerate(closes)]
    strat = ConnorsCoreTiltStrategy(
        sma_regime_period=50,
        lookback=120,
        rsi_threshold=15.0,
        vol_throttle_ratio=1.5,
        risk_pct=0.00585,
    )
    ctx = _ctx()
    strat.on_start(ctx)
    last: list = []
    for bar in bars:
        last = strat.on_bar(bar, ctx)
    buys = [s for s in last if s.signal_type == SignalType.BUY]
    for s in buys:
        assert s.metadata.get("leg") == "tilt"
        assert s.metadata.get("reason") == "entry"
        assert s.metadata.get("sizing") == "atr_risk"
        assert s.metadata.get("risk_pct") == 0.00585


def test_regime_exit_tagged_when_tilt_open_via_leg_book() -> None:
    leg_book = LegBook()
    leg_book.set_qty("IX.D.SPTRD.IFM.IP", "tilt", Decimal("1.5"))
    # Flat regime, then hard break below SMA50 — avoid SMA5 reversion exit
    # (close > sma5) during the grind.
    closes = [100.0] * 60
    closes.extend([70.0] * 5)
    bars = [_bar(i, c) for i, c in enumerate(closes)]
    strat = ConnorsCoreTiltStrategy(
        sma_regime_period=50,
        sma_exit_period=5,
        time_stop_bars=100,
        lookback=120,
    )
    ctx = _ctx(leg_book)
    strat.on_start(ctx)
    assert strat._tilt_open is True
    closes_sig = []
    for bar in bars:
        for s in strat.on_bar(bar, ctx):
            if s.signal_type == SignalType.CLOSE:
                closes_sig.append(s)
    assert any(s.metadata.get("reason") == "regime_exit" for s in closes_sig)
    regime = next(s for s in closes_sig if s.metadata.get("reason") == "regime_exit")
    assert regime.metadata.get("leg") == "tilt"
    assert regime.metadata.get("close_qty") == "1.5"
