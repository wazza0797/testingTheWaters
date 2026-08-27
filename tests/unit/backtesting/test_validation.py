from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_platform.backtesting.validation import HoldOutValidator, slice_bars
from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.container import build_backtest_engine, build_container
from trading_platform.domain.errors import ConfigurationError, MarketDataError
from trading_platform.domain.models.instrument_rules import InstrumentRules

_START = datetime(2024, 1, 1, tzinfo=UTC)


def _hourly_bars(make_bar, count: int, start: datetime | None = None):
    start = start or _START
    return [
        make_bar(
            timestamp=start + timedelta(hours=i),
            open_="100",
            high="100",
            low="100",
            close="100",
            volume="1000",
        )
        for i in range(count)
    ]


class TestSliceBars:
    def test_inclusive_start_exclusive_end(self, make_bar) -> None:
        bars = _hourly_bars(make_bar, 5)
        sliced = slice_bars(
            bars,
            start=_START + timedelta(hours=1),
            end=_START + timedelta(hours=4),
        )

        assert [b.timestamp for b in sliced] == [
            _START + timedelta(hours=1),
            _START + timedelta(hours=2),
            _START + timedelta(hours=3),
        ]

    def test_none_bounds_returns_all(self, make_bar) -> None:
        bars = _hourly_bars(make_bar, 3)
        assert slice_bars(bars) == bars

    def test_normalizes_naive_datetimes_to_utc(self, make_bar) -> None:
        bars = _hourly_bars(make_bar, 3)
        sliced = slice_bars(bars, end=datetime(2024, 1, 1, 2))  # naive

        assert len(sliced) == 2
        assert sliced[-1].timestamp == _START + timedelta(hours=1)


class TestHoldOutValidator:
    def test_runs_is_and_oos_on_distinct_windows(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        settings = Settings(_env_file=None)
        config = load_config(config_dir=Path("config"), overlay="backtest")
        container = build_container(settings, config)

        bars = _hourly_bars(make_bar, 80)
        train_end = _START + timedelta(hours=40)
        test_start = _START + timedelta(hours=40)

        validator = HoldOutValidator(
            lambda: build_backtest_engine(container, btc_usdt_instrument_rules)
        )
        result = validator.run(bars, "1h", train_end=train_end, test_start=test_start)

        assert result.is_result.bars_processed == 40
        assert result.oos_result.bars_processed == 40

    def test_embargo_gap_between_train_end_and_test_start(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        settings = Settings(_env_file=None)
        config = load_config(config_dir=Path("config"), overlay="backtest")
        container = build_container(settings, config)

        bars = _hourly_bars(make_bar, 100)
        # 30-bar embargo between IS and OOS
        train_end = _START + timedelta(hours=40)
        test_start = _START + timedelta(hours=70)

        validator = HoldOutValidator(
            lambda: build_backtest_engine(container, btc_usdt_instrument_rules)
        )
        result = validator.run(bars, "1h", train_end=train_end, test_start=test_start)

        assert result.is_result.bars_processed == 40
        assert result.oos_result.bars_processed == 30  # hours 70..99

    def test_rejects_overlapping_windows(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        settings = Settings(_env_file=None)
        config = load_config(config_dir=Path("config"), overlay="backtest")
        container = build_container(settings, config)
        bars = _hourly_bars(make_bar, 50)

        validator = HoldOutValidator(
            lambda: build_backtest_engine(container, btc_usdt_instrument_rules)
        )
        with pytest.raises(ConfigurationError, match="overlapping"):
            validator.run(
                bars,
                "1h",
                train_end=_START + timedelta(hours=40),
                test_start=_START + timedelta(hours=30),
            )

    def test_empty_is_window_raises(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        settings = Settings(_env_file=None)
        config = load_config(config_dir=Path("config"), overlay="backtest")
        container = build_container(settings, config)
        bars = _hourly_bars(make_bar, 10)

        validator = HoldOutValidator(
            lambda: build_backtest_engine(container, btc_usdt_instrument_rules)
        )
        with pytest.raises(MarketDataError, match="in-sample"):
            validator.run(
                bars,
                "1h",
                train_end=datetime(2023, 1, 1, tzinfo=UTC),
                test_start=_START,
            )

    def test_empty_oos_window_raises(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        settings = Settings(_env_file=None)
        config = load_config(config_dir=Path("config"), overlay="backtest")
        container = build_container(settings, config)
        bars = _hourly_bars(make_bar, 10)

        validator = HoldOutValidator(
            lambda: build_backtest_engine(container, btc_usdt_instrument_rules)
        )
        with pytest.raises(MarketDataError, match="out-of-sample"):
            validator.run(
                bars,
                "1h",
                train_end=_START + timedelta(hours=5),
                test_start=datetime(2025, 1, 1, tzinfo=UTC),
            )
