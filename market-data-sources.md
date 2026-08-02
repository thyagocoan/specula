# Free Minute-Bar Market Data — Ingestion Spec (S&P 500 + Crypto, 8-Year Window)

> Context document for a Claude Code session. Goal: build a reproducible local data lake of
> **1-minute OHLCV bars** for US equities (S&P 500 universe) and crypto, covering
> **2018-08-01 → present (2026-08)**, using only free sources.

---

## 0. Design principles (read first)

1. **Download the lowest granularity once (1m), resample everything else locally.** Never fetch 5m/15m/1h/1d separately — it wastes API budget and creates inconsistent bar boundaries.
2. **Raw → Bronze → Silver.** Keep the untouched vendor payload (zips/JSON) on disk. Never re-download to fix a parsing bug.
3. **Parquet + DuckDB, not CSV.** ~500 symbols × 1m × 8y ≈ 350–400M rows. CSV is unusable at this size.
4. **Idempotent, resumable.** Every downloader must skip already-present partitions and survive being killed mid-run.
5. **Point-in-time correctness.** Survivorship bias and unadjusted splits are the two things that will silently make a backtest look profitable when it isn't.

---

## 1. Coverage window

| | Start | End | Notes |
|---|---|---|---|
| Target window | 2018-08-01 | rolling (T-1) | 8 years |
| Alpaca equities availability | 2016-01-01 | T minus 15 min | fully covers target |
| Binance spot availability | 2017-08 (BTCUSDT) | T-1 day | fully covers target |
| Binance USDⓈ-M futures | 2019-09 | T-1 day | partial — starts inside window |

**Hard constraint:** there is **no free source for US equity 1-minute bars before ~2016.** If the project ever needs 2000–2016 intraday, that is a paid dataset (FirstRate Data / Pi Trading / Databento). Do not waste time hunting for it.

---

## 2. Crypto sources

### 2.1 Binance public data dumps — PRIMARY

Static file archive, **no API key, no rate limit, no account required.** This is the best free crypto dataset available.

**Base URL:** `https://data.binance.vision`

**URL patterns:**

```
# Spot, monthly klines (preferred — fewer requests)
https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY}-{MM}.zip

# Spot, daily klines (use for the current, incomplete month)
https://data.binance.vision/data/spot/daily/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY}-{MM}-{DD}.zip

# USDⓈ-M perpetual futures
https://data.binance.vision/data/futures/um/monthly/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY}-{MM}.zip

# COIN-M futures
https://data.binance.vision/data/futures/cm/monthly/klines/{SYMBOL}/{INTERVAL}/...

# Raw trades / aggregated trades (for tick-level or realistic fill modelling)
https://data.binance.vision/data/spot/monthly/trades/{SYMBOL}/{SYMBOL}-trades-{YYYY}-{MM}.zip
https://data.binance.vision/data/spot/monthly/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-{YYYY}-{MM}.zip

# Integrity: every file has a sibling checksum
{same_url}.CHECKSUM     # verify with: sha256sum -c FILE.zip.CHECKSUM
```

**Intervals available:** `1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1mo`
(`1mo` is used instead of `1M` for case-insensitive filesystems.) → **fetch `1m` only.**

**Kline CSV columns (no header row in the file):**

```
open_time_ms, open, high, low, close, volume, close_time_ms,
quote_asset_volume, num_trades, taker_buy_base_volume, taker_buy_quote_volume, ignore
```

> ⚠️ Newer archive files may include a header row. Detect and skip it rather than assuming.
> ⚠️ Timestamps are **UTC epoch milliseconds**. Some recent files use microseconds — sniff the digit count.

**Symbol list:** official helper repo `binance/binance-public-data` includes `shell/fetch-all-trading-pairs.sh`.
Suggested starting universe: `BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT, MATICUSDT`.

**Convenience wrappers (optional — direct `curl` in parallel is faster):**
```bash
pip install binance_historical_data   # BinanceDataDumper, dumps full history in ~3 lines
pip install binance-history           # bh.fetch_klines(symbol, timeframe, start, end) -> DataFrame
```

**Delay:** previous-day data appears a few minutes after 00:00 UTC. Schedule daily pulls at ~00:30 UTC.

### 2.2 Secondary / cross-validation crypto sources

| Source | Granularity | History | Use for |
|---|---|---|---|
| **Kraken** CSV dumps | 1m OHLCVT | 2013+ | Pre-Binance era, USD (not USDT) pairs |
| **Bybit** `public.bybit.com` | 1m + tick | 2018+ | Perp funding-rate strategies, cross-exchange checks |
| **OKX** public data dumps | 1m + tick | 2018+ | Third opinion on wick/outlier disputes |
| **Alpaca crypto** (`/v1beta3/crypto`) | 1m | — | No auth required for historical crypto; already in the equities SDK |
| **CryptoDataDownload** | 1m/1h CSV | varies | Convenient but **gappy** — validate before use |
| **ccxt** | REST paging | shallow | Live/incremental only, not bulk backfill |

---

## 3. US equities sources

### 3.1 Alpaca Markets "Basic" plan — PRIMARY (free)

The only free source of **full-coverage US equity minute bars** with meaningful depth.

| Attribute | Free (Basic) tier |
|---|---|
| Cost | $0 (paper account is enough) |
| Coverage | All US stocks & ETFs |
| Feed for **historical** | SIP consolidated tape — CTA (NYSE) + UTP (Nasdaq) = **100% of market volume** |
| Historical depth | **since 2016** |
| Restriction | Only the **most recent 15 minutes** is withheld |
| Rate limit | **200 API calls / minute** |
| Real-time websocket | IEX only, 30 symbols (irrelevant for backtesting) |

> Key point often misunderstood: the free IEX limitation applies to the **real-time stream**, not to historical bars. Historical requests return full SIP data as long as you stay >15 min behind now.

**Endpoint:**
```
GET https://data.alpaca.markets/v2/stocks/bars
  ?symbols=AAPL,MSFT,...        # multi-symbol per request — batch aggressively
  &timeframe=1Min
  &start=2018-08-01T00:00:00Z
  &end=2026-08-01T00:00:00Z
  &limit=10000
  &adjustment=all               # split + dividend adjusted; also fetch raw separately
  &feed=sip
  &page_token=...               # paginate until next_page_token is null

Headers:
  APCA-API-KEY-ID:     $ALPACA_KEY_ID
  APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY
```

**Pagination gotcha (documented behaviour):** results are sorted **by symbol first, then timestamp**. A multi-symbol request may return only the first symbol until you exhaust `next_page_token`. Do not assume a single response contains all requested symbols.

**SDK:** `pip install alpaca-py`

```python
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
```

**Bar semantics:** minute bars are aggregated from trades; the bar timestamp is the **left edge** of the interval (a 14:52:28 trade lands in the 14:52:00 bar). Daily bars are truncated in **America/New_York**.

### 3.2 Long-history daily (for regime context / longer-horizon strategies)

| Source | Granularity | History | Notes |
|---|---|---|---|
| **Stooq bulk** `stooq.com/db/h/` | 1d | 30+ years | Whole-US-market ASCII zip (~333 MB). Free, no key. **Intraday on Stooq is useless: 5m capped at ~2000 bars (~1 month), 1h at ~1400 bars (~9 months).** |
| **yfinance** | 1d back to 1962 | long | Unofficial, breaks periodically. Intraday: 1m only last 30 days (7-day chunks), 1h ~730 days. Use for adjusted daily + corporate-action sanity checks. |
| **Tiingo** free tier | 1d | 30+ years | Clean split/dividend-adjusted EOD, requires free key |

### 3.3 Fallback / top-up equity sources

| Source | What's free | Use for |
|---|---|---|
| **Databento** | $125 signup credits | Deep, institutional-grade 1m/tick if you need pre-2016 or MBO data |
| **FirstRate Data** | 1 year of 1m for popular datasets; 2-week samples otherwise | Independent cross-check of Alpaca bars |
| **Kibot** | 3 months 1m for IBM/OIH; 1 month tick for IVE/WDC; free daily EOD all US | Tick-format reference |
| **EODHD** | free plan, 1m/5m/1h | Backup API; 1m claimed since 2004 but symbol-dependent |
| **Polygon.io** free | 2 years, 5 req/min | Too restrictive — skip |

---

## 4. Universe construction (survivorship bias)

**Do not use today's S&P 500 list for a 2018 backtest.** Roughly 25–30 constituents change per year; the losers get deleted and the list you'd be testing is a pre-selected set of winners.

Required:
- Point-in-time membership table: `symbol, start_date, end_date`
- Community source: GitHub `fja05680/sp500` (maintains historical constituent CSVs), cross-referenced against the Wikipedia "List of S&P 500 companies" *Selected changes* table.
- **Include delisted/removed tickers.** Alpaca serves historical bars for many delisted symbols; verify per-symbol and log misses.
- Handle ticker reuse and renames (e.g. FB→META, GOOG/GOOGL classes). Key on a stable internal `security_id`, not the ticker string.

Deliverable: `universe/sp500_membership.parquet` and a helper `constituents_as_of(date) -> list[str]`.

---

## 5. Storage layout

```
data/
├── raw/                                   # untouched vendor payloads, never mutated
│   ├── binance/spot/BTCUSDT/1m/BTCUSDT-1m-2019-03.zip
│   └── alpaca/stocks/1min/AAPL/2019.json.gz
├── bronze/                                # parsed, typed, deduped — 1:1 with raw
│   ├── crypto/exchange=binance/market=spot/symbol=BTCUSDT/year=2019/part-*.parquet
│   └── equity/symbol=AAPL/year=2019/part-*.parquet
├── silver/                                # adjusted, session-tagged, gap-flagged
│   └── equity_1m_adjusted/symbol=AAPL/year=2019/part-*.parquet
├── universe/
│   └── sp500_membership.parquet
└── meta/
    ├── ingest_log.sqlite                  # partition-level status for resumability
    └── quality_report.parquet
```

**Canonical bronze schema (both asset classes):**

| column | type | notes |
|---|---|---|
| `ts` | `TIMESTAMP` (UTC, tz-aware) | left edge of bar |
| `symbol` | `VARCHAR` | vendor symbol |
| `open/high/low/close` | `DOUBLE` | |
| `volume` | `DOUBLE` | base asset / shares |
| `trades` | `BIGINT` | null where unavailable |
| `vwap` | `DOUBLE` | Alpaca provides; compute for Binance |
| `source` | `VARCHAR` | `binance_spot`, `alpaca_sip`, ... |

Compression: `zstd`. Row group ~128 MB. Partition by `symbol` then `year` (avoid `month` — too many small files).

---

## 6. Resampling rules

Always aggregate up from 1m; never mix vendor-native higher timeframes with locally resampled ones.

```sql
-- DuckDB
SELECT
  time_bucket(INTERVAL '5 minutes', ts) AS ts,
  symbol,
  first(open  ORDER BY ts) AS open,
  max(high)               AS high,
  min(low)                AS low,
  last(close  ORDER BY ts) AS close,
  sum(volume)             AS volume,
  sum(volume * vwap) / nullif(sum(volume), 0) AS vwap
FROM bronze_1m
GROUP BY 1, 2;
```

- **Crypto:** 24/7, bucket in UTC, no session logic.
- **Equities:** bucket in `America/New_York`, tag each bar `regular | premarket | postmarket`. Regular session = 09:30–16:00 ET. Handle half-days (13:00 ET close) — use `pandas_market_calendars` (`XNYS`) rather than hardcoding.
- **Do not forward-fill missing minutes by default.** A missing minute means no trades; that is information. Provide fill as an explicit opt-in flag.

---

## 7. Corporate actions (equities only)

- Fetch **both** `adjustment=raw` and `adjustment=all` from Alpaca. Store raw in bronze, adjusted in silver.
- Rationale: adjusted history is retroactively rewritten on every new split/dividend, so an adjusted-only lake is not reproducible. Raw + an actions table is.
- Sanity check: any raw close-to-close move >40% with no corresponding split/dividend event → flag for manual review.
- Reference case: AAPL 4:1 split 2020-08-31; TSLA 5:1 2020-08-31 and 3:1 2022-08-25.
- Crypto has no corporate actions, but **does** have exchange-level pair delistings and rebrands (MATIC→POL). Maintain a symbol alias map.

---

## 8. Data quality checks (run after every ingest)

1. **Gap scan** — expected vs. actual bar count per symbol-day against the exchange calendar. Report `coverage_pct`.
2. **OHLC sanity** — `low <= min(open, close) <= max(open, close) <= high`; no negative or zero prices; no negative volume.
3. **Monotonic, unique** `(symbol, ts)`.
4. **Outlier wicks** — bar range > 20× the trailing 100-bar median range → flag, don't auto-delete.
5. **Zero-volume bars** with price movement → suspicious, flag.
6. **Cross-source spot check** — compare 20 random symbol-days against a second source (FirstRate free year for equities; Kraken/Bybit for crypto). Assert close prices agree within tolerance.
7. **Checksum verification** for every Binance zip.

Write results to `meta/quality_report.parquet` with a per-symbol grade.

---

## 9. Sizing estimates

| Dataset | Rows | Parquet (zstd) |
|---|---|---|
| S&P 500, 1m, regular session only, 8y | ~390 bars × 252 d × 8 y × 500 ≈ **390M** | ~12–18 GB |
| S&P 500, 1m, incl. pre/post (04:00–20:00 ET) | ~960M | ~30–40 GB |
| 10 crypto pairs, 1m, 8y | 1,440 × 365 × 8 × 10 ≈ **42M** | ~1.5–2 GB |

Full initial equity backfill at 200 req/min with batched multi-symbol requests: budget **6–12 hours**. Run it overnight with resume support; do not try to parallelise past the rate limit — you'll just get 429s.

---

## 10. Recommended toolchain

```
Storage/query : DuckDB + Parquet (pyarrow)
Dataframes    : polars (preferred at this scale) or pandas
HTTP          : httpx with async + semaphore-bounded concurrency
Calendars     : pandas_market_calendars
Retries       : tenacity (exponential backoff, honour Retry-After on 429)
Orchestration : plain CLI + SQLite ingest log (Prefect/Dagster is overkill here)
```

**Backtest engines:**

| Engine | Best for |
|---|---|
| `vectorbt` | Fast vectorised parameter sweeps over the whole universe |
| `backtesting.py` | Simple, readable event-driven single-asset prototypes |
| `nautilus_trader` | Realistic fills, latency, funding rates; Rust core; closest to live |

---

## 11. Environment

```bash
export ALPACA_KEY_ID="..."
export ALPACA_SECRET_KEY="..."
export ALPACA_DATA_URL="https://data.alpaca.markets"
export DATA_ROOT="./data"
# Binance archive needs no credentials
```

`.env` + `python-dotenv`. Never commit keys.

---

## 12. Build order (task list for the agent)

- [ ] **T1** Scaffold repo, config (`pydantic-settings`), `data/` tree, SQLite ingest log schema.
- [ ] **T2** Binance downloader: symbol × month enumeration, async fetch, checksum verify, skip-existing, unzip → bronze Parquet.
- [ ] **T3** Alpaca downloader: batched multi-symbol paging, rate-limit governor (200/min), `raw` + `all` adjustments, resume from ingest log.
- [ ] **T4** Point-in-time S&P 500 membership table + `constituents_as_of()` helper.
- [ ] **T5** Bronze → silver: timezone normalisation, session tagging, gap flags, adjusted series.
- [ ] **T6** DuckDB views + `resample(symbol, tf, start, end)` API.
- [ ] **T7** Quality-check suite (section 8) with a CLI report.
- [ ] **T8** Incremental daily updater (crypto 00:30 UTC; equities after 16:15 ET).
- [ ] **T9** Reference backtest: SMA crossover on BTCUSDT + a cross-sectional momentum sweep on the S&P 500, purely to validate the data path end to end.

**Acceptance criteria:** for any `(symbol, timeframe, start, end)` in the 8-year window, a single function call returns a gap-flagged, adjustment-correct DataFrame in under a second from the local lake, and the entire lake can be rebuilt from `raw/` with no network access.

---

## 13. Known traps summary

| Trap | Consequence | Mitigation |
|---|---|---|
| Today's S&P 500 list used for 2018 | Massively inflated returns | Point-in-time membership + delisted tickers |
| Adjusted-only storage | Non-reproducible backtests | Store raw + actions table |
| Assuming Alpaca free = IEX only | Unnecessarily discarding the best free source | IEX limit applies to the real-time stream, not historical |
| Multi-symbol Alpaca response read without paging | Silently missing symbols | Loop until `next_page_token` is null |
| Binance ms vs µs timestamps | Bars 1000× in the wrong era | Sniff digit count per file |
| Forward-filling absent minutes | Fake liquidity, unfillable backtest trades | Keep gaps; opt-in fill only |
| Ignoring half-days / holidays | Phantom gap alerts, wrong daily bars | `pandas_market_calendars` XNYS |
| Backtesting crypto on aggregated cross-exchange prices | Untradeable fills | Backtest on the venue you'd actually trade |
