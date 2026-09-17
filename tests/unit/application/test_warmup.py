"""Unit tests for demo/paper strategy warmup bar loading."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.application.warmup import load_warmup_bars, resolve_warmup_bar_count
from trading_platform.domain.models.bar import Bar

_BASE = datetime(2024, 1, 1, tzinfo=UTC)


def _bar(offset: int, symbol: str = "IX.D.SPTRD.DAILY.IP") -> Bar:
    return Bar(
        symbol=symbol,
        timeframe="1d",
        timestamp=_BASE + timedelta(days=offset),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1"),
    )


class _Repo:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = list(bars)
        self.save_calls = 0

    def load_bars(self, symbol: str, timeframe: str, start=None, end=None):  # noqa: ANN001
        return iter(self._bars)

    def save_bars(self, symbol: str, timeframe: str, bars: list[Bar]) -> None:
        self.save_calls += 1
        by_ts = {b.timestamp: b for b in self._bars}
        for bar in bars:
            by_ts[bar.timestamp] = bar
        self._bars = sorted(by_ts.values(), key=lambda b: b.timestamp)

    def latest_timestamp(self, symbol: str, timeframe: str) -> datetime | None:
        return self._bars[-1].timestamp if self._bars else None


class _Adapter:
    max_ohlcv_limit = 500

    def __init__(self, bars: list[Bar] | None = None) -> None:
        self._bars = bars or []
        self.fetch_calls = 0

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Bar]:
        self.fetch_calls += 1
        return list(self._bars)


class TestResolveWarmupBarCount:
    def test_connors_sma200(self) -> None:
        assert resolve_warmup_bar_count({"sma_regime_period": 200}) == 220

    def test_defaults_floor(self) -> None:
        assert resolve_warmup_bar_count({}) >= 70


class TestLoadWarmupBars:
    def test_prefers_cache_when_long_enough(self) -> None:
        cached = [_bar(i) for i in range(100)]
        adapter = _Adapter([_bar(i) for i in range(50)])
        bars = load_warmup_bars(_Repo(cached), adapter, "IX.D.SPTRD.DAILY.IP", "1d", min_bars=50)
        assert len(bars) == 50
        assert bars[0].timestamp == _BASE + timedelta(days=50)
        assert adapter.fetch_calls == 0

    def test_falls_back_to_venue_when_cache_short(self) -> None:
        adapter = _Adapter([_bar(i) for i in range(80)])
        repo = _Repo([_bar(0)])
        bars = load_warmup_bars(repo, adapter, "IX.D.SPTRD.DAILY.IP", "1d", min_bars=70)
        assert len(bars) == 70
        assert adapter.fetch_calls == 1
        assert repo.save_calls == 1
        assert len(repo._bars) == 80

    def test_through_filters_resume_cursor(self) -> None:
        cached = [_bar(i) for i in range(10)]
        through = _BASE + timedelta(days=4)
        bars = load_warmup_bars(
            _Repo(cached),
            _Adapter(),
            "IX.D.SPTRD.DAILY.IP",
            "1d",
            min_bars=3,
            through=through,
        )
        assert len(bars) == 3
        assert bars[-1].timestamp == through
        assert all(b.timestamp <= through for b in bars)

    def test_through_with_short_cache_fetches_and_persists(self) -> None:
        """Resume cursor must not skip venue backfill when parquet is empty."""
        through = _BASE + timedelta(days=60)
        adapter = _Adapter([_bar(i) for i in range(80)])
        repo = _Repo([])
        bars = load_warmup_bars(
            repo,
            adapter,
            "IX.D.SPTRD.DAILY.IP",
            "1d",
            min_bars=50,
            through=through,
        )
        assert adapter.fetch_calls == 1
        assert repo.save_calls == 1
        assert len(bars) == 50
        assert bars[-1].timestamp == through
        assert all(b.timestamp <= through for b in bars)
