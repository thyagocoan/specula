"""Load bars from the local Parquet lake and resample locally (never re-fetch)."""

from pathlib import Path

import pandas as pd
import polars as pl

DATA_ROOT = Path("data")

EQUITY_SYMBOLS = {"NVDA", "AAPL", "GOOGL", "MSFT", "AMZN", "AVGO", "META", "TSLA",
                  "BRK.B", "LLY"}

_equity_cache: set[str] | None = None


def equity_symbols() -> set[str]:
    """All equity symbols present in the lake (silver layer), plus the core set."""
    global _equity_cache
    if _equity_cache is None:
        base = DATA_ROOT / "silver" / "equity_1m_adjusted"
        found = (
            {p.name.split("=", 1)[1] for p in base.glob("symbol=*")}
            if base.exists() else set()
        )
        _equity_cache = found | EQUITY_SYMBOLS
    return _equity_cache


def is_equity(symbol: str) -> bool:
    return symbol in equity_symbols()


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


def resample_equity(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Session-aligned resampling for US equities: buckets anchored to the
    09:30 ET open (so a 2h bar covers 09:30-11:30 ET, not arbitrary UTC
    boundaries). Index stays UTC."""
    ny = df.tz_convert("America/New_York")
    out = ny.resample(rule, offset="9h30min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])
    return out.tz_convert("UTC")
