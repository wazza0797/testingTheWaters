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

# Mirror image of _ENTRY/_EXIT for a short-only recipe: enter short on the
# death cross, cover on the golden cross. Against `_CLOSES` this fires SELL
# at index 7 (matching _EXIT's own index) and nothing else while flat.
_SHORT_ENTRY = _EXIT
_SHORT_EXIT = _ENTRY

# `_CLOSES` extended so a second golden cross happens after the short opens
# at index 7 — verified by hand: sma(2)/sma(3) stay equal (50/50) through
# index 11, then close=300 at index 12 pushes sma(2) above sma(3) again.
_CLOSES_WITH_COVER = _CLOSES + ["50", "300", "300"]

# Two simple (non-cross) conditions that can become `True` on the exact
# same bar — `sma(period=1)` is just that bar's own close — used to exercise
# the "both long and short entry edges fire on the same bar" tie-break.
_TIE_LONG_ENTRY = {"compare": {"indicator": "sma", "period": 1, "op": ">", "value": 150}}
_TIE_SHORT_ENTRY = {"compare": {"indicator": "sma", "period": 1, "op": ">", "value": 50}}
_NEVER_TRUE_EXIT = {"compare": {"indicator": "sma", "period": 1, "op": "<", "value": -1}}
# Always-true leaf used when a test only cares about exit/risk behaviour and
# needs a valid entry tree for construction (signals from it are ignored by
# injecting the position directly).
_ALWAYS_TRUE_ENTRY = {"compare": {"indicator": "sma", "period": 1, "op": ">", "value": 0}}


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


class TestLongShortConfig:
    def test_rejects_mixing_legacy_and_long_short_keys(self) -> None:
        with pytest.raises(ValueError, match="cannot mix"):
            RuleStrategy(entry=_ENTRY, exit=_EXIT, short_entry=_SHORT_ENTRY, short_exit=_SHORT_EXIT)

    def test_rejects_long_entry_without_long_exit(self) -> None:
        with pytest.raises(ValueError, match="long_entry"):
            RuleStrategy(long_entry=_ENTRY)

    def test_rejects_short_entry_without_short_exit(self) -> None:
        with pytest.raises(ValueError, match="short_entry"):
            RuleStrategy(short_entry=_SHORT_ENTRY)

    def test_requires_at_least_one_side_configured(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            RuleStrategy()


class TestShortEntryExitTransitions:
    def test_short_entry_fires_sell_only_on_transition_bar(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RuleStrategy(short_entry=_SHORT_ENTRY, short_exit=_SHORT_EXIT, lookback=20)
        strategy.on_start(ctx)

        signals_by_index: dict[int, list[Signal]] = {}
        for i, bar in enumerate(bars):
            signals_by_index[i] = strategy.on_bar(bar, ctx)

        assert [s.signal_type for s in signals_by_index[7]] == [SignalType.SELL]
        for i, signals in signals_by_index.items():
            if i != 7:
                assert signals == [], f"expected no signal at index {i}, got {signals}"

    def test_short_exit_fires_close_once_covered(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES_WITH_COVER)
        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RuleStrategy(short_entry=_SHORT_ENTRY, short_exit=_SHORT_EXIT, lookback=20)
        strategy.on_start(ctx)

        signals_by_index: dict[int, list[Signal]] = {}
        for i, bar in enumerate(bars):
            signals = strategy.on_bar(bar, ctx)
            signals_by_index[i] = signals
            for signal in signals:
                if signal.signal_type == SignalType.SELL:
                    provider.position = Position(
                        symbol="BTC/USDT", quantity=Decimal("-1"), average_entry_price=bar.close
                    )
                elif signal.signal_type == SignalType.CLOSE:
                    provider.position = None

        assert [s.signal_type for s in signals_by_index[7]] == [SignalType.SELL]
        assert [s.signal_type for s in signals_by_index[12]] == [SignalType.CLOSE]
        for i, signals in signals_by_index.items():
            if i not in (7, 12):
                assert signals == [], f"expected no signal at index {i}, got {signals}"

    def test_does_not_sell_while_already_short(self, make_bar) -> None:
        bars = _make_bars(make_bar, _CLOSES)
        provider = _TogglablePositionProvider()
        provider.position = Position(
            symbol="BTC/USDT", quantity=Decimal("-1"), average_entry_price=Decimal("100")
        )
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RuleStrategy(short_entry=_SHORT_ENTRY, short_exit=_SHORT_EXIT, lookback=20)
        strategy.on_start(ctx)

        all_signals = [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        assert all(s.signal_type != SignalType.SELL for s in all_signals)

    def test_long_and_short_sides_coexist_independently(self, make_bar) -> None:
        """A strategy configured with both sides only ever opens the side
        whose own entry fires while flat — the other side's entry is
        evaluated (tracked) but never fires a signal for it."""
        bars = _make_bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RuleStrategy(
            long_entry=_ENTRY,
            long_exit=_EXIT,
            short_entry=_SHORT_ENTRY,
            short_exit=_SHORT_EXIT,
            lookback=20,
        )
        strategy.on_start(ctx)

        signals_by_index: dict[int, list[Signal]] = {}
        for i, bar in enumerate(bars):
            signals_by_index[i] = strategy.on_bar(bar, ctx)

        # index5: golden cross while flat -> BUY (long_entry fires, not short).
        assert [s.signal_type for s in signals_by_index[5]] == [SignalType.BUY]

    def test_simultaneous_long_and_short_entry_edges_emit_neither(self, make_bar) -> None:
        bars = _make_bars(make_bar, ["10", "200"])
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RuleStrategy(
            long_entry=_TIE_LONG_ENTRY,
            long_exit=_NEVER_TRUE_EXIT,
            short_entry=_TIE_SHORT_ENTRY,
            short_exit=_NEVER_TRUE_EXIT,
            lookback=5,
        )
        strategy.on_start(ctx)

        all_signals = [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        assert all_signals == []


class TestStopTakeProfitConfig:
    def test_accepts_legacy_stop_and_take_profit_atr(self) -> None:
        RuleStrategy(entry=_ENTRY, exit=_EXIT, stop_atr=1.5, take_profit_atr=2.0)

    def test_accepts_long_short_risk_keys(self) -> None:
        RuleStrategy(
            long_entry=_ENTRY,
            long_exit=_EXIT,
            short_entry=_SHORT_ENTRY,
            short_exit=_SHORT_EXIT,
            long_stop_atr=1.5,
            long_take_profit_atr=2.0,
            short_stop_atr=1.5,
            short_take_profit_atr=2.0,
            stop_atr_period=14,
        )

    def test_rejects_non_positive_stop_atr(self) -> None:
        with pytest.raises(ValueError, match="stop_atr"):
            RuleStrategy(entry=_ENTRY, exit=_EXIT, stop_atr=0)

    def test_rejects_long_risk_keys_without_long_side(self) -> None:
        with pytest.raises(ValueError, match="long_stop_atr"):
            RuleStrategy(
                short_entry=_SHORT_ENTRY,
                short_exit=_SHORT_EXIT,
                long_stop_atr=1.5,
            )

    def test_rejects_mixing_legacy_stop_with_long_short_trees(self) -> None:
        with pytest.raises(ValueError, match="long_/short_ risk keys"):
            RuleStrategy(
                long_entry=_ENTRY,
                long_exit=_EXIT,
                stop_atr=1.5,
            )


class TestStopTakeProfitBehaviour:
    """ATR-multiple stop/TP fire CLOSE against bar high/low relative to
    `Position.average_entry_price`. Uses a never-true indicator exit so only
    risk levels can close the position."""

    @staticmethod
    def _range_bars(make_bar, n: int, *, high: str = "102", low: str = "98", close: str = "100"):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        return [
            make_bar(
                timestamp=start + timedelta(hours=i),
                open_=close,
                high=high,
                low=low,
                close=close,
            )
            for i in range(n)
        ]

    def test_long_stop_fires_close_when_low_breaches_level(self, make_bar) -> None:
        # Warmup with ~4 ATR over high-low=4 → ATR(period=5) ≈ 4.
        # Entry 100, stop_atr=1.5 → stop at 100 - 6 = 94.
        bars = self._range_bars(make_bar, 10, high="104", low="100", close="100")
        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RuleStrategy(
            entry=_ALWAYS_TRUE_ENTRY,
            exit=_NEVER_TRUE_EXIT,
            stop_atr=1.5,
            stop_atr_period=5,
            lookback=30,
        )
        strategy.on_start(ctx)
        for bar in bars:
            strategy.on_bar(bar, ctx)

        provider.position = Position(
            symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
        )
        stop_bar = make_bar(
            timestamp=bars[-1].timestamp + timedelta(hours=1),
            open_="100",
            high="100",
            low="93",  # below 94 stop
            close="95",
        )
        signals = strategy.on_bar(stop_bar, ctx)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.CLOSE
        assert signals[0].metadata["exit_reason"] == "stop_loss"

    def test_long_take_profit_fires_when_high_breaches_level(self, make_bar) -> None:
        bars = self._range_bars(make_bar, 10, high="104", low="100", close="100")
        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RuleStrategy(
            entry=_ALWAYS_TRUE_ENTRY,
            exit=_NEVER_TRUE_EXIT,
            take_profit_atr=1.5,
            stop_atr_period=5,
            lookback=30,
        )
        strategy.on_start(ctx)
        for bar in bars:
            strategy.on_bar(bar, ctx)

        provider.position = Position(
            symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
        )
        tp_bar = make_bar(
            timestamp=bars[-1].timestamp + timedelta(hours=1),
            open_="100",
            high="107",  # above 100 + 6 = 106
            low="100",
            close="105",
        )
        signals = strategy.on_bar(tp_bar, ctx)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.CLOSE
        assert signals[0].metadata["exit_reason"] == "take_profit"

    def test_stop_beats_take_profit_on_same_bar(self, make_bar) -> None:
        bars = self._range_bars(make_bar, 10, high="104", low="100", close="100")
        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RuleStrategy(
            entry=_ALWAYS_TRUE_ENTRY,
            exit=_NEVER_TRUE_EXIT,
            stop_atr=1.5,
            take_profit_atr=1.5,
            stop_atr_period=5,
            lookback=30,
        )
        strategy.on_start(ctx)
        for bar in bars:
            strategy.on_bar(bar, ctx)

        provider.position = Position(
            symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
        )
        both_bar = make_bar(
            timestamp=bars[-1].timestamp + timedelta(hours=1),
            open_="100",
            high="107",  # TP
            low="93",  # stop
            close="100",
        )
        signals = strategy.on_bar(both_bar, ctx)

        assert [s.metadata.get("exit_reason") for s in signals] == ["stop_loss"]

    def test_short_stop_fires_when_high_breaches_level(self, make_bar) -> None:
        bars = self._range_bars(make_bar, 10, high="104", low="100", close="100")
        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RuleStrategy(
            short_entry=_ALWAYS_TRUE_ENTRY,
            short_exit=_NEVER_TRUE_EXIT,
            short_stop_atr=1.5,
            stop_atr_period=5,
            lookback=30,
        )
        strategy.on_start(ctx)
        for bar in bars:
            strategy.on_bar(bar, ctx)

        provider.position = Position(
            symbol="BTC/USDT", quantity=Decimal("-1"), average_entry_price=Decimal("100")
        )
        stop_bar = make_bar(
            timestamp=bars[-1].timestamp + timedelta(hours=1),
            open_="100",
            high="107",  # above 100 + 6
            low="100",
            close="105",
        )
        signals = strategy.on_bar(stop_bar, ctx)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.CLOSE
        assert signals[0].metadata["exit_reason"] == "stop_loss"

    def test_stop_beats_indicator_exit_on_same_bar(self, make_bar) -> None:
        bars = self._range_bars(make_bar, 10, high="104", low="100", close="100")
        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        # Indicator exit that is always true once in position — stop must still win.
        always_exit = {"compare": {"indicator": "sma", "period": 1, "op": ">", "value": 0}}
        strategy = RuleStrategy(
            entry=_ALWAYS_TRUE_ENTRY,
            exit=always_exit,
            stop_atr=1.5,
            stop_atr_period=5,
            lookback=30,
        )
        strategy.on_start(ctx)
        for bar in bars:
            strategy.on_bar(bar, ctx)

        provider.position = Position(
            symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
        )
        stop_bar = make_bar(
            timestamp=bars[-1].timestamp + timedelta(hours=1),
            open_="100",
            high="100",
            low="93",
            close="95",
        )
        signals = strategy.on_bar(stop_bar, ctx)

        assert signals[0].metadata["exit_reason"] == "stop_loss"

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
