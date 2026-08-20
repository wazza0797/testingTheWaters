# Milestone 2 — Indicator Engine

**Status:** Complete

## Goals

Reusable, testable technical indicators computed over a bar series — the
first building block strategies (M3) will read from.

## Design Decisions (confirmed with the user before implementation)

Three real trade-offs were resolved up front rather than assumed:

1. **Numeric type — `float64`, not `Decimal`.** Every other domain value
   (`Bar`, `Order`, `Fill`, ...) is `Decimal` throughout this codebase.
   Indicators are the one deliberate exception: they're signal-generation
   inputs (thresholds, crossovers), not money/quantity values that get
   persisted or accounted for, and pandas' vectorized rolling/smoothing math
   is only correct and efficient on floats — every reference indicator
   implementation (TA-Lib, pandas-ta, TradingView) does the same. The
   conversion happens at exactly one boundary (`indicators/utils.py`); `Bar`
   and everything downstream of a fill remain `Decimal`.
2. **RSI variant — Wilder's smoothed RSI**, the original 1978 formula and
   what TradingView/StockCharts/most brokers mean by "RSI", not the simpler
   (and less common) plain-moving-average variant.
3. **Dependency — added `pandas`**, as already earmarked in the original
   architecture doc and `pyproject.toml` comments, rather than hand-rolling
   rolling-window/NaN handling with stdlib.

## Delivered

- `pyproject.toml` — added `pandas` (runtime) and `pandas-stubs` (dev, for
  `mypy --strict` against pandas' types). Also fixed a latent inconsistency:
  `[tool.mypy] python_version` was pinned to `"3.11"` while `.python-version`
  (and CI) actually run `3.12` — harmless while nothing needed 3.12-only
  stub syntax, but `numpy`'s stubs (a pandas dependency) do, so it surfaced
  now. Bumped to `"3.12"` to match the environment that's actually used.
- `indicators/sma.py` — `compute_sma(closes, period)`: `Series.rolling(period).mean()`.
- `indicators/ema.py` — `compute_ema(closes, period)`: seeded with the
  simple average of the first `period` closes, then the standard recursive
  formula (`ema[i] = (close[i] - ema[i-1]) * multiplier + ema[i-1]`,
  `multiplier = 2/(period+1)`) for every bar after. Deliberately **not**
  `Series.ewm(...)` — pandas' default (`adjust=True`) computes a
  differently-weighted average over the *entire* history rather than this
  textbook recursive formula, which would silently produce different
  numbers than every other trading platform means by "EMA" (verified by a
  regression test that asserts our output differs from `ewm(adjust=True)`).
- `indicators/rsi.py` — `compute_rsi(closes, period=14)`: Wilder's smoothing
  (first average = simple mean of first `period` gains/losses; every
  average after = `(prev * (period-1) + current) / period`; `RSI = 100 -
  100/(1+RS)`), with the documented zero-division conventions (`RSI=100`
  all-gains, `RSI=0` all-losses, `RSI=50` no movement).
- `indicators/registry.py` — `IndicatorRegistry` (`register`/`get`/`compute`/
  `available`) and `build_default_registry()` pre-populated with
  `sma`/`ema`/`rsi`, so Milestone 3 strategies can reference an indicator by
  name from config without importing indicator modules directly.
- `indicators/utils.py` — `closes_from_bars(bars) -> pd.Series`: the single
  `Decimal` → `float64` conversion boundary, indexed by bar timestamp.
- `tests/unit/indicators/` — 40 tests across `test_sma.py`, `test_ema.py`,
  `test_rsi.py`, `test_registry.py`, `test_utils.py`.

## Acceptance Criteria

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Indicators are deterministic and side-effect free | ✅ | Unit-tested per indicator (`test_deterministic`, `test_does_not_mutate_input`). Independently re-verified outside the test suite: interleaved calls to `compute_sma`/`compute_rsi` across two different series and two separate `IndicatorRegistry` instances produce bit-identical results on repeat calls — confirms no shared/global state leaks between calls. |
| No dependency on exchange or filesystem | ✅ | `rg` over `src/trading_platform/indicators/` confirms the only non-stdlib imports are `pandas` and `trading_platform.domain.models.bar` (a pure, in-memory dataclass); zero references to `exchanges/`, `ccxt`, `open(`, `Path(`, sockets, or `requests` in actual code (only in a docstring explaining the constraint). |
| Each indicator matches known-value vectors | ✅ (beyond the stated criteria, per the M1 lesson about verifying rather than assuming) | **SMA/EMA:** hand-derived step-by-step by an independent calculation (not by re-running the implementation) and asserted against. **RSI:** cross-checked against a published, third-party worked example (14-period Wilder RSI on a 15-close EUR/USD series) — sum of gains (0.0185), sum of losses (0.0070), and the resulting RSI (72.549019...) match the external source's own arithmetic exactly. A second, independently hand-derived RSI case at `period=2` was added after review, since the published example only exercises `period=14` — confirms the recursive smoothing step itself is correct, not just the one hardcoded period. |
| `uv run pytest -m "not network"` passes | ✅ | 198 tests (158 pre-existing + 40 new) |
| `uv run mypy src` / `ruff check` / `ruff format --check` | ✅ | all clean, strict mode |

## Known Gaps (by design — later milestones)

- No incremental/streaming indicator computation — every call recomputes
  over the full series passed in. Acceptable for this milestone's explicit
  "pure function, side-effect free" requirement; if per-bar streaming
  becomes a performance concern in M6 (paper trading), it can be added as a
  stateful wrapper around these same pure functions without changing their
  signatures.
- `IndicatorRegistry` only knows `sma`/`ema`/`rsi` (the milestone's minimum
  viable set). Multi-output indicators (e.g. MACD) and indicator-of-indicator
  composition are not yet needed by any strategy and are deferred until M3
  actually requires them.
