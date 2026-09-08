from __future__ import annotations

from trading_platform.domain.errors import StrategyError


class ConditionError(StrategyError):
    """Raised when a condition tree (entry/exit/regime `when`) fails to
    parse or validate — malformed YAML shape, an unknown combinator/leaf
    type, a bad comparison operator, an unknown indicator name, or an empty
    `all`/`any` list. Always raised at strategy construct time (see
    `RuleStrategy`/`RegimeRouterStrategy`), never mid-run, so a bad recipe
    fails fast with a clear message instead of silently misbehaving on the
    first bar.
    """
