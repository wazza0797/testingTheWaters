from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, cast

from trading_platform.domain.errors import StrategyError
from trading_platform.domain.ports.strategy import IStrategy


def load_strategy_class(path: str) -> type[IStrategy]:
    """Resolve a strategy class from a `"module.submodule:ClassName"` path,
    e.g. `"trading_platform.strategies.examples.sma_crossover:SmaCrossoverStrategy"`.

    Adding a new built-in or third-party strategy requires only a new file
    implementing `IStrategy` plus this config string — no changes to this
    loader, `strategies/handler.py`, or any other core module.
    """
    if ":" not in path:
        raise StrategyError(f"Strategy path must be 'module:ClassName', got {path!r} (missing ':')")
    module_name, _, class_name = path.partition(":")
    if not module_name or not class_name:
        raise StrategyError(f"Strategy path must be 'module:ClassName', got {path!r}")

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise StrategyError(f"Could not import strategy module {module_name!r}: {exc}") from exc

    try:
        strategy_cls = getattr(module, class_name)
    except AttributeError as exc:
        raise StrategyError(f"Module {module_name!r} has no attribute {class_name!r}") from exc

    if not isinstance(strategy_cls, type):
        raise StrategyError(f"{path!r} does not resolve to a class (got {strategy_cls!r})")

    return cast(type[IStrategy], strategy_cls)


def instantiate_strategy(path: str, params: Mapping[str, Any] | None = None) -> IStrategy:
    """Resolve and construct a strategy, passing `params` as keyword arguments.

    Wraps both signature mismatches (`TypeError` — unknown/missing keyword
    arguments) and constructor-level validation failures (`ValueError` — e.g.
    `SmaCrossoverStrategy`'s `fast_period >= slow_period` check) in
    `StrategyError`, so callers never need to know which exception type a
    given strategy's `__init__` happens to raise for bad params.
    """
    strategy_cls = load_strategy_class(path)
    try:
        return strategy_cls(**dict(params or {}))
    except (TypeError, ValueError) as exc:
        raise StrategyError(
            f"Failed to instantiate strategy {path!r} with params {dict(params or {})!r}: {exc}"
        ) from exc
