# Feature request: multi-exchange universe + follow-the-sun trading

Status: **designed, not started** (parked 2026-08-02 as the "second
implementation"; prerequisite work — League/Explorer validation pipeline — is
done). Owner intent: trade the best validated setups around the clock using
IB access to ASX, Xetra and other high-volume exchanges, so a trading day
follows the sun: ASX during the Melbourne day → Europe in the evening → US
overnight, with crypto filling the gaps.

## 1. Goal

Extend the whole pipeline (data lake → backtests → League/Explorer →
walk-forward → Journal → scanner/paper trading) beyond US stocks + Binance
crypto to the biggest companies of the world's high-volume exchanges,
starting with ASX (Australia) and Xetra (Germany), then optionally LSE,
TSE (Japan), HKEX.

The League explicitly showed the current edge (FFFD 1h→30min midband,
holdout PF 1.83, breadth 195/250) is a *stocks* edge — this feature tests
whether it generalizes across exchanges, which would be strong evidence it
is structural rather than a US artifact.

## 2. Why not free data

- Alpaca (current stock source) is US-only.
- Yahoo/yfinance give at most ~30 days of 1-minute history — useless for
  the 13-month backtest window.
- Paid aggregators (Polygon, databento, dxFeed) cover some exchanges but
  cost more than IB's own data for this use case.

**Decision: IB Gateway / TWS API is the data source.** The user already has
market access on IB for these exchanges.

## 3. Data ingestion design (IB Gateway)

### Prerequisites (user actions)
- Enable market-data subscriptions in IB Account Management, per exchange
  (approx.: ASX ~A$25/mo, Xetra/Deutsche Börse ~€15/mo; check current
  prices). Delayed data is NOT good enough for the scanner but is fine to
  evaluate before paying (backfill works with any subscription level that
  serves historical bars).
- Run IB Gateway (paper login is fine for data) reachable from the compose
  network. Add a `ib-gateway` service to docker-compose (community images
  exist, e.g. `ghcr.io/gnzsnz/ib-gateway`, with IBC for auto-login) or run
  it on the Windows host and point the container at it.

### New ingestion scripts
- `scripts/download_ib_raw.py` — `ib_async` (successor of ib_insync)
  against Gateway:
  - `reqHistoricalData` 1-min TRADES bars, `useRTH=True`.
  - IB pacing: ~60 historical requests / 10 min, 1-min bars best fetched in
    1-day chunks → a 13-month backfill for one symbol ≈ 270 requests ≈ 45
    min of pacing budget; a 50-symbol universe ≈ multi-night job. Build it
    idempotent exactly like `download_binance_raw.py` (skip existing
    day-partitions, resume freely) and run it as a portal job with the
    `[league] step`-style progress lines.
  - Raw layout: `data/raw/ib/exchange=ASX/symbol=BHP/date=YYYY-MM-DD.parquet`.
- `scripts/build_bronze_ib.py` — normalize to the bronze/silver layout the
  engine already reads (UTC index, OHLCV columns), one silver dir per
  exchange or reuse `equity_1m_adjusted` with an exchange column — see §4
  registry.
- Nightly `daily_update.py` gains a step: pull yesterday's bars for all
  enrolled IB symbols (cheap — 1 request per symbol-day).

### Universe seeds
- ASX top ~50 by dollar volume (S&P/ASX 50: BHP, CBA, CSL, NAB, WBC, ANZ,
  WES, MQG, FMG, WDS, TLS, RIO, GMG, WOW, TCL, ALL, REA, COL, QBE, SUN, …)
- Xetra: DAX 40 constituents (SAP, SIE, ALV, DTE, AIR, MBG, BMW, BAS, BAYN,
  MUV2, …)
- Store per-exchange symbol lists in `data/meta/universe_<exchange>.json`
  (fetched once, editable), the way the S&P-250 list was seeded.

## 4. Engine generalization (the real work)

Everything below currently hardcodes US equities; each needs to read from a
new **exchange registry** instead.

### 4.1 Exchange registry — new `src/specula/exchanges.py`
```python
EXCHANGES = {
  "US":   {"tz": "America/New_York", "open": "09:30", "close": "16:00",
            "currency": "USD", "calendar": "XNYS",
            "fee_pct": 0.035, "entry_cutoff_min": 15},   # min before close
  "ASX":  {"tz": "Australia/Sydney", "open": "10:00", "close": "16:00",
            "currency": "AUD", "calendar": "XASX",
            "fee_pct": 0.08, "fee_min": 5.0, "entry_cutoff_min": 15},
  "XETRA":{"tz": "Europe/Berlin", "open": "09:00", "close": "17:30",
            "currency": "EUR", "calendar": "XETR",
            "fee_pct": 0.05, "fee_min": 1.25, "entry_cutoff_min": 15},
  "CRYPTO":{"tz": "UTC", "open": None, "close": None, "currency": "USDT",
            "fee_pct": 0.10},
}
def exchange_of(symbol) -> str     # from a symbol→exchange map built at ingest
def info(symbol) -> dict
```
Symbol→exchange map: persisted at ingest time (e.g.
`data/meta/symbol_exchange.json`); `BHP.AX`-style suffixes are an
alternative but plain symbols + map keeps labels clean. Collisions (e.g.
`RIO` on both ASX and NYSE) argue for suffixed internal symbols
(`RIO.AX`) — recommend suffixes for non-US symbols only.

### 4.2 Call sites to generalize
- `data.py::resample_equity` — hardcodes `America/New_York` +
  `offset="9h30min"`. Change to `tz`/session-open offset from the registry.
- `data.py::is_equity` / `equity_symbols` — becomes "is exchange-traded"
  plus `exchange_of`.
- `backtest.py::eod_masks` — hardcodes NY + 15:45 cutoff. Parametrize by
  exchange (close time − entry_cutoff_min; flat at last bar of session).
- `backtest.py::trade_size_for` + `settings.py` — per-class sizes today;
  add per-currency sizing: keep `trade_size_stock_usd` as the reference and
  convert with a coarse static FX table (good enough for backtests), or add
  `trade_size_aud`/`trade_size_eur` settings. Fees: per-exchange from the
  registry instead of the single `fee_stock_pct` (IB tiered: ASX 0.08%
  min A$5 — note the A$5 minimum means $1000-sized trades pay 0.5%+ on
  ASX unless size is raised to ~A$6k+; **this materially changes viable
  trade size and must be modeled before trusting any ASX backtest**).
- `signals.py::_day_key` — NY/UTC branch → registry tz.
- `features.py` session-anchored bits (VWAP day key) — same.
- `sweeps.py` fee scenarios — derive from registry per symbol.
- `league.py::universe()` + `evaluate()` — class split becomes per-exchange
  split (tabs: All / US / ASX / XETRA / Crypto), `MIN_ASSETS` per exchange.
- `runlog`/portal — nothing structural; labels/timezones only. Overview
  trigger log "market time" column reads registry tz (already parametrized
  as `marketTz` in the UI — extend the crypto/NY branch).
- Trading calendars: `pandas-market-calendars` already a dependency; use
  XASX/XETR for holidays in quality checks.

### 4.3 Scanner / follow-the-sun (last phase)
- `scanner.py::stock_bars` is Alpaca-only → add an IB live/recent-bars
  source (`reqHistoricalData` keepUpToDate or 60s polling of the last day,
  same as crypto path).
- Session gating: scan a symbol only while its exchange is open (registry
  calendar). The result IS follow-the-sun: ASX 10:00–16:00 Syd ≈ Melbourne
  day; Xetra 17:00–01:30 Mel; US 23:30–06:00 Mel; crypto always.
- Paper fills: currency-aware P&L (convert to USD at fill-time rate or a
  daily FX close; state which in the UI).
- Telegram alerts unchanged (already timezone-aware, Melbourne-first).

## 5. Phasing (recommended)

1. **ASX pilot (one session):** IB Gateway service + `download_ib_raw` +
   bronze/silver for ASX top-50 + exchange registry + generalize
   resample/eod/fees; run the League on the ASX class tab. Decision gate:
   does any approved setup hold OOS on ASX at IB's real A$-minimum fees?
2. **Xetra (fast follow):** same rails, DAX 40. Add per-exchange League
   tabs + Journal exchange filter.
3. **Scanner follow-the-sun:** IB live bars + session gating + currency
   P&L; roster can then hold BHP.AX next to LLY.
4. **Optional:** LSE / TSE / HKEX — only after ASX/Xetra prove the edge
   travels; each adds subscription cost and backfill nights.

## 6. Risks / honesty notes

- **Fee minimums dominate:** at $1000/trade the current US assumption
  (0.035%/side) is real, but ASX's A$5 minimum ≈ 0.5%/side at that size —
  most intraday edges die there. Either raise per-trade size for ASX or
  expect the League to (correctly) reject it. Model minimums as
  `max(pct, min_fee/size)` in the fee pipeline.
- **Pacing/backfill:** first ASX backfill is multi-night; don't block the
  nightly update (run as a yielding job like the explorer).
- **Survivorship:** seed universes from *current* index constituents — same
  caveat as the S&P-250 list; fine for setup discovery, noted for honesty.
- **Currency:** backtest P&L in local currency converted at a static rate
  is an approximation; flag it in the UI until live FX is wired.
- **Data licensing:** IB market data is for personal use; fine here.

## 7. Pointers for whoever picks this up

- Current universe/ingest reference implementations:
  `scripts/download_alpaca_raw.py`, `scripts/download_binance_raw.py`,
  `scripts/build_bronze_alpaca.py`, `scripts/ingest_crypto_top20.py`.
- Session-alignment reference: `data.py::resample_equity`,
  `backtest.py::eod_masks` (the two NY hardcodes).
- Validation pipeline the new exchanges plug into: `specula/league.py`
  (evaluate/merge_store), `scripts/league_explorer.py`, League page.
- The scanner phase should wait for lab-strategy scanner support (also
  pending) so approved League setups can be monitored at all.
