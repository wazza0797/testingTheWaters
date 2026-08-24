from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.strategies.context import DefaultStrategyContext
from trading_platform.strategies.examples.sma_crossover import SmaCrossoverStrategy

# Hand-verified with fast_period=2, slow_period=3 (see milestone doc for the
# worked SMA table): a golden cross (BUY) at index 5, a death cross (SELL) at
# index 7, and no signal anywhere else — including the plateau at the start
# and end where fast == slow exactly.
_CLOSES = ["100", "100", "100", "100", "100", "200", "50", "50", "50", "50", "50"]
_EXPECTED_SIGNALS_BY_INDEX = {5: SignalType.BUY, 7: SignalType.SELL}


def _make_bars(make_bar) -> list:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        make_bar(timestamp=start + timedelta(hours=i), open_=c, high=c, low=c, close=c)
        for i, c in enumerate(_CLOSES)
    ]


def _run(strategy: SmaCrossoverStrategy, bars: list, ctx: DefaultStrategyContext) -> list[Signal]:
    strategy.on_start(ctx)
    signals: list[Signal] = []
    for bar in bars:
        signals.extend(strategy.on_bar(bar, ctx))
    return signals


class TestConstruction:
    def test_defaults_to_10_and_30(self) -> None:
        strategy = SmaCrossoverStrategy()

        assert strategy.fast_period == 10
        assert strategy.slow_period == 30

    @pytest.mark.parametrize("fast_period,slow_period", [(0, 5), (5, 0), (-1, 5)])
    def test_raises_when_a_period_is_less_than_one(self, fast_period, slow_period) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            SmaCrossoverStrategy(fast_period=fast_period, slow_period=slow_period)

    @pytest.mark.parametrize("fast_period,slow_period", [(10, 10), (30, 10)])
    def test_raises_when_fast_is_not_strictly_less_than_slow(
        self, fast_period, slow_period
    ) -> None:
        with pytest.raises(ValueError, match="must be strictly less than"):
            SmaCrossoverStrategy(fast_period=fast_period, slow_period=slow_period)


class TestCrossoverDetection:
    def test_emits_buy_and_sell_only_on_the_bars_where_the_cross_happens(self, make_bar) -> None:
        bars = _make_bars(make_bar)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)

        signals_by_index: dict[int, list[Signal]] = {}
        strategy.on_start(ctx)
        for i, bar in enumerate(bars):
            signals_by_index[i] = strategy.on_bar(bar, ctx)

        for i, signals in signals_by_index.items():
            if i in _EXPECTED_SIGNALS_BY_INDEX:
                assert len(signals) == 1, f"expected exactly one signal at index {i}"
                assert signals[0].signal_type == _EXPECTED_SIGNALS_BY_INDEX[i]
            else:
                assert signals == [], f"expected no signal at index {i}, got {signals}"

    def test_signal_metadata_carries_fast_and_slow_sma_values(self, make_bar) -> None:
        bars = _make_bars(make_bar)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)

        signals = _run(strategy, bars, ctx)

        buy = next(s for s in signals if s.signal_type == SignalType.BUY)
        assert buy.metadata["fast_sma"] == pytest.approx(150.0)
        assert buy.metadata["slow_sma"] == pytest.approx(400.0 / 3.0)

    def test_signal_carries_symbol_strategy_name_and_bar_timestamp(self, make_bar) -> None:
        bars = _make_bars(make_bar)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)

        signals = _run(strategy, bars, ctx)

        buy = next(s for s in signals if s.signal_type == SignalType.BUY)
        assert buy.symbol == "BTC/USDT"
        assert buy.strategy_name == "sma_crossover"
        assert buy.timestamp == bars[5].timestamp

    def test_flat_price_series_never_signals(self, make_bar) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = [
            make_bar(
                timestamp=start + timedelta(hours=i),
                open_="100",
                high="100",
                low="100",
                close="100",
            )
            for i in range(20)
        ]
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)

        signals = _run(strategy, bars, ctx)

        assert signals == []

    def test_no_signal_while_history_is_insufficient(self, make_bar) -> None:
        bars = _make_bars(make_bar)[:3]  # fewer than slow_period + 1 bars
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)

        signals = _run(strategy, bars, ctx)

        assert signals == []


class TestLifecycle:
    def test_on_start_resets_internal_history(self, make_bar) -> None:
        bars = _make_bars(make_bar)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)
        strategy.on_start(ctx)
        for bar in bars[:6]:  # runs through the golden cross at index 5
            strategy.on_bar(bar, ctx)

        strategy.on_start(ctx)  # reset — history and prev SMA state are gone
        first_bar_signals = strategy.on_bar(bars[0], ctx)

        assert first_bar_signals == []  # can't detect a cross on the very first bar again

    def test_on_stop_does_not_raise(self, make_bar) -> None:
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)

        strategy.on_stop(ctx)  # should not raise even though on_start was never called


class TestDeterminism:
    def test_running_the_same_bars_twice_produces_identical_signals(self, make_bar) -> None:
        bars = _make_bars(make_bar)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        signals_a = _run(SmaCrossoverStrategy(fast_period=2, slow_period=3), bars, ctx)
        signals_b = _run(SmaCrossoverStrategy(fast_period=2, slow_period=3), bars, ctx)

        assert [(s.signal_type, s.timestamp, s.metadata) for s in signals_a] == [
            (s.signal_type, s.timestamp, s.metadata) for s in signals_b
        ]
