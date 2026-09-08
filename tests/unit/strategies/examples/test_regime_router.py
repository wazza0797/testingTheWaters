from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.unit.strategies.conformance import assert_strategy_conforms
from trading_platform.domain.models.position import Position
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.strategies.context import DefaultStrategyContext
from trading_platform.strategies.examples.regime_router import RegimeRouterStrategy
from trading_platform.strategies.rules import ConditionError


class _TogglablePositionProvider:
    def __init__(self) -> None:
        self.position: Position | None = None

    def position_for(self, symbol: str) -> Position | None:
        return self.position


def _bars(make_bar, closes: list[str], start=None) -> list:
    start = start or datetime(2024, 1, 1, tzinfo=UTC)
    return [
        make_bar(timestamp=start + timedelta(hours=i), open_=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]


# Regime that is "always on" via a trivially-true condition (close > 0),
# entering as soon as fast SMA crosses above slow SMA.
_ALWAYS_ON_TREND_REGIME = {
    "name": "trend",
    "when": {"compare": {"indicator": "sma", "period": 1, "op": ">", "value": 0.0}},
    "playbook": {
        "entry": {
            "cross": {
                "left": {"indicator": "sma", "period": 2},
                "right": {"indicator": "sma", "period": 3},
                "direction": "above",
            }
        },
        "exit": {
            "cross": {
                "left": {"indicator": "sma", "period": 2},
                "right": {"indicator": "sma", "period": 3},
                "direction": "below",
            }
        },
    },
}

_CLOSES = ["100", "100", "100", "100", "100", "200", "50", "50", "50", "50", "50"]


class TestConstruction:
    def test_requires_non_empty_regimes(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            RegimeRouterStrategy(regimes=[])

    def test_requires_unique_regime_names(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            RegimeRouterStrategy(regimes=[_ALWAYS_ON_TREND_REGIME, _ALWAYS_ON_TREND_REGIME])

    def test_rejects_regime_missing_required_keys(self) -> None:
        with pytest.raises(ValueError, match="missing required key"):
            RegimeRouterStrategy(regimes=[{"name": "x", "when": {}}])

    def test_rejects_playbook_missing_entry_or_exit(self) -> None:
        bad = {"name": "x", "when": _ALWAYS_ON_TREND_REGIME["when"], "playbook": {"entry": {}}}
        with pytest.raises(ValueError, match="playbook"):
            RegimeRouterStrategy(regimes=[bad])

    def test_rejects_unknown_indicator_in_when(self) -> None:
        bad = {
            "name": "x",
            "when": {"compare": {"indicator": "not_real", "op": ">", "value": 1}},
            "playbook": _ALWAYS_ON_TREND_REGIME["playbook"],
        }
        with pytest.raises(ConditionError, match="not_real"):
            RegimeRouterStrategy(regimes=[bad])

    def test_rejects_default_other_than_flat(self) -> None:
        with pytest.raises(ValueError, match="flat"):
            RegimeRouterStrategy(regimes=[_ALWAYS_ON_TREND_REGIME], default="hold")

    def test_rejects_min_regime_bars_less_than_one(self) -> None:
        with pytest.raises(ValueError, match="min_regime_bars"):
            RegimeRouterStrategy(regimes=[_ALWAYS_ON_TREND_REGIME], min_regime_bars=0)


class TestSingleRegimeBehavesLikeRuleStrategy:
    def test_emits_buy_and_close_on_transition_bars(self, make_bar) -> None:
        bars = _bars(make_bar, _CLOSES)
        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RegimeRouterStrategy(regimes=[_ALWAYS_ON_TREND_REGIME], lookback=20)
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

    def test_buy_metadata_includes_regime_name(self, make_bar) -> None:
        bars = _bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RegimeRouterStrategy(regimes=[_ALWAYS_ON_TREND_REGIME], lookback=20)
        strategy.on_start(ctx)

        buy_signal = None
        for bar in bars:
            for signal in strategy.on_bar(bar, ctx):
                if signal.signal_type == SignalType.BUY:
                    buy_signal = signal

        assert buy_signal is not None
        assert buy_signal.metadata["regime"] == "trend"


class TestRegimeSelectionAndPriority:
    def test_first_matching_regime_wins(self, make_bar) -> None:
        always_true = {"compare": {"indicator": "sma", "period": 1, "op": ">", "value": 0.0}}
        never_true = {"compare": {"indicator": "sma", "period": 1, "op": "<", "value": 0.0}}
        first = {
            "name": "first",
            "when": always_true,
            "playbook": {"entry": never_true, "exit": never_true},
        }
        second = {
            "name": "second",
            "when": always_true,
            "playbook": {"entry": never_true, "exit": never_true},
        }
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RegimeRouterStrategy(regimes=[first, second], lookback=10)
        strategy.on_start(ctx)

        bars = _bars(make_bar, ["100"] * 5)
        for bar in bars:
            strategy.on_bar(bar, ctx)

        assert strategy._active_index == 0  # "first" wins over "second"

    def test_no_matching_regime_leaves_router_in_default_flat_state(self, make_bar) -> None:
        never_true = {"compare": {"indicator": "sma", "period": 1, "op": "<", "value": 0.0}}
        regime = {
            "name": "never",
            "when": never_true,
            "playbook": {"entry": never_true, "exit": never_true},
        }
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RegimeRouterStrategy(regimes=[regime], lookback=10)
        strategy.on_start(ctx)

        bars = _bars(make_bar, ["100"] * 5)
        signals = [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        assert signals == []
        assert strategy._active_index is None


def _above_100(period: int = 1) -> dict:
    return {"compare": {"indicator": "sma", "period": period, "op": ">=", "value": 100.0}}


def _below_100(period: int = 1) -> dict:
    return {"compare": {"indicator": "sma", "period": period, "op": "<", "value": 100.0}}


_NEVER = {"compare": {"indicator": "sma", "period": 1, "op": "<", "value": -1.0}}


class TestHysteresis:
    def test_blocks_switching_until_min_regime_bars_have_elapsed(self, make_bar) -> None:
        # regime_a matches while close >= 100, regime_b while close < 100.
        # Closes: 100, 100, 50, 50, 50 — the `when` flips to "b" at index 2,
        # but min_regime_bars=3 should keep "a" active through index 2 and
        # only allow the switch at index 3 (3 bars after activating at 0).
        regime_a = {
            "name": "a",
            "when": _above_100(),
            "playbook": {"entry": _NEVER, "exit": _NEVER},
        }
        regime_b = {
            "name": "b",
            "when": _below_100(),
            "playbook": {"entry": _NEVER, "exit": _NEVER},
        }
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RegimeRouterStrategy(
            regimes=[regime_a, regime_b], lookback=10, min_regime_bars=3
        )
        strategy.on_start(ctx)

        bars = _bars(make_bar, ["100", "100", "50", "50", "50"])
        active_history = []
        for bar in bars:
            strategy.on_bar(bar, ctx)
            active_history.append(strategy._active_index)

        assert active_history == [0, 0, 0, 1, 1]

    def test_min_regime_bars_of_one_allows_switching_every_bar(self, make_bar) -> None:
        regime_a = {
            "name": "a",
            "when": _above_100(),
            "playbook": {"entry": _NEVER, "exit": _NEVER},
        }
        regime_b = {
            "name": "b",
            "when": _below_100(),
            "playbook": {"entry": _NEVER, "exit": _NEVER},
        }
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RegimeRouterStrategy(
            regimes=[regime_a, regime_b], lookback=10, min_regime_bars=1
        )
        strategy.on_start(ctx)

        bars = _bars(make_bar, ["100", "50", "100", "50"])
        active_history = []
        for bar in bars:
            strategy.on_bar(bar, ctx)
            active_history.append(strategy._active_index)

        assert active_history == [0, 1, 0, 1]


class TestRegimeSwitchFlattensOpenPosition:
    def test_switching_to_a_different_regime_emits_a_flatten_close(self, make_bar) -> None:
        regime_a = {
            "name": "a",
            "when": _above_100(),
            "playbook": {"entry": _NEVER, "exit": _NEVER},
        }
        regime_b = {
            "name": "b",
            "when": _below_100(),
            "playbook": {"entry": _NEVER, "exit": _NEVER},
        }

        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RegimeRouterStrategy(
            regimes=[regime_a, regime_b], lookback=10, min_regime_bars=1
        )
        strategy.on_start(ctx)

        bars = _bars(make_bar, ["100", "50"])
        strategy.on_bar(bars[0], ctx)  # activates regime "a", still flat
        assert strategy._active_index == 0

        provider.position = Position(
            symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
        )
        signals = strategy.on_bar(bars[1], ctx)  # `when` flips to regime "b"

        assert strategy._active_index == 1
        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.CLOSE
        assert signals[0].metadata == {"reason": "regime_switch", "new_regime": "b"}

    def test_switching_to_the_default_flat_state_also_flattens(self, make_bar) -> None:
        regime = {"name": "a", "when": _above_100(), "playbook": {"entry": _NEVER, "exit": _NEVER}}

        provider = _TogglablePositionProvider()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", position_provider=provider)
        strategy = RegimeRouterStrategy(regimes=[regime], lookback=10, min_regime_bars=1)
        strategy.on_start(ctx)

        bars = _bars(make_bar, ["100", "50"])
        strategy.on_bar(bars[0], ctx)
        assert strategy._active_index == 0

        provider.position = Position(
            symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
        )
        signals = strategy.on_bar(bars[1], ctx)  # no regime matches -> default flat

        assert strategy._active_index is None
        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.CLOSE
        assert signals[0].metadata == {"reason": "regime_switch", "new_regime": "flat"}

    def test_no_flatten_signal_when_already_flat(self, make_bar) -> None:
        regime_a = {
            "name": "a",
            "when": _above_100(),
            "playbook": {"entry": _NEVER, "exit": _NEVER},
        }
        regime_b = {
            "name": "b",
            "when": _below_100(),
            "playbook": {"entry": _NEVER, "exit": _NEVER},
        }

        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")  # NullPositionProvider
        strategy = RegimeRouterStrategy(
            regimes=[regime_a, regime_b], lookback=10, min_regime_bars=1
        )
        strategy.on_start(ctx)

        bars = _bars(make_bar, ["100", "50"])
        strategy.on_bar(bars[0], ctx)
        signals = strategy.on_bar(bars[1], ctx)

        assert signals == []


class TestLifecycle:
    def test_on_start_resets_all_state(self, make_bar) -> None:
        bars = _bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RegimeRouterStrategy(regimes=[_ALWAYS_ON_TREND_REGIME], lookback=20)
        strategy.on_start(ctx)
        for bar in bars[:6]:
            strategy.on_bar(bar, ctx)

        strategy.on_start(ctx)

        assert strategy._active_index is None
        first_bar_signals = strategy.on_bar(bars[0], ctx)
        assert first_bar_signals == []

    def test_on_stop_does_not_raise(self) -> None:
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = RegimeRouterStrategy(regimes=[_ALWAYS_ON_TREND_REGIME])
        strategy.on_stop(ctx)


class TestDeterminism:
    def test_running_the_same_bars_twice_produces_identical_signals(self, make_bar) -> None:
        bars = _bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        def _run() -> list[Signal]:
            strategy = RegimeRouterStrategy(regimes=[_ALWAYS_ON_TREND_REGIME], lookback=20)
            strategy.on_start(ctx)
            return [s for bar in bars for s in strategy.on_bar(bar, ctx)]

        a, b = _run(), _run()
        assert [(s.signal_type, s.timestamp) for s in a] == [
            (s.signal_type, s.timestamp) for s in b
        ]


class TestGenericConformance:
    def test_conforms_to_the_generic_istrategy_behavioral_contract(self, make_bar) -> None:
        bars = _bars(make_bar, _CLOSES)
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        assert_strategy_conforms(
            lambda: RegimeRouterStrategy(regimes=[_ALWAYS_ON_TREND_REGIME], lookback=20), ctx, bars
        )
