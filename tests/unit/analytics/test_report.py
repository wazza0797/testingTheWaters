from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from typer.testing import CliRunner

from trading_platform.analytics.report import build_performance_report
from trading_platform.analytics.significance import SignificanceFlag
from trading_platform.backtesting.result import BacktestResult, EquityPoint
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import OrderSide
from trading_platform.main import app

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)
_HELP_ENV = {"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}
runner = CliRunner()


def _fill(side: OrderSide, qty: str, price: str, day: int, fee: str = "0") -> Fill:
    return Fill(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=side,
        filled_qty=Decimal(qty),
        remaining_qty=Decimal("0"),
        fill_price=Decimal(price),
        fee=Decimal(fee),
        fee_type=FeeType.TAKER,
        is_complete=True,
        timestamp=UTC_TS + timedelta(days=day),
    )


class TestPerformanceReportPipeline:
    def test_synthetic_backtest_result_to_report(self) -> None:
        fills = (
            _fill(OrderSide.BUY, "1", "100", 0, fee="1"),
            _fill(OrderSide.SELL, "1", "110", 1, fee="1"),
        )
        equity = tuple(
            EquityPoint(UTC_TS + timedelta(days=i), Decimal(str(100 + i))) for i in range(5)
        )
        result = BacktestResult(
            symbol="BTC/USDT",
            timeframe="1d",
            starting_cash=Decimal("10000"),
            ending_cash=Decimal("10008"),
            bars_processed=5,
            fills=fills,
            total_fees_paid=Decimal("2"),
            equity_curve=equity,
            final_position=None,
        )

        report = build_performance_report(result, bars=())

        assert report.metrics.round_trip_count == 1
        assert report.metrics.ending_equity == Decimal("104")
        assert any(f.flag == SignificanceFlag.LOW_SAMPLE_SIZE for f in report.flags)
        assert any(f.flag == SignificanceFlag.LOW_BAR_COUNT for f in report.flags)

        payload = report.to_dict()
        json.dumps(payload)


class TestBacktestCliReportFlag:
    def test_help_lists_report_flag(self) -> None:
        result = runner.invoke(app, ["backtest", "--help"], env=_HELP_ENV)
        assert result.exit_code == 0
        assert "--report" in result.output

    def test_rejects_unknown_report_format(self) -> None:
        result = runner.invoke(app, ["backtest", "--report", "csv"])
        assert result.exit_code == 1
        assert "Unsupported --report" in result.output
