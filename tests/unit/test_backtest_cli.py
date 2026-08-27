from __future__ import annotations

from typer.testing import CliRunner

from trading_platform.main import app

runner = CliRunner()


class TestBacktestCliOptions:
    def test_help_lists_symbol_timeframe_and_date_flags(self) -> None:
        result = runner.invoke(app, ["backtest", "--help"])

        assert result.exit_code == 0
        assert "--symbol" in result.output
        assert "--timeframe" in result.output
        assert "--start" in result.output
        assert "--end" in result.output

    def test_rejects_malformed_timeframe_before_loading_data(self) -> None:
        result = runner.invoke(app, ["backtest", "--timeframe", "1x"])

        assert result.exit_code == 1
        assert "Backtest failed" in result.output or "Unsupported timeframe" in result.output
