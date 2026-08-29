from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_platform.domain.errors import MarketDataError
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.ingest import DataIngestService


class FakeExchangeAdapter:
    """Serves pages of bars from a pre-baked list, mimicking paginated fetch_ohlcv."""

    exchange_name = "fake"

    def __init__(self, all_bars: list[Bar], page_size: int = 1000) -> None:
        self._all_bars = sorted(all_bars, key=lambda b: b.timestamp)
        self._page_size = page_size
        self.calls: list[dict] = []

    def fetch_ohlcv(self, symbol: str, timeframe: str, since=None, limit=None) -> list[Bar]:
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "since": since})
        # Mimics a real exchange's own hard per-request cap, independent of
        # whatever `limit` the caller asks for — exercises pagination even
        # though `DataIngestService` always requests `_FETCH_LIMIT` (1000).
        effective_limit = min(limit or self._page_size, self._page_size)
        candidates = [bar for bar in self._all_bars if since is None or bar.timestamp >= since]
        return candidates[:effective_limit]

    def fetch_instrument_rules(self, symbol: str):  # pragma: no cover - unused in these tests
        raise NotImplementedError

    def place_order(self, order):  # pragma: no cover
        raise NotImplementedError

    def cancel_order(self, order_id, symbol):  # pragma: no cover
        raise NotImplementedError

    def get_balance(self, asset):  # pragma: no cover
        raise NotImplementedError

    def fetch_order(self, order_id, symbol):  # pragma: no cover
        raise NotImplementedError


class FakeRepository:
    def __init__(self) -> None:
        self.saved: dict[tuple[str, str], dict[datetime, Bar]] = {}

    def save_bars(self, symbol: str, timeframe: str, bars: list[Bar]) -> None:
        bucket = self.saved.setdefault((symbol, timeframe), {})
        for bar in bars:
            bucket[bar.timestamp] = bar

    def load_bars(self, symbol: str, timeframe: str, start=None, end=None):
        bucket = self.saved.get((symbol, timeframe), {})
        yield from sorted(bucket.values(), key=lambda b: b.timestamp)

    def latest_timestamp(self, symbol: str, timeframe: str) -> datetime | None:
        bucket = self.saved.get((symbol, timeframe), {})
        return max(bucket) if bucket else None


def _make_bars(
    count: int, start: datetime, step: timedelta, make_bar: Callable[..., Bar]
) -> list[Bar]:
    return [make_bar(timestamp=start + step * i) for i in range(count)]


class TestSync:
    def test_persists_all_fetched_bars_on_first_sync(self, make_bar: Callable[..., Bar]) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = _make_bars(5, start, timedelta(hours=1), make_bar)
        exchange = FakeExchangeAdapter(bars)
        repository = FakeRepository()
        service = DataIngestService(exchange, repository)

        new_count = service.sync("BTC/USDT", "1h", since=start)

        assert new_count == 5
        assert len(list(repository.load_bars("BTC/USDT", "1h"))) == 5

    def test_resumes_from_latest_stored_timestamp(self, make_bar: Callable[..., Bar]) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = _make_bars(5, start, timedelta(hours=1), make_bar)
        exchange = FakeExchangeAdapter(bars)
        repository = FakeRepository()
        repository.save_bars("BTC/USDT", "1h", bars[:2])  # pretend first 2 already downloaded

        service = DataIngestService(exchange, repository)
        new_count = service.sync("BTC/USDT", "1h", since=start)

        assert new_count == 3
        assert len(list(repository.load_bars("BTC/USDT", "1h"))) == 5

    def test_publishes_bar_closed_with_ingest_mode_for_each_new_bar(
        self, make_bar: Callable[..., Bar], fake_event_bus
    ) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = _make_bars(3, start, timedelta(hours=1), make_bar)
        exchange = FakeExchangeAdapter(bars)
        repository = FakeRepository()
        service = DataIngestService(exchange, repository, fake_event_bus)

        service.sync("BTC/USDT", "1h", since=start)

        bar_closed_events = [e for e in fake_event_bus.published if isinstance(e, BarClosed)]
        assert len(bar_closed_events) == 3
        assert all(e.mode == "ingest" for e in bar_closed_events)

    def test_no_event_bus_is_optional(self, make_bar: Callable[..., Bar]) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = _make_bars(2, start, timedelta(hours=1), make_bar)
        service = DataIngestService(FakeExchangeAdapter(bars), FakeRepository())

        new_count = service.sync("BTC/USDT", "1h", since=start)

        assert new_count == 2

    def test_re_running_sync_is_idempotent(self, make_bar: Callable[..., Bar]) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = _make_bars(5, start, timedelta(hours=1), make_bar)
        exchange = FakeExchangeAdapter(bars)
        repository = FakeRepository()
        service = DataIngestService(exchange, repository)

        service.sync("BTC/USDT", "1h", since=start)
        second_run_new_count = service.sync("BTC/USDT", "1h", since=start)

        assert second_run_new_count == 0
        assert len(list(repository.load_bars("BTC/USDT", "1h"))) == 5

    def test_paginates_across_multiple_fetches(
        self, monkeypatch, make_bar: Callable[..., Bar]
    ) -> None:
        # Match the adapter's per-request cap to what the ingest service asks
        # for, mirroring the real invariant that `BinanceAdapter`'s max OHLCV
        # request size equals `DataIngestService._FETCH_LIMIT`.
        import trading_platform.market_data.ingest as ingest_module

        monkeypatch.setattr(ingest_module, "_FETCH_LIMIT", 3)

        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = _make_bars(10, start, timedelta(hours=1), make_bar)
        exchange = FakeExchangeAdapter(bars, page_size=3)
        repository = FakeRepository()
        service = DataIngestService(exchange, repository)

        new_count = service.sync("BTC/USDT", "1h", since=start)

        assert new_count == 10
        assert len(exchange.calls) >= 4  # several full pages + 1 short page to stop

    def test_raises_when_pagination_never_terminates(self, monkeypatch) -> None:
        """A pathological adapter that always returns a full page of brand-new
        bars never hits the "short page" stop condition — the `_MAX_PAGES`
        safety cap must kick in instead of looping forever.
        """

        class InfinitePageExchangeAdapter:
            exchange_name = "fake"

            def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None) -> list[Bar]:
                page_limit = limit or 1000
                start = since or datetime(2024, 1, 1, tzinfo=UTC)
                return [
                    Bar(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=start + timedelta(hours=i + 1),
                        open=Decimal("100"),
                        high=Decimal("110"),
                        low=Decimal("90"),
                        close=Decimal("105"),
                        volume=Decimal("1"),
                    )
                    for i in range(page_limit)
                ]

        import trading_platform.market_data.ingest as ingest_module

        monkeypatch.setattr(ingest_module, "_MAX_PAGES", 3)
        monkeypatch.setattr(ingest_module, "_FETCH_LIMIT", 2)

        service = DataIngestService(InfinitePageExchangeAdapter(), FakeRepository())

        with pytest.raises(MarketDataError):
            service.sync("BTC/USDT", "1h", since=datetime(2024, 1, 1, tzinfo=UTC))
