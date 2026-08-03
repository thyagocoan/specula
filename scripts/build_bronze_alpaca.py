"""Parse raw Alpaca JSON payloads into bronze (raw prices) and silver (adjusted,
session-tagged) Parquet layers.

Bronze  <- adjustment=raw   : data/bronze/equity/symbol={SYM}/year={YYYY}/part-0.parquet
Silver  <- adjustment=all   : data/silver/equity_1m_adjusted/symbol={SYM}/year={YYYY}/part-0.parquet

Silver bars are tagged `session` in {regular, premarket, postmarket} using the
XNYS calendar (handles half-days). Rebuilds from raw on every run.

Usage:
    uv run python scripts/build_bronze_alpaca.py
"""

import gzip
import json
import sys
from pathlib import Path

import pandas_market_calendars as mcal
import polars as pl

DATA_ROOT = Path("data")
RAW_BASE = DATA_ROOT / "raw" / "alpaca" / "stocks" / "1min"


def load_symbol(adjustment: str, symbol: str) -> pl.DataFrame:
    files = sorted((RAW_BASE / f"adjustment={adjustment}" / f"symbol={symbol}").glob("*.json.gz"))
    rows = []
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            rows.extend(json.load(f)["bars"])
    if not rows:
        return pl.DataFrame()
    return (
        pl.from_dicts(rows)
        .select(
            ts=pl.col("t").str.to_datetime(time_zone="UTC"),
            open=pl.col("o").cast(pl.Float64),
            high=pl.col("h").cast(pl.Float64),
            low=pl.col("l").cast(pl.Float64),
            close=pl.col("c").cast(pl.Float64),
            volume=pl.col("v").cast(pl.Float64),
            trades=pl.col("n").cast(pl.Int64),
            vwap=pl.col("vw").cast(pl.Float64),
        )
        .unique(subset="ts", keep="first")
        .sort("ts")
        .with_columns(symbol=pl.lit(symbol), source=pl.lit("alpaca_sip"))
        .select("ts", "symbol", "open", "high", "low", "close", "volume", "trades", "vwap", "source")
    )


def session_table(start: str, end: str) -> pl.DataFrame:
    """XNYS regular-session open/close per trading day (UTC), half-days included."""
    sched = mcal.get_calendar("XNYS").schedule(start_date=start, end_date=end)
    tbl = sched.reset_index(names="day")
    tbl["market_open"] = tbl["market_open"].dt.tz_convert("UTC")
    tbl["market_close"] = tbl["market_close"].dt.tz_convert("UTC")
    return pl.from_pandas(tbl).select(
        day=pl.col("day").cast(pl.Date),
        open_utc=pl.col("market_open"),
        close_utc=pl.col("market_close"),
    )


def tag_sessions(df: pl.DataFrame, sessions: pl.DataFrame) -> pl.DataFrame:
    df = df.with_columns(day=pl.col("ts").dt.convert_time_zone("America/New_York").dt.date())
    df = df.join(sessions, on="day", how="left")
    return df.with_columns(
        session=pl.when(pl.col("open_utc").is_null())
        .then(pl.lit("closed"))
        .when(pl.col("ts") < pl.col("open_utc"))
        .then(pl.lit("premarket"))
        .when(pl.col("ts") >= pl.col("close_utc"))
        .then(pl.lit("postmarket"))
        .otherwise(pl.lit("regular"))
    ).drop("day", "open_utc", "close_utc")


def write_partitioned(df: pl.DataFrame, base: Path) -> list[str]:
    out = []
    for (year,), part in df.group_by(df["ts"].dt.year().alias("year"), maintain_order=True):
        out_dir = base / f"year={year}"
        out_dir.mkdir(parents=True, exist_ok=True)
        part.write_parquet(out_dir / "part-0.parquet", compression="zstd")
        out.append(f"{year}: {part.height} rows")
    return out


def main() -> int:
    symbols = sorted(
        p.name.split("=", 1)[1] for p in (RAW_BASE / "adjustment=raw").glob("symbol=*")
    )
    if not symbols:
        print("no raw alpaca data found", file=sys.stderr)
        return 1

    sessions = None
    for symbol in symbols:
        bronze = load_symbol("raw", symbol)
        silver = load_symbol("all", symbol)
        if bronze.is_empty():
            print(f"[warn] {symbol}: no bars", flush=True)
            continue
        if sessions is None:
            # span from the SILVER frame: backfills download adjusted-only,
            # so bronze/raw may cover a much shorter window than silver —
            # a bronze-derived calendar mis-tags all older bars as "closed"
            lo = min(bronze["ts"].min(), silver["ts"].min()).strftime("%Y-%m-%d")
            hi = max(bronze["ts"].max(), silver["ts"].max()).strftime("%Y-%m-%d")
            sessions = session_table(lo, hi)
        silver = tag_sessions(silver, sessions)
        b = write_partitioned(bronze, DATA_ROOT / "bronze" / "equity" / f"symbol={symbol}")
        s = write_partitioned(
            silver, DATA_ROOT / "silver" / "equity_1m_adjusted" / f"symbol={symbol}"
        )
        print(f"[done] {symbol} bronze[{'; '.join(b)}] silver[{'; '.join(s)}]", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
