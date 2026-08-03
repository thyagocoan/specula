"""Paper-trading ledger and risk guards (SQLite, alongside the registry).

Everything is simulated fills at trigger/stop prices ± slippage — clearly
paper. Architecture keeps an adapter seam for routing stock orders to the
Alpaca paper API in a later iteration.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from specula.settings import get_settings

DB = Path("data/meta/registry.sqlite")
SLIPPAGE = 0.0005

MAX_OPEN = int(os.environ.get("SPECULA_MAX_OPEN_POSITIONS", "5"))
DAILY_LOSS_CAP_USD = float(os.environ.get("SPECULA_DAILY_LOSS_CAP_USD", "300"))


def _fee_pct(symbol: str) -> float:
    s = get_settings()
    is_crypto = symbol.endswith(("USDT", "USDC"))
    return (s["fee_crypto_pct"] if is_crypto else s["fee_stock_pct"]) / 100

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_ts TEXT NOT NULL,
    sl REAL,
    tp REAL,
    status TEXT NOT NULL DEFAULT 'open',
    exit_price REAL,
    exit_ts TEXT,
    exit_reason TEXT,
    pnl_usd REAL,
    pnl_pct REAL,
    cfg TEXT
)
"""


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(_SCHEMA)
    return con


def open_positions(symbol: str | None = None) -> list[dict]:
    con = _connect()
    try:
        q = ("SELECT id, symbol, side, qty, entry_price, entry_ts, sl, tp, cfg "
             "FROM paper_positions WHERE status='open'")
        args = ()
        if symbol:
            q += " AND symbol=?"
            args = (symbol,)
        rows = con.execute(q, args).fetchall()
    finally:
        con.close()
    return [{"id": r[0], "symbol": r[1], "side": r[2], "qty": r[3],
             "entry_price": r[4], "entry_ts": r[5], "sl": r[6], "tp": r[7],
             "cfg": json.loads(r[8]) if r[8] else None} for r in rows]


def open_position(symbol: str, side: str, price: float, size_usd: float,
                  sl: float | None, tp: float | None, cfg: dict) -> dict:
    fill = price * (1 + SLIPPAGE) if side == "long" else price * (1 - SLIPPAGE)
    qty = size_usd / fill
    con = _connect()
    try:
        cur = con.execute(
            "INSERT INTO paper_positions (symbol, side, qty, entry_price, "
            "entry_ts, sl, tp, cfg) VALUES (?,?,?,?,?,?,?,?)",
            (symbol, side, qty, fill,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             sl, tp, json.dumps(cfg, sort_keys=True)),
        )
        con.commit()
        pid = cur.lastrowid
    finally:
        con.close()
    return {"id": pid, "symbol": symbol, "side": side, "qty": qty,
            "entry_price": fill, "sl": sl, "tp": tp}


def close_position(pid: int, price: float, reason: str) -> dict | None:
    con = _connect()
    try:
        row = con.execute(
            "SELECT symbol, side, qty, entry_price FROM paper_positions "
            "WHERE id=? AND status='open'", (pid,)).fetchone()
        if row is None:
            return None
        symbol, side, qty, entry = row
        fill = price * (1 - SLIPPAGE) if side == "long" else price * (1 + SLIPPAGE)
        pnl_usd = (fill - entry) * qty if side == "long" else (entry - fill) * qty
        pnl_usd -= (entry + fill) * qty * _fee_pct(symbol)  # entry + exit fees
        pnl_pct = 100 * pnl_usd / (entry * qty)
        con.execute(
            "UPDATE paper_positions SET status='closed', exit_price=?, "
            "exit_ts=?, exit_reason=?, pnl_usd=?, pnl_pct=? WHERE id=?",
            (fill, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             reason, pnl_usd, pnl_pct, pid),
        )
        con.commit()
    finally:
        con.close()
    return {"id": pid, "symbol": symbol, "side": side, "exit_price": fill,
            "reason": reason, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct}


def pnl_summary(days: int = 1) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    con = _connect()
    try:
        rows = con.execute(
            "SELECT symbol, side, pnl_usd, pnl_pct, exit_reason, exit_ts "
            "FROM paper_positions WHERE status='closed' AND exit_ts >= ? "
            "ORDER BY exit_ts DESC", (since,)).fetchall()
    finally:
        con.close()
    total = sum(r[2] for r in rows)
    wins = sum(1 for r in rows if r[2] > 0)
    return {
        "days": days, "closed_trades": len(rows), "total_pnl_usd": round(total, 2),
        "win_rate_pct": round(100 * wins / len(rows), 1) if rows else None,
        "trades": [{"symbol": r[0], "side": r[1], "pnl_usd": round(r[2], 2),
                    "pnl_pct": round(r[3], 2), "reason": r[4], "exit_ts": r[5]}
                   for r in rows[:15]],
    }


def history(limit: int = 2000) -> list[dict]:
    """All paper positions, open and closed, newest first."""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT id, symbol, side, qty, entry_price, entry_ts, sl, tp, "
            "status, exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, cfg "
            "FROM paper_positions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        con.close()
    cols = ["id", "symbol", "side", "qty", "entry_price", "entry_ts", "sl",
            "tp", "status", "exit_price", "exit_ts", "exit_reason",
            "pnl_usd", "pnl_pct", "cfg"]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["cfg"] = json.loads(d["cfg"]) if d["cfg"] else None
        out.append(d)
    return out


def risk_check() -> str | None:
    """Return a reason string if new entries must be blocked, else None."""
    if len(open_positions()) >= MAX_OPEN:
        return f"max open positions reached ({MAX_OPEN})"
    day = pnl_summary(days=1)
    if day["total_pnl_usd"] <= -abs(DAILY_LOSS_CAP_USD):
        return (f"daily loss cap hit ({day['total_pnl_usd']:.2f} USD <= "
                f"-{DAILY_LOSS_CAP_USD:.0f})")
    return None
