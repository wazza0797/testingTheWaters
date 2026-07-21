from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_platform.domain.errors import ValidationError


@dataclass(frozen=True, slots=True)
class InstrumentRules:
    """Exchange-sourced trading rules for a symbol.

    Fetched via `IExchangeAdapter.fetch_instrument_rules` and cached to
    `data/instruments/{exchange}/{symbol}.json`. Used by `execution/precision.py`
    and the backtest `FillSimulator` to validate and round orders consistently
    across backtest, paper, and live modes.
    """

    exchange: str
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    price_precision: int
    qty_precision: int
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise ValidationError(f"tick_size must be positive: {self.tick_size}")
        if self.step_size <= 0:
            raise ValidationError(f"step_size must be positive: {self.step_size}")
        if self.min_qty < 0:
            raise ValidationError(f"min_qty cannot be negative: {self.min_qty}")
        if self.min_notional < 0:
            raise ValidationError(f"min_notional cannot be negative: {self.min_notional}")
        if self.maker_fee_rate < 0 or self.taker_fee_rate < 0:
            raise ValidationError("Fee rates cannot be negative")
