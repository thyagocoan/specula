# Feature request: IB tape recording + order-flow features

Status: **designed, not started** (parked 2026-08-02). Decision locked in by
the owner: **IB is the production data source** — tape recording starts the
day live trading starts, and everything runs on IB data in production.
Alpaca remains the research bridge only (historical bars for setup
discovery); it is NOT part of the production path.

## 1. Goal

Two deliverables, one data stream:

1. **Order-flow features as gates** — buy/sell pressure, relative volume,
   large-trade share — computed at 1-minute resolution and used as entry
   gates on validated setups, testable with full League discipline (the
   FF-candle volume gate already proved this vein: it was the single
   strongest lever in the fffd_ff family).
2. **A growing private archive of tape (+ optionally book) data** so that
   deeper microstructure ideas become *backtestable* months from now instead
   of remaining forever discretionary. Rule of the house: never trade a
   signal that cannot be validated.

## 2. Why IB (and what Alpaca is for)

- The owner trades through IB and will run production there: one venue for
  data, execution and recording removes a whole class of mismatches
  (feed vs fill discrepancies, symbol mapping, clock skew).
- IB provides for subscribed users: real-time L1 (bid/ask/last), **time &
  sales (tape)**, and **L2 depth** (top ~10 levels, venue-dependent) via the
  TWS API — all recordable.
- IB does **not** provide deep historical tick/L2 backfill. That is the
  catch and the reason to START RECORDING EARLY: the archive only grows
  forward from the day the recorder runs.
- Optional backfill for the *trades* stream (not book) if waiting is too
  slow: databento (metered, one big pull) or Alpaca Algo Trader Plus
  (~$99/mo, full SIP trades via the API we already integrate). Buying
  backfilled trades + recording IB book forward is a sensible hybrid.

## 3. Recorder design (runs from day one of live trading)

New service `recorder` in docker-compose (same pattern as `bot`):

- `src/specula/recorder.py` using `ib_async` against IB Gateway.
- Subscriptions per roster symbol (start with the scanner roster only —
  ~10-30 symbols, not the full 250):
  - `reqTickByTickData("AllLast")` → tape: ts, price, size, exchange,
    special conditions.
  - `reqMktData` L1 → NBBO snapshots (needed later for buy/sell
    classification better than the tick rule).
  - Optional phase 2: `reqMktDepth` → L2 top-10 book snapshots on change
    (largest volume by far — gate behind a config flag).
- IB constraint: tick-by-tick subscriptions are capped (typically 3-10
  simultaneous depending on account); for wider coverage use
  `reqMktData` 250ms snapshots instead of true tick-by-tick — fine for
  1-minute feature aggregation.
- Storage layout (raw, append-only):
  `data/raw/ibtape/kind=trades/symbol=TEL/date=2026-08-10.parquet`
  (one file per symbol-day, zstd; L1 under kind=quotes, book under
  kind=depth). Expected volume: trades+quotes for 30 liquid names ≈
  0.5-2 GB/day compressed; depth 5-20 GB/day (why it's phase 2).
- Recorder health: heartbeat into scanner_state-style JSON + Telegram
  alert if the stream dies during market hours.

## 4. Feature build (nightly, in daily_update)

`scripts/build_tape_features.py` → 1-minute feature bars per symbol-day:

| feature | definition | use |
|---|---|---|
| `vol_delta` | buy volume − sell volume (tick rule vs prevailing NBBO mid when quotes exist) | pressure gate: fade entries only when delta confirms exhaustion flipping |
| `delta_ratio` | vol_delta / total volume | normalized pressure |
| `rvol` | volume vs same-minute-of-day 20-day average | "is anything happening" gate |
| `big_share` | volume in trades ≥ $50k / total | institutional participation |
| `trade_count`, `avg_trade_usd` | activity texture | retail-vs-institutional days |
| `spread_bps` (from quotes) | time-weighted spread | execution-cost model per symbol — feeds honest slippage |

Stored as `data/silver/tape_features_1m/symbol=*/...` — a few MB/day total.

## 5. Integration (all existing machinery)

- New gate family in `features.py`: `{"ind": "pressure", "col": "delta_ratio",
  "op": "gt|lt", "x": 0.2, "lookback_min": 15}` etc. — same
  `regime_entry_mask` dispatch, sampled by the explorer like every gate.
- League/explorer validate as usual. IMPORTANT HONESTY RULE: tape features
  only exist from recording-start forward, so configs gated on them must be
  evaluated ONLY on that window (League `evaluate()` needs a
  `min_data_start` guard per config — small change, do not forget it, or
  gated configs silently backtest gateless on old data).
- `spread_bps` additionally upgrades the backtest slippage model from the
  flat 0.01% to per-symbol measured costs — worth doing for the roster even
  if no gate ever passes.

## 6. Phasing

1. **Day one of live trading:** recorder for roster symbols, trades + L1
   quotes only. Zero strategy work — just accumulate.
2. **After ~4-6 weeks of data:** build tape features, add pressure gates,
   run a league round restricted to the recorded window. Also recompute
   per-symbol spread costs and recalibrate backtest slippage.
3. **Optional:** buy historical SIP trades backfill (databento one-shot or
   Alpaca ATP month) to extend the testable window backward — trades only;
   the book stream stays forward-only by nature.
4. **Phase 2 (only if pressure gates prove OOS value):** add L2 depth
   recording and book-imbalance features.

## 7. Risks / honesty notes

- **Latency reality:** IB retail API latency (~100-300ms) means these
  features inform minutes-scale entries; sub-second tape strategies stay
  out of scope — that race is lost by construction.
- **Recorder discipline:** gaps in the archive (crashed recorder, closed
  laptop) poison same-minute-of-day baselines. The container must run on
  always-on hardware and alert on stream loss.
- **Multiple comparisons:** pressure gates multiply the search space; keep
  the family small and hypothesis-driven, and lean on the post-discovery
  column as always.
- **IB market-data lines:** each subscribed symbol consumes a data line
  (default ~100 lines/account); roster-scale is fine, universe-scale is not.
