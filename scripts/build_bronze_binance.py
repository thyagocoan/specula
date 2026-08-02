"""Parse raw Binance kline zips into the bronze Parquet layer.

Reads every data/raw/binance/spot/{SYMBOL}/{interval}/*.zip, handles the two
archive quirks (optional header row; ms vs us epoch timestamps), dedupes on
(symbol, ts), and writes zstd Parquet partitioned by symbol/year:

    data/bronze/crypto/exchange=binance/market=spot/symbol={SYM}/year={YYYY}/part-0.parquet

Rebuilds affected partitions from raw on every run (raw is the source of truth).

Usage:
    uv run python scripts/build_bronze_binance.py --symbol BTCUSDT
"""

import argparse
import io
import sys
import zipfile
from pathlib import Path

import polars as pl

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]


def parse_zip(path: Path) -> pl.DataFrame:
    with zipfile.ZipFile(path) as zf:
        name = zf.namelist()[0]
        raw = zf.read(name)
    df = pl.read_csv(
        io.BytesIO(raw),
        has_header=False,
        new_columns=KLINE_COLUMNS,
        infer_schema_length=0,  # read everything as utf8; cast explicitly below
    )
    # newer archives may carry a header row — drop it if present
    if df.height and not df["open_time"][0].isdigit():
        df = df.slice(1)
    df = df.with_columns(pl.col("open_time").cast(pl.Int64))
    # sniff epoch unit: ms = 13 digits, us = 16 digits
    unit_divisor = pl.when(pl.col("open_time") > 10**15).then(1000).otherwise(1)
    return df.select(
        ts=pl.from_epoch(pl.col("open_time") // unit_divisor, time_unit="ms").dt.replace_time_zone("UTC"),
        open=pl.col("open").cast(pl.Float64),
        high=pl.col("high").cast(pl.Float64),
        low=pl.col("low").cast(pl.Float64),
        close=pl.col("close").cast(pl.Float64),
        volume=pl.col("volume").cast(pl.Float64),
        trades=pl.col("trades").cast(pl.Int64),
        quote_volume=pl.col("quote_volume").cast(pl.Float64),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1m")
    ap.add_argument("--data-root", default="data")
    args = ap.parse_args()

    raw_dir = Path(args.data_root) / "raw" / "binance" / "spot" / args.symbol / args.interval
    zips = sorted(raw_dir.glob("*.zip"))
    if not zips:
        print(f"no raw zips found under {raw_dir}", file=sys.stderr)
        return 1

    frames = [parse_zip(p) for p in zips]
    df = (
        pl.concat(frames)
        .unique(subset="ts", keep="first")
        .sort("ts")
        .with_columns(
            symbol=pl.lit(args.symbol),
            vwap=pl.col("quote_volume") / pl.col("volume"),
            source=pl.lit("binance_spot"),
        )
        .select("ts", "symbol", "open", "high", "low", "close", "volume", "trades", "vwap", "source")
    )

    out_base = (
        Path(args.data_root) / "bronze" / "crypto" / "exchange=binance" / "market=spot"
        / f"symbol={args.symbol}"
    )
    for (year,), part in df.group_by(df["ts"].dt.year().alias("year"), maintain_order=True):
        out_dir = out_base / f"year={year}"
        out_dir.mkdir(parents=True, exist_ok=True)
        part.write_parquet(out_dir / "part-0.parquet", compression="zstd")
        print(f"[done] {args.symbol} {year}: {part.height} rows "
              f"({part['ts'].min()} .. {part['ts'].max()})", flush=True)

    print(f"\ntotal: {df.height} rows from {len(zips)} zips", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
