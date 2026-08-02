# vectorbt (OSS) — Engineering Assessment

*Researched 2026-08-02 from github.com/polakowo/vectorbt, vectorbt.dev, vectorbt.pro, PyPI, and GitHub issues.*

## 1. Project health, versions, maintenance status, PRO relationship

**Releases (PyPI-confirmed dates):**
- **v1.1.0 — July 5, 2026 (latest)**; v1.0.0 — April 22, 2026; 0.28.5 — Mar 26, 2026; 0.28.4 — Jan 26, 2026; 0.28.2 — Dec 12, 2025; 0.28.1 — Aug 16, 2025; 0.28.0 — Jun 29, 2025; 0.27.3 — May 2, 2025; 0.27.0 — Dec 18, 2024.
- **Last commit: July 14, 2026** (master; "Improve rolling std numerical stability"), with recent PRs merged from several outside contributors. ~8.5k stars.

**Maintenance status — important nuance:** the old reputation ("OSS is frozen in maintenance mode, pinned to ancient numba/numpy") was accurate for the 0.2x era, but is **out of date as of 2026**. The project was revamped: v1.0.0 (Apr 2026) rebranded OSS as *"the open-source community edition of VectorBT PRO"* and added an optional **Rust backend** (`vectorbt-rust`, extra `pip install "vectorbt[rust]"`, `engine=` argument dispatches numba vs rust). v1.1.0 added Python 3.14, pandas 3, NumPy 2.4+, Numba 0.66+ support. It is actively maintained again.

**Dependency constraints (verbatim from master `pyproject.toml`) — the pin problem has inverted:**
- `requires-python = ">=3.11,<3.15"`
- `numpy>=2.4.6`, `pandas>=3.0.3,<4.0`, `numba>=0.66`
- **Caveat:** instead of pinning *old* numba/numpy, v1.x now demands a *cutting-edge* stack (Python ≥3.11, NumPy ≥2.4, pandas ≥3.0). If you're on Python 3.10 or pandas 1.x/2.x, you're stuck on vectorbt ≤0.28.x, which is the old code line with the old constraints.
- Extras: `rust = ["vectorbt-rust==1.1.0"]`; `full = ["TA-Lib", "yfinance>=0.2.22", "python-binance", "ccxt>=4.0.14", "alpaca-py", "ray>=1.4.1", "ta", "pandas-ta-classic", "python-telegram-bot>=13.4", "quantstats>=0.0.37"]`. Note `pandas-ta-classic` (the maintained fork) replaced the original `pandas-ta`.

**License:** "fair-code," Apache 2.0 **with Commons Clause** — free to use, but you "may not sell products or services that are primarily this software."

**OSS vs PRO (vectorbtpro):** PRO is a proprietary, subscription/sponsorship-gated rewrite (private repo, private docs/Discord). PRO-only features (from vectorbt.pro/features): **limit orders with time-in-force (DAY, GTC, GTD, LOO, FOK)** via `order_type="limit"`/`limit_delta`; **leverage/margin** (`leverage`, lazy/eager modes); **time stops** (`td_stop`/`dt_stop`); **stop laddering** (`stop_ladder`); **`delta_format`** for stops (absolute/target/percent); contract multipliers (futures); position stacking; cash deposits/dividends (`cash_deposits`, `cash_dividends`); simulation chaining (`last_state`); the full **`vbt.Splitter`** class; `@vbt.parameterized` decorator; **chunking/parallelization infrastructure** and disk offloading for billion-combination sweeps; indicator search across libraries; richer callbacks (`pre_segment_func_nb`, `post_order_func_nb` in from_signals context). OSS retains the core: vectorized `Portfolio.from_signals`/`from_orders`/`from_order_func`, IndicatorFactory, basic splitters, stats/records.

## 2. Custom indicators — IndicatorFactory

Yes, fully supported in OSS, and it is the canonical mechanism:

- `vbt.IndicatorFactory(input_names=[...], param_names=[...], output_names=[...], in_output_names=[...])`.
- **`.from_apply_func(func)`** wraps an arbitrary NumPy or Numba-compiled function; the function receives broadcast input arrays plus a *single* parameter combination per call; the factory handles parameter iteration, output concatenation into columns, and broadcasting. Optional `cache_func` for shared precomputation across combos.
- **`.from_custom_func(func)`** for full control (receives all parameter arrays at once, `var_args=True`, `keyword_only_args=True` supported); you handle concatenation.
- **Multiple parameter combinations:** pass lists per parameter; `param_product=True` builds the Cartesian product; `per_column=True` maps one param set per input column; `param_settings` (`is_array_like`, `bc_to_input`, `per_column`) controls interpretation. Each combination becomes a column level in a MultiIndex.
- Generated classes get `.run()` and `.run_combs()` (all pairwise/n-wise combinations, e.g., fast/slow MA crossovers, with "smart caching" to avoid recomputation).
- **Third-party parsers in OSS:** `IndicatorFactory.from_talib("SMA")` (wraps any TA-Lib function, auto-extracting inputs/params/outputs), `IndicatorFactory.from_pandas_ta("adx")`, and `from_ta` for the `ta` library. Docs claim "99% of indicators from Technical Analysis Library, Pandas TA, and TA-Lib" are wrappable. So SMA, EMA, BBands, **ADX/DMI, Stochastic, TRIX** are all reachable via TA-Lib/pandas-ta wrappers even though ADX/TRIX are not native. TA-Lib itself requires the C library installed (the usual TA-Lib install pain, unchanged).

## 3. Built-in (native) indicators in OSS (`vectorbt.indicators.basic`)

Exhaustive list per current API docs — 8 indicators:

| Indicator | Inputs | Params | Outputs |
|---|---|---|---|
| `MA` | close | window, ewm | ma (ewm=True gives EMA) |
| `MSTD` | close | window, ewm | mstd |
| `BBANDS` | close | window, ewm, alpha | lower, middle, upper, bandwidth, percent_b |
| `RSI` | close | window, ewm | rsi |
| `STOCH` | high, low, close | k_window, d_window, ewm | percent_k, percent_d |
| `MACD` | close | fast_window, slow_window, signal_window, ewm | macd, signal, hist |
| `ATR` | high, low, close | window, ewm | tr, atr |
| `OBV` | close, volume | — | obv |

No native ADX/DMI or TRIX — use `from_talib`/`from_pandas_ta` (see §2).

## 4. Portfolio.from_signals (OSS)

All key features exist in OSS. Full signature confirmed from master source (`vectorbt/portfolio/base.py`). Key parameters:

- **Signals:** `entries`, `exits`, `short_entries`, `short_exits` (all boolean arrays, broadcast); `direction` (`LongOnly`/`ShortOnly`/`Both`, only used when short arrays not given); conflict resolution via `upon_long_conflict`, `upon_short_conflict`, `upon_dir_conflict`, `upon_opposite_entry`; dynamic signals via `signal_func_nb`/`signal_args`.
- **Costs:** `fees` — "Fees in percentage of the order value" (fraction, broadcastable per element); `fixed_fees` — "Fixed amount of fees to pay per order"; `slippage` — "Slippage in percentage of price".
- **Sizing:** `size`, `size_type` — in from_signals only `SizeType.Amount`, `SizeType.Value`, `SizeType.Percent` are supported (Target* types are from_orders territory); plus `min_size`, `max_size`, `size_granularity`, `allow_partial`, `lock_cash`, `accumulate` (AccumulationMode).
- **Stops:** `sl_stop` — "A percentage below/above the acquisition price for long/short position. **Note that 0.01 = 1%**" — so 0.5% = `0.005`, 1% = `0.01`. `tp_stop` — same semantics, opposite direction. `sl_trail` (bool, broadcastable) makes `sl_stop` trailing (TSL). All broadcast element-wise, so you can sweep stop values as parameter columns.
- **Intra-bar OHLC stop evaluation:** `open`, `high`, `low` parameters are "used solely for stop signals." `high` "Defaults to np.nan, which gets replaced by the maximum out of open and close"; `low` analogously with the minimum. I.e., pass real OHLC and stops are checked against the bar's high/low range; without them it degrades to open/close bounds. Stops can trigger on the entry bar itself (long-standing documented behavior, cf. discussion #188).
- **Stop pricing/behavior enums:** `stop_entry_price` (`StopEntryPrice`: `ValPrice`, `Price`, `FillPrice` — slippage applied, `Close`) sets the reference price for the stop level; `stop_exit_price` (`StopExitPrice`: `StopLimit`, `StopMarket` — slippage applied, `Price`, `Close`) sets the execution price when triggered; `upon_stop_exit` (`StopExitMode`: `Close`, `CloseReduce`, `Reverse`, `ReverseReduce`); `upon_stop_update` (`StopUpdateMode`: `Keep`, `Override`, `OverrideNaN`, relevant with accumulation); `adjust_sl_func_nb`/`adjust_tp_func_nb` allow per-bar dynamic stop adjustment in numba; `use_stops` toggles the stop machinery.
- **Other:** `init_cash`, `cash_sharing`, `call_seq`, `val_price`, `ffill_val_price`, `update_value`, `max_orders`, `max_logs`, `seed`, `group_by`, `broadcast_kwargs`, `freq`, `engine` (numba/rust dispatch, new in 1.x).

**Not in OSS:** limit orders/TIF, time-based stops (`td_stop`/`dt_stop`), absolute-price stop formats (`delta_format`), stop ladders, leverage — all PRO (see §1).

## 5. Parameter sweeps and memory

**Mechanism:** everything is 2D — rows = time, columns = asset × parameter combination. Indicator params (`param_product=True`, `run_combs`) and broadcastable simulation args (`fees`, `sl_stop`, etc. accept arrays keyed by column levels / `broadcast_named_args`) expand into extra columns with a MultiIndex; one numba (or Rust) pass simulates all columns. Inputs that don't vary are broadcast as `np.broadcast_to` views (near-zero copy); README claims ~1,000,000 orders filled in 70–100 ms (Apple M1).

**Memory reality check (corroborated by issue #406 — users OOM at ~40k combos × 39k bars even with 32 GB):**
- Signal arrays are bool (1 byte/element); float arrays are float64 (8 bytes).
- Single symbol, ~3M 1-minute bars: one float64 column = 24 MB. With 1,000 param combos: entries+exits bool = 2 × 3 GB = 6 GB; any materialized float64 output (e.g., `pf.value()`, `pf.returns()`, indicator outputs per combo) = **24 GB per array**. Order records themselves are compact (structured array scaling with trade count), but post-simulation analytics that return row×column frames re-materialize full matrices.
- ~400M rows (8y × 1min × large universe) with even modest per-symbol parameter grids is **not feasible in one broadcast pass** on a single machine; you must loop/chunk manually (per symbol, per parameter batch), extract compact results (`pf.stats()`, trade records), and discard the Portfolio object each iteration. OSS has no built-in chunking/parallelization framework — that (plus disk offloading and `@parameterized` with `chunked=True`) is a headline PRO feature. In OSS the community pattern is a Python loop over combo chunks, optionally with `ray` (an optional extra) for parallelism.

## 6. Intraday / session logic

**No built-in session support in OSS.** There is no "trade only 09:30–16:00" or "force close at EOD" parameter in `Portfolio.from_signals`. Evidence: open GitHub issues asking exactly this — #28 "Trading schedule", #731 "Closing all positions at the end of the day", #743 "Intraday trades not closing correctly with autoclose + stop loss" — all answered with signal-array workarounds, none with a built-in. (PRO's `dt_stop` time stop covers "close at end of day" natively.)

**Standard OSS patterns (encode it in the boolean arrays):**
- Session mask: `idx = df.index.indexer_between_time("09:30", "16:00")` (or `df.between_time`) → set `entries` False outside the mask.
- EOD flat: mark the last bar of each session as a forced exit, e.g. `eod = ~np.append(df.index.date[1:] == df.index.date[:-1], False)`; `exits |= eod`, or `exits[df.index.time == time(15, 59)] = True`.
- Combine with `upon_long_conflict`/ConflictMode (`Exit` priority) so the EOD exit wins over a simultaneous entry, and be aware `accumulate` + sized exits closes only part of the position (the #731 complaint) — for full EOD close use default full-close exit semantics.
- For logic that must react to state (e.g., "no new entries in last 30 min *if* flat"), use `signal_func_nb` (numba callback, available in OSS from_signals).

## 7. Performance analytics and walk-forward

**`Portfolio.stats()`** metric set: Start/End Value, Total Return [%], Benchmark Return [%], Max Gross Exposure [%], Total Fees Paid, Max Drawdown [%] + Duration, Total Trades, **Win Rate [%]**, Best/Worst Trade [%], Avg Winning/Losing Trade [%], **Profit Factor**, **Expectancy**, **Sharpe Ratio**, Calmar, Omega, Sortino.
- **Records:** `pf.trades` / `pf.positions` (entry/exit price, size, PnL, return, duration, direction per trade), `pf.orders`, `pf.logs`, `pf.drawdowns` — all compact structured-array Records classes with their own `.stats()`.
- **Returns:** numba-compiled empyrical-equivalent metrics with rolling variants via the returns accessor (`pf.returns_acc.sharpe_ratio()` etc.), optional Rust acceleration, plus a **QuantStats adapter** for tear sheets.

**Walk-forward in OSS:** yes, via the older splitter API in `vectorbt.generic.splitters`: `RangeSplitter`, `RollingSplitter`, `ExpandingSplitter` ("Expanding walk-forward splitter"), `split_ranges_into_sets()` (`set_lens=(0.5, 0.25)` → train/valid/test), used through accessor methods `df.vbt.range_split()`, `df.vbt.rolling_split()`, `df.vbt.expanding_split()`; scikit-learn splitters (KFold etc.) are also accepted. The modern, far more capable **`vbt.Splitter` class is PRO-only** — OSS walk-forward means manually re-running the pipeline per split, which is workable but boilerplate-heavy.

---

### Bottom line

OSS vectorbt in mid-2026 is healthier than its reputation: actively maintained (v1.1.0, July 2026), modern numpy 2.4/pandas 3/numba 0.66 stack, optional Rust engine, and it covers everything needed for signal-based backtesting (long/short signals, percent + fixed fees, slippage, fractional sl/tp/trailing stops with intra-bar high/low evaluation). The real gaps vs PRO for an intraday minute-data workload: no session/time-stop primitives (manual signal encoding required), no chunking/parallelization framework (manual batching required at scale), no limit orders/leverage, and the basic splitter API for walk-forward. The new hard floor of Python ≥3.11 / numpy ≥2.4.6 / pandas ≥3.0.3 is the main version caveat — older environments are confined to the 0.28.x line.

Sources: [GitHub repo](https://github.com/polakowo/vectorbt) · [Releases](https://github.com/polakowo/vectorbt/releases) · [pyproject.toml](https://raw.githubusercontent.com/polakowo/vectorbt/master/pyproject.toml) · [PyPI](https://pypi.org/project/vectorbt/) · [Features](https://vectorbt.dev/getting-started/features/) · [IndicatorFactory API](https://vectorbt.dev/api/indicators/factory/) · [Basic indicators](https://vectorbt.dev/api/indicators/basic/) · [Portfolio API](https://vectorbt.dev/api/portfolio/base/) · [Portfolio enums](https://vectorbt.dev/api/portfolio/enums/) · [Splitters](https://vectorbt.dev/api/generic/splitters/) · [PRO features overview](https://vectorbt.pro/features/overview/) · [PRO portfolio features](https://vectorbt.pro/features/portfolio/) · [Issue #731 EOD close](https://github.com/polakowo/vectorbt/issues/731) · [Issue #28 trading schedule](https://github.com/polakowo/vectorbt/issues/28) · [Issue #743 intraday autoclose](https://github.com/polakowo/vectorbt/issues/743) · [Issue #406 memory](https://github.com/polakowo/vectorbt/issues/406) · [Discussion #188 same-bar stops](https://github.com/polakowo/vectorbt/discussions/188)
