"""Data-quality report over the bronze/silver lake.

Checks per symbol:
  - regular-session minute coverage vs the XNYS calendar (equities) or 24/7 (crypto)
  - OHLC sanity: low <= min(open,close) <= max(open,close) <= high, prices > 0, volume >= 0
  - duplicate timestamps
  - max |adjusted/raw - 1| close ratio (equities; >0 means dividends/splits in window)

Writes data/meta/quality_report.parquet and prints a table.

Usage:
    uv run python scripts/quality_report.py
"""

import sys
from datetime import timedelta
from pathlib import Path

import pandas_market_calendars as mcal
import polars as pl

DATA_ROOT = Path("data")


def ohlc_violations(df: pl.DataFrame) -> int:
    return df.filter(
        (pl.col("low") > pl.min_horizontal("open", "close"))
        | (pl.col("high") < pl.max_horizontal("open", "close"))
        | (pl.col("low") <= 0)
        | (pl.col("volume") < 0)
    ).height


def check_equity(symbol: str, expected_regular: int) -> dict:
    silver = pl.read_parquet(
        DATA_ROOT / "silver" / "equity_1m_adjusted" / f"symbol={symbol}" / "**" / "*.parquet"
    )
    bronze = pl.read_parquet(
        DATA_ROOT / "bronze" / "equity" / f"symbol={symbol}" / "**" / "*.parquet"
    )
    regular = silver.filter(pl.col("session") == "regular").height
    ratio = (
        silver.join(
            bronze.select("ts", raw_close=pl.col("close")), on="ts", how="inner"
        )
        .select(((pl.col("close") / pl.col("raw_close")) - 1).abs().max())
        .item()
    )
    return {
        "symbol": symbol,
        "rows": silver.height,
        "coverage_pct": round(100 * regular / expected_regular, 2),
        "ohlc_violations": ohlc_violations(silver),
        "dup_ts": silver.height - silver["ts"].n_unique(),
        "max_adj_ratio_pct": round(100 * (ratio or 0), 3),
    }


def main() -> int:
    reports = []

    # crypto: 24/7 expectation
    btc = pl.read_parquet(DATA_ROOT / "bronze" / "crypto" / "**" / "*.parquet")
    span_min = int((btc["ts"].max() - btc["ts"].min() + timedelta(minutes=1)).total_seconds() // 60)
    reports.append(
        {
            "symbol": "BTCUSDT",
            "rows": btc.height,
            "coverage_pct": round(100 * btc.height / span_min, 2),
            "ohlc_violations": ohlc_violations(btc),
            "dup_ts": btc.height - btc["ts"].n_unique(),
            "max_adj_ratio_pct": 0.0,
        }
    )

    # equities: expected regular minutes from the XNYS calendar over the actual window
    symbols = sorted(
        p.name.split("=", 1)[1]
        for p in (DATA_ROOT / "silver" / "equity_1m_adjusted").glob("symbol=*")
    )
    any_sym = pl.read_parquet(
        DATA_ROOT / "silver" / "equity_1m_adjusted" / f"symbol={symbols[0]}" / "**" / "*.parquet"
    )
    lo, hi = any_sym["ts"].min(), any_sym["ts"].max()
    sched = mcal.get_calendar("XNYS").schedule(
        start_date=lo.strftime("%Y-%m-%d"), end_date=hi.strftime("%Y-%m-%d")
    )
    expected_regular = int(
        ((sched["market_close"] - sched["market_open"]).dt.total_seconds() // 60).sum()
    )

    for symbol in symbols:
        reports.append(check_equity(symbol, expected_regular))

    out = pl.DataFrame(reports)
    meta_dir = DATA_ROOT / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    out.write_parquet(meta_dir / "quality_report.parquet", compression="zstd")
    with pl.Config(tbl_rows=-1):
        print(out)
    print(f"\nexpected regular-session minutes per equity symbol: {expected_regular}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
