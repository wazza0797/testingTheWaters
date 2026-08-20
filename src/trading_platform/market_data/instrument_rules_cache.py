from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.utils.time import to_utc, utc_now

logger = logging.getLogger(__name__)

_DECIMAL_FIELDS = (
    "tick_size",
    "step_size",
    "min_qty",
    "min_notional",
    "maker_fee_rate",
    "taker_fee_rate",
)

DEFAULT_MAX_AGE_HOURS = 24.0


class InstrumentRulesCache:
    """Persists `InstrumentRules` to `{root}/instruments/{exchange}/{symbol}.json`.

    A plain JSON file cache, refreshed by the CLI/`DataIngestService` on
    startup or daily (see `docs/architecture.md`): `load()` returns `None`
    once an entry is older than `max_age_hours`, so callers fall back to
    re-fetching from the exchange. Not exposed as a domain port: it's an
    implementation-only concern with no need to swap backends.
    """

    def __init__(self, root: Path, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> None:
        self._root = root
        self._max_age_hours = max_age_hours

    def _path(self, exchange: str, symbol: str) -> Path:
        safe_symbol = symbol.replace("/", "-")
        return self._root / "instruments" / exchange / f"{safe_symbol}.json"

    def load(self, exchange: str, symbol: str) -> InstrumentRules | None:
        path = self._path(exchange, symbol)
        if not path.exists():
            return None

        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        cached_at_raw = payload.pop("cached_at", None)
        # Cache entries written before this field existed are treated as
        # unconditionally stale rather than raising — safer than trusting an
        # unknown-age cache indefinitely.
        if cached_at_raw is None:
            return None

        cached_at = to_utc(datetime.fromisoformat(cached_at_raw))
        age_hours = (utc_now() - cached_at).total_seconds() / 3600
        if age_hours > self._max_age_hours:
            logger.info(
                "instrument_rules_cache_stale",
                extra={"exchange": exchange, "symbol": symbol, "age_hours": round(age_hours, 2)},
            )
            return None

        for field_name in _DECIMAL_FIELDS:
            payload[field_name] = Decimal(payload[field_name])
        return InstrumentRules(**payload)

    def save(self, rules: InstrumentRules) -> None:
        path = self._path(rules.exchange, rules.symbol)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "exchange": rules.exchange,
            "symbol": rules.symbol,
            "tick_size": str(rules.tick_size),
            "step_size": str(rules.step_size),
            "min_qty": str(rules.min_qty),
            "min_notional": str(rules.min_notional),
            "price_precision": rules.price_precision,
            "qty_precision": rules.qty_precision,
            "maker_fee_rate": str(rules.maker_fee_rate),
            "taker_fee_rate": str(rules.taker_fee_rate),
            "cached_at": utc_now().isoformat(),
        }
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        logger.info(
            "instrument_rules_cached", extra={"exchange": rules.exchange, "symbol": rules.symbol}
        )
