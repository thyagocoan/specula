"""Append-only registry of every backtest ever run — SQLite-backed.

Each executed config is one row in data/meta/registry.sqlite (table `runs`):
run_id, timestamp, git commit, sweep tag, full params as JSON (enough to
reproduce the run via backtest.build_portfolio), and the result metrics.
Appends are transactional (safe under concurrent jobs); reads are indexed.

The pre-migration Parquet registry (backtest_runs.parquet) is imported once
into an empty database and then kept as a frozen archive.
"""

import json
import math
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DB = Path("data/meta/registry.sqlite")
LEGACY_PARQUET = Path("data/meta/backtest_runs.parquet")
WEB_DATA = Path("web/public/data/runs.json")
REPORTS = Path("reports")

CFG_COLS = ["symbol", "strategy", "setup_tf", "exec_tf"]

COLUMNS = [
    "run_id", "created_at", "git_sha", "sweep_tag", "symbol", "strategy",
    "setup_tf", "exec_tf", "params", "n_trades", "total_return_pct",
    "max_dd_pct", "win_rate_pct", "profit_factor", "avg_trade_pct", "sharpe",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT,
    git_sha TEXT,
    sweep_tag TEXT,
    symbol TEXT,
    strategy TEXT,
    setup_tf TEXT,
    exec_tf TEXT,
    params TEXT,
    n_trades INTEGER,
    total_return_pct REAL,
    max_dd_pct REAL,
    win_rate_pct REAL,
    profit_factor REAL,
    avg_trade_pct REAL,
    sharpe REAL
)
"""


def _finite(v):
    """Non-finite floats become None so params stay strict-JSON-safe."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _to_sql(v):
    """Coerce numpy scalars / NaN / inf to SQLite-friendly Python values."""
    import numpy as np

    if v is None:
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        v = float(v)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if not isinstance(v, (str, int, float, bytes)) and pd.isna(v):
        return None
    return v


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(_SCHEMA)
    con.execute("""
        CREATE TABLE IF NOT EXISTS autotrade (
            symbol TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            cfg TEXT,
            size_usd REAL DEFAULT 1000,
            added_at TEXT
        )
    """)
    for col in ("symbol", "sweep_tag", "profit_factor"):
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_runs_{col} ON runs({col})")
    _migrate_legacy(con)
    return con


# ------------------------------------------------------------ autotrade roster

def autotrade_list() -> list[dict]:
    con = _connect()
    try:
        rows = con.execute(
            "SELECT symbol, enabled, cfg, size_usd, added_at FROM autotrade "
            "ORDER BY symbol").fetchall()
    finally:
        con.close()
    return [{"symbol": r[0], "enabled": bool(r[1]),
             "cfg": json.loads(r[2]) if r[2] else None,
             "size_usd": r[3], "added_at": r[4]} for r in rows]


def autotrade_symbols() -> list[str]:
    return [r["symbol"] for r in autotrade_list() if r["enabled"]]


def autotrade_set(symbol: str, enabled: bool = True, cfg: dict | None = None,
                  size_usd: float | None = None) -> None:
    con = _connect()
    try:
        con.execute(
            "INSERT INTO autotrade (symbol, enabled, cfg, size_usd, added_at) "
            "VALUES (?, ?, ?, COALESCE(?, 1000), ?) "
            "ON CONFLICT(symbol) DO UPDATE SET enabled=excluded.enabled, "
            "cfg=COALESCE(excluded.cfg, autotrade.cfg), "
            "size_usd=COALESCE(?, autotrade.size_usd)",
            (symbol.upper(), int(enabled),
             json.dumps(cfg, sort_keys=True) if cfg else None, size_usd,
             datetime.now(timezone.utc).isoformat(timespec="seconds"), size_usd),
        )
        con.commit()
    finally:
        con.close()


def _migrate_legacy(con: sqlite3.Connection) -> None:
    if con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] > 0:
        return
    if not LEGACY_PARQUET.exists():
        return
    df = pd.read_parquet(LEGACY_PARQUET)
    rows = [
        tuple(_to_sql(r.get(c)) for c in COLUMNS)
        for r in df.to_dict("records")
    ]
    con.executemany(
        f"INSERT OR REPLACE INTO runs ({','.join(COLUMNS)}) "
        f"VALUES ({','.join('?' * len(COLUMNS))})", rows,
    )
    con.commit()
    print(f"[runlog] migrated {len(rows)} legacy rows into {DB}", flush=True)


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def make_row(cfg: dict, metrics: dict, sweep_tag: str, sha: str) -> dict:
    row = {
        "run_id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": sha,
        "sweep_tag": sweep_tag,
        **{k: cfg.get(k) for k in CFG_COLS},
        "params": json.dumps({k: _finite(v) for k, v in cfg.items()}, sort_keys=True),
    }
    row.update(metrics)
    return row


def append(rows: list[dict]) -> pd.DataFrame:
    con = _connect()
    try:
        data = [tuple(_to_sql(r.get(c)) for c in COLUMNS) for r in rows]
        con.executemany(
            f"INSERT OR REPLACE INTO runs ({','.join(COLUMNS)}) "
            f"VALUES ({','.join('?' * len(COLUMNS))})", data,
        )
        con.commit()
        df = pd.read_sql_query("SELECT * FROM runs ORDER BY rowid", con)
    finally:
        con.close()
    export_web(df)
    return df


def load() -> pd.DataFrame:
    con = _connect()
    try:
        return pd.read_sql_query("SELECT * FROM runs ORDER BY rowid", con)
    finally:
        con.close()


def get_cfg(run_id: str) -> dict:
    con = _connect()
    try:
        row = con.execute(
            "SELECT params FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"run_id {run_id} not found in {DB}")
    return json.loads(row[0])


def records(df: pd.DataFrame) -> list[dict]:
    """Registry rows as JSON-safe dicts (params parsed, inf/NaN -> null)."""
    import numpy as np

    recs = []
    for _, r in df.iterrows():
        rec = {}
        for k, v in r.items():
            if k == "params":
                continue
            if isinstance(v, np.integer):
                v = int(v)
            elif isinstance(v, (np.floating, float)):
                v = float(v)
                if not math.isfinite(v):
                    v = None
            elif v is not None and pd.isna(v):
                v = None
            rec[k] = v
        rec["params"] = {k: _finite(v) for k, v in json.loads(r["params"]).items()}
        rec["report"] = (REPORTS / f"{r['run_id']}.html").exists()
        recs.append(rec)
    return recs


def payload(df: pd.DataFrame) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(df),
        "runs": records(df),
    }


def export_web(df: pd.DataFrame) -> None:
    """Mirror the registry into the web app's data file (skipped if web/ absent)."""
    if not WEB_DATA.parent.parent.exists():
        return
    WEB_DATA.parent.mkdir(parents=True, exist_ok=True)
    WEB_DATA.write_text(json.dumps(payload(df)), encoding="utf-8")
