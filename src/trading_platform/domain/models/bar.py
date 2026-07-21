from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_platform.domain.errors import ValidationError


@dataclass(frozen=True, slots=True)
class Bar:
    """A single OHLCV candle for a symbol/timeframe, keyed by open time (UTC)."""

    symbol: str
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValidationError(
                f"Bar high ({self.high}) is below low ({self.low}) "
                f"for {self.symbol}@{self.timestamp.isoformat()}"
            )
        if not (self.low <= self.open <= self.high):
            raise ValidationError(
                f"Bar open ({self.open}) outside [low, high] "
                f"for {self.symbol}@{self.timestamp.isoformat()}"
            )
        if not (self.low <= self.close <= self.high):
            raise ValidationError(
                f"Bar close ({self.close}) outside [low, high] "
                f"for {self.symbol}@{self.timestamp.isoformat()}"
            )
        if self.volume < 0:
            raise ValidationError(f"Bar volume cannot be negative: {self.volume}")
