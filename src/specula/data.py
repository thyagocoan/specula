"""Load bars from the local Parquet lake and resample locally (never re-fetch)."""

from pathlib import Path

import pandas as pd
import polars as pl

DATA_ROOT = Path("data")


def load_crypto_1m(symbol: str = "BTCUSDT", data_root: Path = DATA_ROOT) -> pd.DataFrame:
    """Bronze 1m crypto bars as a UTC-indexed pandas OHLCV frame."""
    df = pl.read_parquet(
        data_root / "bronze" / "crypto" / "exchange=binance" / "market=spot"
        / f"symbol={symbol}" / "**" / "*.parquet"
    )
    return (
        df.sort("ts")
        .select("ts", "open", "high", "low", "close", "volume")
        .to_pandas()
        .set_index("ts")
    )


def load_equity_1m(symbol: str, session: str | None = "regular",
                   data_root: Path = DATA_ROOT) -> pd.DataFrame:
    """Silver (adjusted) 1m equity bars; optionally filtered to one session tag."""
    df = pl.read_parquet(
        data_root / "silver" / "equity_1m_adjusted" / f"symbol={symbol}" / "**" / "*.parquet"
    )
    if session is not None:
        df = df.filter(pl.col("session") == session)
    return (
        df.sort("ts")
        .select("ts", "open", "high", "low", "close", "volume")
        .to_pandas()
        .set_index("ts")
    )


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate 1m bars up to `rule` (e.g. '5min'); bars with no trades are dropped."""
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])
