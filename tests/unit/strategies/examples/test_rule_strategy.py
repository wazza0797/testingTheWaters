from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.unit.strategies.conformance import assert_strategy_conforms
from trading_platform.domain.models.position import Position
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.strategies.context import DefaultStrategyContext
from trading_platform.strategies.examples.rule_strategy import RuleStrategy
from trading_platform.strategies.rules import ConditionError

# Same worked example as SmaCrossoverStrategy's own hand-verified table
# (see tests/unit/strategies/examples/test_sma_crossover.py and the M3
# milestone doc): fast=2/slow=3 SMA golden cross at index 5, death cross at
# index 7, nothing anywhere else.
_CLOSES = ["100", "100", "100", "100", "100", "200", "50", "50", "50", "50", "50"]

_ENTRY = {
    "cross": {
        "left": {"indicator": "sma", "period": 2},
        "right": {"indicator": "sma", "period": 3},
        "direction": "above",
    }
}
_EXIT = {
    "cross": {
        "left": {"indicator": "sma", "period": 2},
        "right": {"indicator": "sma", "period": 3},
        "direction": "below",
    }
}


class _TogglablePositionProvider:
    """Test double letting a test simulate "we just got filled" by flipping
    `position` after observing a BUY/CLOSE signal — no real portfolio/risk
    engine needed to exercise `RuleStrategy`'s flat/in-position gating."""

    def __init__(self) -> None:
        self.position: Position | None = None

    def position_for(self, symbol: str) -> Position | None:
        return self.position


def _make_bars(make_bar, closes: list[str]) -> list:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        make_bar(timestamp=start + timedelta(hours=i), open_=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]


class TestConstruction:
    def test_parses_entry_and_exit_trees(self) -> None:
        RuleStrategy(entry=_ENTRY, exit=_EXIT)  # should not raise

    def test_rejects_unknown_indicator_name(self) -> None:
        bad_entry = {"compare": {"indicator": "not_a_real_indicator", "op": ">", "value": 1}}
        with pytest.raises(ConditionError, match="not_a_real_indicator"):
            RuleStrategy(entry=bad_entry, exit=_EXIT)

    def test_rejects_malformed_entry_shape(self) -> None:
        with pytest.raises(ConditionError):
            RuleStrategy(entry={"nonsense": True}, exit=_EXIT)


class TestEntryExitTransitions:
    def test_emits_buy_and_close_only_on_transition_bars(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)
        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RuleStrategy(entry=_ENTRY, exit=_EXIT, lookback=20)
        strategy.on_start(ctx)

        signals_by_index: dict[int, list[Signal]] = {}
        for i, bar in enumerate(bars):
            signals = strategy.on_bar(bar, ctx)
            signals_by_index[i] = signals
            for signal in signals:
                if signal.signal_type == SignalType.BUY:
                    provider.position = Position(
                        symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=bar.close
                    )
                elif signal.signal_type == SignalType.CLOSE:
                    provider.position = None

        assert [s.signal_type for s in signals_by_index[5]] == [SignalType.BUY]
        assert [s.signal_type for s in signals_by_index[7]] == [SignalType.CLOSE]
        for i, signals in signals_by_index.items():
            if i not in (5, 7):
                assert signals == [], f"expected no signal at index {i}, got {signals}"

    def test_buy_metadata_carries_leaf_trace(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)
        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RuleStrategy(entry=_ENTRY, exit=_EXIT, lookback=20)
        strategy.on_start(ctx)

        buy_signal = None
        for bar in bars:
            for signal in strategy.on_bar(bar, ctx):
                if signal.signal_type == SignalType.BUY:
                    buy_signal = signal

        assert buy_signal is not None
        assert "sma(period=2) crosses above sma(period=3)" in buy_signal.metadata
        assert buy_signal.metadata["sma(period=2) crosses above sma(period=3)"] is True

    def test_does_not_buy_while_already_in_position(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)
        provider = _TogglablePositionProvider()
        provider.position = Position(
            symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
        )
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RuleStrategy(entry=_ENTRY, exit=_EXIT, lookback=20)
        strategy.on_start(ctx)

        all_signals = [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        assert all(s.signal_type != SignalType.BUY for s in all_signals)

    def test_does_not_close_while_flat(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")  # NullPositionProvider
        strategy = RuleStrategy(entry=_ENTRY, exit=_EXIT, lookback=20)
        strategy.on_start(ctx)

        all_signals = [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        assert all(s.signal_type != SignalType.CLOSE for s in all_signals)

    def test_no_signal_while_history_is_insufficient(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)[:3]
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RuleStrategy(entry=_ENTRY, exit=_EXIT, lookback=20)
        strategy.on_start(ctx)

        all_signals = [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        assert all_signals == []

    def test_flat_price_series_never_signals(self, make_bar) -> None:
        bars = _make_bars(make_bar, ["100"] * 15)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RuleStrategy(entry=_ENTRY, exit=_EXIT, lookback=20)
        strategy.on_start(ctx)

        all_signals = [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        assert all_signals == []


class TestMixAndMatchRecipe:
    def test_or_of_two_volume_checks_and_one_volatility_check(self, make_bar) -> None:
        """The whole point of RuleStrategy: this is NOT one-indicator-per-
        category AND — it's two volume leaves OR'd together, ANDed with a
        single volatility leaf, with no trend/momentum gate at all."""
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = [
            make_bar(
                timestamp=start + timedelta(hours=i),
                open_=str(100 + i),
                high=str(105 + i),
                low=str(95 + i),
                close=str(100 + i),
                volume=str(100 if i < 25 else 500),
            )
            for i in range(26)
        ]
        entry = {
            "all": [
                {
                    "any": [
                        {
                            "compare": {
                                "indicator": "rel_volume",
                                "period": 5,
                                "op": ">=",
                                "value": 1.5,
                            }
                        },
                        {
                            "compare": {
                                "indicator": "volume_breakout",
                                "period": 5,
                                "op": "==",
                                "value": 1,
                            }
                        },
                    ]
                },
                {"compare": {"indicator": "atr_pct", "period": 5, "op": ">=", "value": 0.0}},
            ]
        }
        exit_tree = {"compare": {"indicator": "rsi", "period": 5, "op": "<", "value": 0.0}}
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RuleStrategy(entry=entry, exit=exit_tree, lookback=30)
        strategy.on_start(ctx)

        signals = [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        assert any(s.signal_type == SignalType.BUY for s in signals)


class TestLifecycle:
    def test_on_start_resets_state(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RuleStrategy(entry=_ENTRY, exit=_EXIT, lookback=20)

        strategy.on_start(ctx)
        for bar in bars[:6]:
            strategy.on_bar(bar, ctx)

        strategy.on_start(ctx)
        first_bar_signals = strategy.on_bar(bars[0], ctx)
        assert first_bar_signals == []

    def test_on_stop_does_not_raise(self) -> None:
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RuleStrategy(entry=_ENTRY, exit=_EXIT)
        strategy.on_stop(ctx)


class TestDeterminism:
    def test_running_the_same_bars_twice_produces_identical_signals(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        def _run() -> list[Signal]:
            strategy = RuleStrategy(entry=_ENTRY, exit=_EXIT, lookback=20)
            strategy.on_start(ctx)
            return [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        a, b = _run(), _run()
        assert [(s.signal_type, s.timestamp) for s in a] == [
            (s.signal_type, s.timestamp) for s in b
        ]


class TestGenericConformance:
    def test_conforms_to_the_generic_istrategy_behavioral_contract(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        assert_strategy_conforms(
            lambda: RuleStrategy(entry=_ENTRY, exit=_EXIT, lookback=20), ctx, bars
        )
