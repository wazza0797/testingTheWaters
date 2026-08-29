from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.analytics.regime import (
    MarketRegime,
    calendar_quarter_label,
    calendar_splits,
    market_regime_labels,
)
from trading_platform.analytics.trades import RoundTrip
from trading_platform.backtesting.result import EquityPoint
from trading_platform.domain.models.bar import Bar

UTC_TS = datetime(2024, 1, 15, tzinfo=UTC)


def _bar(i: int, close: float) -> Bar:
    return Bar(
        symbol="BTC/USDT",
        timeframe="1h",
        timestamp=UTC_TS + timedelta(hours=i),
        open=Decimal(str(close)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 1)),
        close=Decimal(str(close)),
        volume=Decimal("10"),
    )


class TestCalendar:
    def test_quarter_label(self) -> None:
        assert calendar_quarter_label(datetime(2024, 2, 1, tzinfo=UTC)) == "2024-Q1"
        assert calendar_quarter_label(datetime(2024, 11, 1, tzinfo=UTC)) == "2024-Q4"

    def test_calendar_splits_assign_quarters(self) -> None:
        equity = (
            EquityPoint(datetime(2024, 1, 10, tzinfo=UTC), Decimal("100")),
            EquityPoint(datetime(2024, 1, 20, tzinfo=UTC), Decimal("110")),
            EquityPoint(datetime(2024, 4, 10, tzinfo=UTC), Decimal("110")),
            EquityPoint(datetime(2024, 4, 20, tzinfo=UTC), Decimal("100")),
        )
        trips = (
            RoundTrip(
                symbol="BTC/USDT",
                quantity=Decimal("1"),
                entry_price=Decimal("100"),
                exit_price=Decimal("110"),
                entry_time=datetime(2024, 1, 5, tzinfo=UTC),
                exit_time=datetime(2024, 1, 15, tzinfo=UTC),
                pnl=Decimal("10"),
                fees=Decimal("0"),
                is_partial=False,
            ),
        )
        bars = (
            Bar(
                symbol="BTC/USDT",
                timeframe="1d",
                timestamp=datetime(2024, 1, 10, tzinfo=UTC),
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1"),
            ),
            Bar(
                symbol="BTC/USDT",
                timeframe="1d",
                timestamp=datetime(2024, 1, 20, tzinfo=UTC),
                open=Decimal("110"),
                high=Decimal("110"),
                low=Decimal("110"),
                close=Decimal("110"),
                volume=Decimal("1"),
            ),
        )

        rows = calendar_splits(equity, trips, bars, by="quarter")
        labels = {r.label for r in rows}
        assert "2024-Q1" in labels
        assert "2024-Q2" in labels
        q1 = next(r for r in rows if r.label == "2024-Q1")
        assert q1.round_trip_count == 1
        assert q1.return_pct == Decimal("10")


class TestMarketRegime:
    def test_warmup_is_unknown(self) -> None:
        bars = tuple(_bar(i, 100.0) for i in range(5))
        labels = market_regime_labels(bars, sma_period=200)
        assert all(lab == MarketRegime.UNKNOWN for lab in labels)

    def test_bull_when_price_above_rising_sma(self) -> None:
        # Steadily rising prices → after warmup, bull
        bars = tuple(_bar(i, 100.0 + i) for i in range(220))
        labels = market_regime_labels(bars, sma_period=200)
        assert labels[50] == MarketRegime.UNKNOWN
        assert labels[-1] == MarketRegime.BULL

    def test_market_regime_return_attributes_only_in_regime_steps(self) -> None:
        """Non-contiguous first→last would embed out-of-regime PnL; attribution must not.

        Equity steps: 100→110 (bull +10%), 110→200 (bear, ignored), 200→220 (bull +10%).
        Attributed bull return = 1.1×1.1−1 = 21%, not naive (220−100)/100 = 120%.
        """
        from trading_platform.analytics.regime import _regime_attributed_equity_path

        base = datetime(2024, 6, 1, tzinfo=UTC)
        ts = [base + timedelta(hours=i) for i in range(4)]
        equity = (
            EquityPoint(ts[0], Decimal("100")),
            EquityPoint(ts[1], Decimal("110")),
            EquityPoint(ts[2], Decimal("200")),
            EquityPoint(ts[3], Decimal("220")),
        )
        ts_to_regime = {
            ts[0]: MarketRegime.BULL,
            ts[1]: MarketRegime.BULL,
            ts[2]: MarketRegime.BEAR,
            ts[3]: MarketRegime.BULL,
        }
        path = _regime_attributed_equity_path(equity, ts_to_regime, "bull")
        assert len(path) >= 2
        return_pct = (path[-1].equity - path[0].equity) / path[0].equity * Decimal("100")
        assert return_pct == Decimal("21")
