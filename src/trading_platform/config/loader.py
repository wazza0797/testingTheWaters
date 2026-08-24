from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

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


class BacktestConfig(BaseModel):
    """Simulation parameters for the backtest engine (Milestone 4).

    `starting_cash` and `position_size_pct` size the pass-through risk
    engine's orders (see `risk/sizing.py::EquityFractionSizer`) — there is no
    real position-sizing module yet. `starting_cash` is `Decimal` (not
    `float`) so a YAML value like `"10000"` round-trips exactly; write it
    quoted in yaml to avoid `pyyaml` parsing it as a float first.
    """

    starting_cash: Decimal = Decimal("10000")
    position_size_pct: float = 1.0
    spread_bps: float = 5.0
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


class AppConfig(BaseModel):
    """Typed, validated view over `config/*.yaml`. Never holds secrets — those
    live in `Settings` (env vars) and are merged in by the composition root.
    """

    trading: TradingConfig = Field(default_factory=TradingConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)


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
