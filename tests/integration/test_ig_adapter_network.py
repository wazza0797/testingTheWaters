"""Live IG Markets demo adapter integration tests.

Excluded from CI / default suites via ``pytest -m "not network"``.

One-liner (from repo root, demo creds in ``.env``)::

    IG_DEMO_INTEGRATION=1 IG_DEMO_EPIC=CS.D.GBPEUR.CFD.IP uv run pytest -m network tests/integration/test_ig_adapter_network.py -v

Reads only (no orders)::

    uv run pytest -m network tests/integration/test_ig_adapter_network.py -k "not Dealing and not DemoSmoke" -v

The real ``IgRestClient`` paces requests (~3s demo / ~1.5s live). Full suite
takes several minutes — that is intentional.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.integration.ig_demo_support import (
    ACCOUNT_CASH_SENTINEL,
    assert_filled,
    build_demo_adapter,
    epic_under_test,
    flatten_epic,
    market_order,
    position_qty,
    require_dealing_enabled,
    require_ig_demo_credentials,
    require_tradeable,
    wait_for_terminal,
)
from trading_platform.application.demo_smoke import run_demo_smoke
from trading_platform.config.settings import Environment, Settings
from trading_platform.domain.errors import ConfigurationError, ExchangeAdapterError
from trading_platform.domain.models.order import OrderSide, OrderType
from trading_platform.exchanges.factory import build_exchange_adapter
from trading_platform.exchanges.ig.adapter import IgAdapter
from trading_platform.exchanges.ig.client import DEMO_BASE_URL
from trading_platform.portfolio.seed import seed_book_from_exchange

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def settings() -> Settings:
    return require_ig_demo_credentials()


@pytest.fixture(scope="module")
def adapter(settings: Settings) -> IgAdapter:
    ig = build_demo_adapter(settings)
    # Touch session once so later tests share a warm CST.
    _ = ig.get_balance(ACCOUNT_CASH_SENTINEL)
    yield ig
    ig._client.close()


@pytest.fixture(scope="module")
def epic() -> str:
    return epic_under_test()


# ---------------------------------------------------------------------------
# Session / account / factory
# ---------------------------------------------------------------------------


class TestIgSessionAndAccount:
    def test_factory_builds_demo_adapter_against_demo_host(self, adapter: IgAdapter) -> None:
        assert adapter.exchange_name == "ig"
        assert adapter._client.base_url == DEMO_BASE_URL
        assert adapter._client.is_demo is True

    def test_login_establishes_session_tokens(self, adapter: IgAdapter) -> None:
        adapter._client.ensure_session()
        assert adapter._client._cst
        assert adapter._client._security_token

    def test_get_balance_account_cash_positive(self, adapter: IgAdapter) -> None:
        cash = adapter.get_balance(ACCOUNT_CASH_SENTINEL)
        assert cash > 0

    def test_account_currency_populated_after_login(self, adapter: IgAdapter) -> None:
        currency = adapter._client.account_currency
        assert currency is not None
        assert len(currency) == 3

    def test_factory_live_still_refused(self, settings: Settings) -> None:
        with pytest.raises(ConfigurationError, match="not implemented"):
            build_exchange_adapter("ig", Environment.LIVE, settings)


# ---------------------------------------------------------------------------
# Market data / reads
# ---------------------------------------------------------------------------


class TestIgMarketData:
    def test_fetch_instrument_rules(self, adapter: IgAdapter, epic: str) -> None:
        rules = adapter.fetch_instrument_rules(epic)
        assert rules.exchange == "ig"
        assert rules.symbol == epic
        assert rules.allows_short is True
        assert rules.min_qty > 0
        assert rules.step_size > 0
        assert rules.tick_size > 0

    def test_market_status_returns_known_token(self, adapter: IgAdapter, epic: str) -> None:
        status = adapter.market_status(epic)
        assert status is not None
        assert status.upper() in {
            "TRADEABLE",
            "CLOSED",
            "OFFLINE",
            "EDITS_ONLY",
            "ON_AUCTION",
            "ON_AUCTION_NO_EDITS",
            "SUSPENDED",
        }

    def test_fetch_ohlcv_hour_bars(self, adapter: IgAdapter, epic: str) -> None:
        since = datetime.now(tz=UTC) - timedelta(hours=12)
        bars = adapter.fetch_ohlcv(epic, "1h", since=since, limit=10)
        assert len(bars) >= 1
        assert all(bar.symbol == epic for bar in bars)
        assert all(bar.timeframe == "1h" for bar in bars)
        assert bars == sorted(bars, key=lambda b: b.timestamp)

    def test_fetch_ohlcv_day_and_minute_resolutions(self, adapter: IgAdapter, epic: str) -> None:
        day_bars = adapter.fetch_ohlcv(epic, "1d", limit=5)
        assert len(day_bars) >= 1
        minute_bars = adapter.fetch_ohlcv(epic, "5m", limit=5)
        assert len(minute_bars) >= 1

    def test_unsupported_timeframe_raises(self, adapter: IgAdapter, epic: str) -> None:
        with pytest.raises(ExchangeAdapterError, match="Unsupported IG timeframe"):
            adapter.fetch_ohlcv(epic, "3h", limit=1)

    def test_get_balance_flat_epic_is_zero_or_existing(self, adapter: IgAdapter, epic: str) -> None:
        # Read path only — do not flatten here (that would be dealing).
        qty = position_qty(adapter, epic)
        assert isinstance(qty, Decimal)

    def test_seed_book_from_exchange(self, adapter: IgAdapter, epic: str) -> None:
        book = seed_book_from_exchange(adapter, epic, timeframe="1h")
        assert book.cash > 0


# ---------------------------------------------------------------------------
# Error / unsupported adapter surfaces
# ---------------------------------------------------------------------------


class TestIgErrorPaths:
    def test_cancel_order_not_supported(self, adapter: IgAdapter, epic: str) -> None:
        with pytest.raises(ExchangeAdapterError, match="does not cancel"):
            adapter.cancel_order("fake-deal-ref", epic)

    def test_limit_orders_rejected(self, adapter: IgAdapter, epic: str) -> None:
        order = market_order(epic, OrderSide.BUY, Decimal("1"))
        limit = replace(order, order_type=OrderType.LIMIT, price=Decimal("1.0"))
        with pytest.raises(ExchangeAdapterError, match="market orders"):
            adapter.place_order(limit)

    def test_fetch_order_unknown_reference_errors(self, adapter: IgAdapter, epic: str) -> None:
        with pytest.raises(ExchangeAdapterError):
            adapter.fetch_order("definitely-not-a-real-deal-reference-000", epic)


# ---------------------------------------------------------------------------
# Dealing — long / short round-trips (gated)
# ---------------------------------------------------------------------------


@pytest.fixture
def dealing_adapter(adapter: IgAdapter, epic: str) -> IgAdapter:
    require_dealing_enabled()
    require_tradeable(adapter, epic)
    flatten_epic(adapter, epic)
    assert position_qty(adapter, epic) == 0
    yield adapter
    # Always attempt to leave the epic flat for the next run / other tests.
    with contextlib.suppress(ExchangeAdapterError):
        flatten_epic(adapter, epic)
    # Extra pause between dealing scenarios — client already paces calls, but
    # demo api-key windows are shared with any other process using the key.
    time.sleep(5.0)


class TestIgDealingLong:
    def test_long_open_confirm_position_close_flat(
        self, dealing_adapter: IgAdapter, epic: str
    ) -> None:
        rules = dealing_adapter.fetch_instrument_rules(epic)
        qty = rules.min_qty

        open_ref = dealing_adapter.place_order(market_order(epic, OrderSide.BUY, qty))
        open_status = wait_for_terminal(dealing_adapter, open_ref, epic)
        assert_filled(open_status, context="long open")
        assert open_status.average_fill_price is not None
        assert open_status.average_fill_price > 0

        pos = position_qty(dealing_adapter, epic)
        assert pos == qty

        close_ref = dealing_adapter.place_order(market_order(epic, OrderSide.SELL, qty))
        close_status = wait_for_terminal(dealing_adapter, close_ref, epic)
        assert_filled(close_status, context="long close")

        assert position_qty(dealing_adapter, epic) == 0


class TestIgDealingShort:
    def test_short_open_confirm_position_close_flat(
        self, dealing_adapter: IgAdapter, epic: str
    ) -> None:
        rules = dealing_adapter.fetch_instrument_rules(epic)
        assert rules.allows_short is True
        qty = rules.min_qty

        open_ref = dealing_adapter.place_order(market_order(epic, OrderSide.SELL, qty))
        open_status = wait_for_terminal(dealing_adapter, open_ref, epic)
        assert_filled(open_status, context="short open")

        pos = position_qty(dealing_adapter, epic)
        assert pos == -qty

        close_ref = dealing_adapter.place_order(market_order(epic, OrderSide.BUY, qty))
        close_status = wait_for_terminal(dealing_adapter, close_ref, epic)
        assert_filled(close_status, context="short close")

        assert position_qty(dealing_adapter, epic) == 0


class TestIgDemoSmokePath:
    def test_application_demo_smoke_long_roundtrip(
        self, dealing_adapter: IgAdapter, epic: str
    ) -> None:
        """Exercise the same path as ``trading-platform demo-smoke``."""
        result = run_demo_smoke(
            dealing_adapter,
            epic,
            side=OrderSide.BUY,
            close=True,
            poll_interval_sec=2.5,
            max_polls=30,
        )
        assert result.exchange == "ig"
        assert result.open_status.state.value == "filled"
        assert result.close_status is not None
        assert result.close_status.state.value == "filled"
        assert position_qty(dealing_adapter, epic) == 0
