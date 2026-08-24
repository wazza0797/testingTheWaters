from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.domain.models.position import Position
from trading_platform.strategies.context import DefaultStrategyContext, NullPositionProvider


class TestNullPositionProvider:
    def test_always_reports_flat(self) -> None:
        provider = NullPositionProvider()

        assert provider.position_for("BTC/USDT") is None
        assert provider.position_for("ETH/USDT") is None


class TestDefaultStrategyContext:
    def test_indicator_returns_latest_sma_value(self, make_bar) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        closes = ["10", "20", "30"]
        bars = [
            make_bar(
                timestamp=start + timedelta(hours=i),
                open_=c,
                high=c,
                low=c,
                close=c,
            )
            for i, c in enumerate(closes)
        ]
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        result = ctx.indicator("sma", bars, period=3)

        assert result == 20.0

    def test_indicator_returns_nan_when_insufficient_history(self, make_bar) -> None:
        bars = [make_bar()]
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        result = ctx.indicator("sma", bars, period=5)

        assert math.isnan(result)

    def test_indicator_returns_nan_for_empty_bars(self) -> None:
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        assert math.isnan(ctx.indicator("sma", [], period=3))

    def test_indicator_supports_ema_and_rsi_by_name(self, make_bar) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = [
            make_bar(timestamp=start + timedelta(hours=i), open_=c, high=c, low=c, close=c)
            for i, c in enumerate(["10", "20", "30", "25", "35"])
        ]
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        ema = ctx.indicator("ema", bars, period=3)
        rsi = ctx.indicator("rsi", bars, period=3)

        assert isinstance(ema, float) and not math.isnan(ema)
        assert isinstance(rsi, float) and not math.isnan(rsi)

    def test_position_for_delegates_to_position_provider(self) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
        )

        class StubProvider:
            def position_for(self, symbol: str) -> Position | None:
                return position if symbol == "BTC/USDT" else None

        ctx = DefaultStrategyContext(
            symbol="BTC/USDT", timeframe="1h", position_provider=StubProvider()
        )

        assert ctx.position_for("BTC/USDT") is position
        assert ctx.position_for("ETH/USDT") is None

    def test_position_for_defaults_to_flat(self) -> None:
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        assert ctx.position_for("BTC/USDT") is None

    def test_params_default_to_empty_mapping(self) -> None:
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")

        assert dict(ctx.params) == {}

    def test_params_are_stored_and_readable(self) -> None:
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h", params={"fast_period": 10})

        assert ctx.params["fast_period"] == 10
