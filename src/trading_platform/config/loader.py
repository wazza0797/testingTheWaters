from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from trading_platform.domain.errors import ConfigurationError

DEFAULT_CONFIG_DIR = Path("config")


class TradingConfig(BaseModel):
    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"


class StrategyConfig(BaseModel):
    """Config-driven strategy selection for `StrategyLoader`.

    `path` is a `"module:ClassName"` string (see
    `strategies/loader.py::load_strategy_class`); `None` until a milestone
    actually wires a strategy into a running loop (M4 backtest engine).
    """

    path: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class WalkForwardConfig(BaseModel):
    """Rolling IS/OOS windows with grid search (Milestone 4.5 Phase C).

    Used by `trading-platform walk-forward`. Each fold optimizes `param_grid`
    on an in-sample window of `is_bars`, then evaluates the winning params on
    the following `oos_bars`. The window advances by `step_bars`.

    `objective` scores IS candidates (higher is better):
    - `total_return_pct` — from `BacktestResult`
    - `sharpe_daily` — from M5 `compute_metrics` (None treated as worst)
    """

    is_bars: int = 8760
    oos_bars: int = 2190
    step_bars: int = 2190
    param_grid: dict[str, list[Any]] = Field(default_factory=dict)
    objective: Literal["total_return_pct", "sharpe_daily"] = "sharpe_daily"

    @model_validator(mode="after")
    def _validate_window_sizes(self) -> WalkForwardConfig:
        for name, value in (
            ("is_bars", self.is_bars),
            ("oos_bars", self.oos_bars),
            ("step_bars", self.step_bars),
        ):
            if value < 1:
                raise ValueError(f"walk_forward.{name} must be >= 1, got {value}")
        return self


class ValidationConfig(BaseModel):
    """Hold-out train/test split for backtesting (Milestone 4.5 Phase A).

    When `enabled`, `trading-platform backtest` runs the strategy twice —
    in-sample (`timestamp < train_end`) and out-of-sample
    (`timestamp >= test_start`, optionally `< test_end`) — and prints both
    summaries. OOS is the only result that counts for validation.

    Dates are ISO-8601; naive values are treated as UTC (same as CLI
    `--start`/`--end`). A gap between `train_end` and `test_start` is an
    allowed embargo period for indicator warmup.

    `walk_forward` configures the separate `walk-forward` CLI (Phase C);
    it is independent of `enabled` / hold-out dates.
    """

    enabled: bool = False
    train_end: datetime | None = None
    test_start: datetime | None = None
    test_end: datetime | None = None
    walk_forward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)

    @model_validator(mode="after")
    def _require_dates_when_enabled(self) -> ValidationConfig:
        if not self.enabled:
            return self
        if self.train_end is None or self.test_start is None:
            raise ValueError(
                "validation.train_end and validation.test_start are required "
                "when validation.enabled is true"
            )
        return self


class BacktestConfig(BaseModel):
    """Simulation parameters for the backtest engine (Milestone 4).

    `starting_cash` and `position_size_pct` size the pass-through risk
    engine's orders (see `risk/sizing.py::EquityFractionSizer`) — there is no
    real position-sizing module yet. `starting_cash` is `Decimal` (not
    `float`) so a YAML value like `"10000"` round-trips exactly; write it
    quoted in yaml to avoid `pyyaml` parsing it as a float first.

    `cash_safety_buffer_pct` pads `PassThroughRiskEngine`'s cash-sufficiency
    check (on top of the instrument's known taker fee rate) to cover the
    spread/slippage a real fill may incur versus the signal-bar close it was
    sized against — see `PassThroughRiskEngine._affordable_quantity`.

    `spread_volatility_k` (default `0` = off) adds ATR-scaled width on top of
    `spread_bps` so fills are more expensive in volatile regimes — see
    `backtesting/models/spread_model.py` and Milestone 4.5 Phase B.
    """

    starting_cash: Decimal = Decimal("10000")
    position_size_pct: float = 1.0
    cash_safety_buffer_pct: float = 0.001
    spread_bps: float = 5.0
    spread_volatility_k: float = 0.0
    spread_atr_period: int = 14
    latency_bars: int = 1
    volume_participation_rate: float = 0.10
    assume_maker_on_limit: bool = True
    use_next_bar_open: bool = True


class ObservabilityConfig(BaseModel):
    enabled: bool = True
    metrics_port: int = 9090
    health_port: int = 8080
    system_poll_interval_sec: float = 15.0
    log_summary_interval_sec: float = 60.0
    log_summary_enabled: bool = True


class AnalyticsConfig(BaseModel):
    """Performance-report thresholds and bootstrap settings (Milestone 5).

    Used by `build_performance_report` / CLI output and by `AnalyticsHandler`
    significance defaults. Bootstrap is pure-Python (`random`) — no scipy.
    """

    min_round_trips: int = 30
    min_bars: int = 500
    min_daily_returns_for_sharpe: int = 30
    bootstrap_iterations: int = 1000
    bootstrap_seed: int = 42
    market_sma_period: int = 200


class AppConfig(BaseModel):
    """Typed, validated view over `config/*.yaml`. Never holds secrets — those
    live in `Settings` (env vars) and are merged in by the composition root.
    """

    trading: TradingConfig = Field(default_factory=TradingConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Expected a mapping at the top level of {path}, got {type(data).__name__}"
        )
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_dir: Path | None = None, overlay: str | None = None) -> AppConfig:
    """Load `default.yaml` + `observability.yaml`, optionally deep-merged with a
    named overlay (e.g. `overlay="backtest"` loads `backtest.yaml` on top).

    Missing files are treated as empty (all field defaults apply), so a fresh
    checkout with no yaml edits still runs.
    """
    directory = config_dir or DEFAULT_CONFIG_DIR
    merged = _read_yaml(directory / "default.yaml")
    merged = _deep_merge(merged, _read_yaml(directory / "observability.yaml"))
    if overlay:
        merged = _deep_merge(merged, _read_yaml(directory / f"{overlay}.yaml"))
    return AppConfig.model_validate(merged)
