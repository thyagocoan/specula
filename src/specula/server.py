"""Local API behind the Specula web portal.

Serves the backtest registry live, launches allowlisted jobs as subprocesses
(with captured logs), and exposes walk-forward results and report HTMLs.

Start from the repo root:
    uv run uvicorn specula.server:app --port 8756
"""

import json
import math
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from specula import runlog

load_dotenv()
app = FastAPI(title="specula-api")
app.add_middleware(GZipMiddleware, minimum_size=1000)


def _json_safe(o):
    """inf/nan -> null recursively (strict JSON compliance)."""
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o

JOB_TYPES = {
    "sweep_mtf": {
        "label": "MTF sweep (BTCUSDT)",
        "cmd": [sys.executable, "scripts/sweep_mtf_btcusdt.py"],
    },
    "sweep_equities": {
        "label": "MTF sweep (10 equities)",
        "cmd": [sys.executable, "scripts/sweep_mtf_equities.py"],
    },
    "rsi_filter": {
        "label": "RSI filter analysis (FFFD BTCUSDT)",
        "cmd": [sys.executable, "scripts/rsi_filter_fffd.py"],
    },
    "walkforward": {
        "label": "Walk-forward validation (BTCUSDT)",
        "cmd": [sys.executable, "scripts/walkforward.py"],
    },
    "export_web": {
        "label": "Re-export web data + reports",
        "cmd": [sys.executable, "scripts/export_web_data.py"],
    },
    "daily_update": {
        "label": "Daily data update (+walk-forward)",
        "cmd": [sys.executable, "scripts/daily_update.py", "--with-backtests"],
    },
    "overnight_lab": {
        "label": "Overnight strategy lab (discovery + OOS)",
        "cmd": [sys.executable, "scripts/overnight_lab.py"],
    },
    "setup_league": {
        "label": "Setup League (favourites + auto candidates on ALL assets)",
        "cmd": [sys.executable, "scripts/setup_league.py"],
    },
    "league_explorer": {
        "label": "League Explorer (new setup combinations until paused)",
        "cmd": [sys.executable, "scripts/league_explorer.py"],
    },
    "readiness": {
        "label": "Readiness report (approved setups validation battery)",
        "cmd": [sys.executable, "scripts/readiness_report.py"],
    },
}

JOBS: dict[str, dict] = {}
LOG_DIR = Path("data/meta/job_logs")
WALKFORWARD_JSON = Path("data/meta/walkforward.json")

# discovery/portal machinery runs on the recent window even when the lake
# holds years of history; the readiness job clears this for full-depth
# validation. Inherited by every job subprocess.
from datetime import timedelta as _td

os.environ.setdefault(
    "SPECULA_FRAME_START",
    (datetime.now(timezone.utc) - _td(days=400)).strftime("%Y-%m-%d"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _watch(job_id: str, proc: subprocess.Popen, log_handle) -> None:
    rc = proc.wait()
    log_handle.close()
    job = JOBS[job_id]
    job["status"] = "done" if rc == 0 else "failed"
    job["returncode"] = rc
    job["finished_at"] = _now()


_runs_cache: dict = {"key": None, "body": None}


@app.get("/api/runs")
def get_runs():
    from fastapi import Response

    con = runlog._connect()
    try:
        key = con.execute(
            "SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM runs").fetchone()
    finally:
        con.close()
    if _runs_cache["key"] != key:
        _runs_cache["body"] = json.dumps(runlog.payload(runlog.load()))
        _runs_cache["key"] = key
    return Response(content=_runs_cache["body"], media_type="application/json")


@app.get("/api/jobs")
def list_jobs():
    return sorted(
        (
            {k: v for k, v in j.items() if not k.startswith("_")}
            for j in JOBS.values()
        ),
        key=lambda j: j["started_at"],
        reverse=True,
    )


@app.post("/api/jobs/{job_type}")
def start_job(job_type: str):
    if job_type not in JOB_TYPES:
        raise HTTPException(404, f"unknown job type {job_type}")
    if any(j["status"] == "running" for j in JOBS.values()):
        raise HTTPException(409, "another job is already running — one at a time")
    job_id = uuid.uuid4().hex[:8]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{job_id}.log"
    handle = log_path.open("wb")
    proc = subprocess.Popen(
        JOB_TYPES[job_type]["cmd"],
        stdout=handle,
        stderr=subprocess.STDOUT,
        cwd=Path.cwd(),
    )
    JOBS[job_id] = {
        "id": job_id,
        "type": job_type,
        "label": JOB_TYPES[job_type]["label"],
        "status": "running",
        "started_at": _now(),
        "finished_at": None,
        "returncode": None,
        "log": str(log_path),
    }
    threading.Thread(target=_watch, args=(job_id, proc, handle), daemon=True).start()
    return JOBS[job_id]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, tail: int = 100):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    out = {k: v for k, v in job.items() if not k.startswith("_")}
    log_path = Path(job["log"])
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        out["log_tail"] = "\n".join(lines[-tail:])
    return out


_curve_cache: dict[str, dict] = {}


@app.get("/api/curve/{run_id}")
def get_curve(run_id: str):
    """Daily equity + price curve for any logged run, built on demand."""
    if run_id in _curve_cache:
        return _curve_cache[run_id]
    from specula.backtest import INIT_CASH, build_portfolio
    from specula.sweeps import cfg_label

    try:
        cfg = runlog.get_cfg(run_id)
    except KeyError:
        raise HTTPException(404, f"run {run_id} not found")
    pf = build_portfolio(cfg)
    val = pf.value().resample("1D").last().dropna() / INIT_CASH
    px = pf.close.dropna().resample("1D").last().dropna()
    px = px / px.iloc[0]
    doc = {
        "run_id": run_id,
        "label": cfg_label(cfg, with_fee=True),
        "points": [{"t": str(t.date()), "v": round(float(v), 4)}
                   for t, v in val.items()],
        "price": [{"t": str(t.date()), "v": round(float(v), 4)}
                  for t, v in px.items()],
    }
    if len(_curve_cache) > 60:
        _curve_cache.pop(next(iter(_curve_cache)))
    _curve_cache[run_id] = doc
    return doc


CANDLE_TFS = {"1min", "5min", "15min", "30min", "1h", "2h", "4h", "1d"}


@app.get("/api/candles/{symbol}")
def get_candles(symbol: str, tf: str = "1h", days: float | None = None,
                indicators: int = 0):
    """OHLC candles for the TradingView-style chart (times = unix UTC).
    indicators=1 adds sma50/sma200/ema21/session vwap/rsi14, computed on the
    full frame (so lookbacks are honest) before the window is sliced."""
    if tf not in CANDLE_TFS:
        raise HTTPException(400, f"tf must be one of {sorted(CANDLE_TFS)}")
    import math as _m

    import pandas as pd

    from specula.backtest import frames
    from specula.data import is_equity

    try:
        df = frames(symbol.upper(), tf)
    except Exception:
        raise HTTPException(404, f"no data for {symbol}")

    ind_cols = []
    if indicators:
        from specula.features import wilder_rsi

        df = df.copy()
        close = df["close"]
        df["sma50"] = close.rolling(50).mean()
        df["sma200"] = close.rolling(200).mean()
        df["ema21"] = close.ewm(span=21, adjust=False).mean()
        df["rsi"] = wilder_rsi(close, 14)
        session = (df.index.tz_convert("America/New_York").date
                   if is_equity(symbol.upper()) else df.index.date)
        tp = (df["high"] + df["low"] + df["close"]) / 3
        pv = (tp * df["volume"]).groupby(list(session)).cumsum()
        vv = df["volume"].groupby(list(session)).cumsum()
        df["vwap"] = (pv / vv).where(vv > 0)
        ind_cols = ["sma50", "sma200", "ema21", "rsi", "vwap"]

    if days:
        df = df[df.index >= df.index.max() - pd.Timedelta(days=days)]
    df = df.tail(20000)

    def _f(v):
        v = float(v)
        return round(v, 6) if _m.isfinite(v) else None

    out = []
    for ts, r in zip(df.index, df.to_dict("records")):
        row = {"time": int(ts.timestamp()), "open": _f(r["open"]),
               "high": _f(r["high"]), "low": _f(r["low"]),
               "close": _f(r["close"])}
        for c in ind_cols:
            row[c] = _f(r[c])
        out.append(row)
    return out


_trades_cache: dict[str, list] = {}


def _run_trades(run_id: str) -> list[dict]:
    if run_id in _trades_cache:
        return _trades_cache[run_id]
    from specula.backtest import build_portfolio
    from specula.settings import get_settings

    import pandas as pd

    cfg = runlog.get_cfg(run_id)
    pf = build_portfolio(cfg)
    idx = pf.wrapper.index
    sym = cfg.get("symbol", "")
    s = get_settings()
    # fill semantics: signal-bar-close fills vs intra-bar stop/target fills.
    # Bars are stamped with their START time, so a close-fill really happens
    # one bar-length later — the UI uses this to place markers honestly.
    bar_sec = int(pd.Timedelta(cfg.get("exec_tf", "1min")).total_seconds())
    entry_fill = ("close" if cfg.get("strategy") == "lab"
                  and cfg.get("entry", {}).get("kind") in
                  ("ma_cross", "vwap", "rsi_cross", "donchian", "boll",
                   "macd", "mom", "fffd_ff") else "intrabar")
    crypto = sym.endswith(("USDT", "USDC"))
    size = (s["trade_size_crypto_usd"] if crypto
            else s["trade_size_stock_usd"]) or 1000
    fee_now = (s["fee_crypto_pct"] if crypto else s["fee_stock_pct"]) / 100.0
    fee_run = float(cfg.get("fee") or 0.0)
    # runs are logged at their sweep's fee scenario; restate returns at the
    # user's current venue fee (entry + exit sides)
    fee_adj_pct = 200.0 * (fee_now - fee_run)
    out = []
    for r in pf.trades.records.to_dict("records"):
        closed = int(r["status"]) == 1
        ret = round(100 * float(r["return"]), 3) if closed else None
        net = round(ret - fee_adj_pct, 3) if closed else None
        out.append({
            "entry_ts": idx[int(r["entry_idx"])].isoformat(),
            "exit_ts": idx[int(r["exit_idx"])].isoformat() if closed else None,
            "side": "long" if int(r["direction"]) == 0 else "short",
            "entry_price": round(float(r["entry_price"]), 6),
            "exit_price": round(float(r["exit_price"]), 6) if closed else None,
            "return_pct": ret,
            "net_return_pct": net,
            "size_usd": size,
            "pnl_usd": round(size * net / 100, 2) if closed else None,
            "status": "closed" if closed else "open",
            "bar_sec": bar_sec,
            "entry_fill": entry_fill,
        })
    if len(_trades_cache) > 400:
        _trades_cache.pop(next(iter(_trades_cache)))
    _trades_cache[run_id] = out
    return out


@app.get("/api/trades/{run_id}")
def get_trades(run_id: str):
    """Every trade of a logged run with exact entry/exit timestamps."""
    try:
        return _run_trades(run_id)
    except KeyError:
        raise HTTPException(404, f"run {run_id} not found")


_journal_cache: dict = {}


def _journal_approved(sig: str, limit_symbols: int) -> dict:
    """Journal feed for League-approved setups: each approved setup on its
    top assets (by PF among the league's per-asset registry rows)."""
    from specula.sweeps import strategy_sig

    approved = [f for f in runlog.fav_setups_list()
                if f["status"] == "approved" and f["params"]]
    if sig != "all":
        approved = [f for f in approved if f["sig"] == sig]
    if not approved:
        raise HTTPException(404, "no approved setups — approve some on the "
                                 "League page first")

    df = runlog.load()
    df = df[(df["sweep_tag"] == "setup-league-v1")
            & df["profit_factor"].notna()]
    want = {f["sig"]: f for f in approved}
    by_sig: dict[str, list] = {}
    for r in df.to_dict("records"):
        rsig = strategy_sig(json.loads(r["params"]))
        if rsig in want:
            by_sig.setdefault(rsig, []).append(r)

    from specula.data import is_equity

    trades, setups = [], []
    for fsig, f in want.items():
        rows = [r for r in by_sig.get(fsig, [])
                if (r["n_trades"] or 0) >= 5 and is_equity(r["symbol"])]
        rows.sort(key=lambda r: -(r["profit_factor"]
                                  if math.isfinite(r["profit_factor"]) else 0))
        for r in rows[:limit_symbols]:
            pf_val = float(r["profit_factor"])
            setups.append({
                "symbol": r["symbol"], "label": f["label"],
                "run_id": r["run_id"],
                "pf": pf_val if math.isfinite(pf_val) else None,
                "oos_pf": None,
            })
            try:
                for t in _run_trades(r["run_id"]):
                    trades.append({**t, "symbol": r["symbol"],
                                   "setup": f["label"]})
            except Exception as e:
                print(f"[journal] {r['symbol']}: {type(e).__name__}: {e}",
                      flush=True)
        if not rows:
            print(f"[journal] approved '{f['label']}' has no league "
                  f"registry rows — re-run the league", flush=True)
    trades.sort(key=lambda t: t["entry_ts"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": (f"{approved[0]['label']} — top {limit_symbols} assets by PF"
                  if sig != "all"
                  else f"{len(approved)} approved setups, top "
                       f"{limit_symbols} assets each"),
        "symbols": sorted({t["symbol"] for t in trades}),
        "setups": sorted(setups, key=lambda x: (x["label"], x["symbol"])),
        "multi": sig == "all" and len(approved) > 1,
        "trades": trades,
    }


@app.get("/api/journal")
def get_journal(limit_symbols: int = 20, sig: str | None = None):
    """Chronological trades with per-trade USD P&L at the configured class
    sizes — the capacity-planning feed for the Journal. With `sig` (an
    approved setup's sig, or "all"), the feed is that approved setup on its
    top assets; without it, the legacy best-setup-per-asset scope."""
    from specula.settings import get_settings

    roster = runlog.autotrade_symbols()
    s = get_settings()
    key = (tuple(roster), limit_symbols, sig, json.dumps(s, sort_keys=True))
    if key in _journal_cache:
        return _journal_cache[key]

    if sig:
        doc = _journal_approved(sig, limit_symbols)
        if len(_journal_cache) > 6:
            _journal_cache.pop(next(iter(_journal_cache)))
        _journal_cache[key] = doc
        return doc

    from specula.sweeps import cfg_label

    df = runlog.load()
    df = df[(df["n_trades"] >= 30) & df["profit_factor"].notna()]
    best = (df.sort_values("profit_factor", ascending=False)
              .groupby("symbol").first().reset_index())

    oos = {}
    if WALKFORWARD_JSON.exists():
        wf = json.loads(WALKFORWARD_JSON.read_text(encoding="utf-8"))
        for d in wf.get("symbols", []):
            sym = d["symbol"].split("·")[0]
            agg = d["scenarios"][0]["aggregate"]
            pf = agg.get("oos_pf")
            if (pf is not None and math.isfinite(pf)
                    and (agg.get("oos_trades") or 0) >= 20):
                oos[sym] = max(oos.get(sym, 0), pf)
    best["oos"] = best["symbol"].map(oos)

    if roster:
        chosen = best[best["symbol"].isin(roster)]
    else:
        chosen = (best[best["oos"].notna() & (best["oos"] > 1.0)]
                  .sort_values("oos", ascending=False).head(limit_symbols))

    trades, setups = [], []
    for r in chosen.to_dict("records"):
        sym = r["symbol"]
        try:
            label = cfg_label(json.loads(r["params"]))
        except Exception:
            label = r.get("strategy") or "?"
        pf_val = float(r["profit_factor"])
        oos_val = float(r.get("oos") if r.get("oos") is not None else math.nan)
        setups.append({
            "symbol": sym, "label": label, "run_id": r["run_id"],
            "pf": pf_val if math.isfinite(pf_val) else None,
            "oos_pf": oos_val if math.isfinite(oos_val) else None,
        })
        try:
            for t in _run_trades(r["run_id"]):
                trades.append({**t, "symbol": sym})
        except Exception as e:
            print(f"[journal] {sym}: {type(e).__name__}: {e}", flush=True)
    trades.sort(key=lambda t: t["entry_ts"])
    doc = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "scope": "roster" if roster else f"top {len(chosen)} by OOS PF",
           "symbols": sorted(chosen["symbol"].tolist()),
           "setups": sorted(setups, key=lambda s: s["symbol"]),
           "trades": trades}
    if len(_journal_cache) > 6:
        _journal_cache.pop(next(iter(_journal_cache)))
    _journal_cache[key] = doc
    return doc


class RosterSync(BaseModel):
    per_setup: int = 10


@app.post("/api/autotrade/sync_approved")
def sync_approved(u: RosterSync):
    """Fill the scanner roster from the approved setups: each approved
    setup's top-N league assets, best setup kept when symbols collide
    (the roster is one config per symbol)."""
    from specula.sweeps import strategy_sig

    approved = [f for f in runlog.fav_setups_list()
                if f["status"] == "approved" and f["params"]]
    if not approved:
        raise HTTPException(400, "no approved setups — approve on the "
                                 "Setups page first")
    df = runlog.load()
    df = df[(df["sweep_tag"] == "setup-league-v1")
            & df["profit_factor"].notna()]
    want = {f["sig"]: f for f in approved}
    best: dict[str, tuple[float, dict, str]] = {}
    matched = 0
    from specula.data import is_equity

    for r in df.to_dict("records"):
        params = json.loads(r["params"])
        rsig = strategy_sig(params)
        if rsig not in want or (r["n_trades"] or 0) < 5:
            continue
        if not is_equity(r["symbol"]):
            continue  # approved setups trade stocks only (crypto = own class)
        matched += 1
        pf = float(r["profit_factor"])
        if not math.isfinite(pf):
            continue
        prev = best.get(r["symbol"])
        if not prev or pf > prev[0]:
            best[r["symbol"]] = (pf, params, want[rsig]["label"])
    # keep each setup's strongest assets, cap per setup
    per_setup: dict[str, list] = {}
    for sym, (pf, params, label) in best.items():
        per_setup.setdefault(label, []).append((pf, sym, params))
    enabled = []
    for label, rows in per_setup.items():
        rows.sort(reverse=True)
        for pf, sym, params in rows[:u.per_setup]:
            runlog.autotrade_set(sym, True, cfg=params)
            enabled.append({"symbol": sym, "setup": label,
                            "pf": round(pf, 2)})
    if not enabled:
        raise HTTPException(404, "no league registry rows for the approved "
                                 "setups — run the league first")
    # the sync is authoritative: anything previously enabled that is not in
    # this assignment (demoted setups, stale entries) gets disabled
    keep_syms = {e["symbol"] for e in enabled}
    stale = [r["symbol"] for r in runlog.autotrade_list()
             if r["enabled"] and r["symbol"] not in keep_syms]
    for sym in stale:
        runlog.autotrade_set(sym, False)
    return {"ok": True, "enabled": sorted(enabled, key=lambda x: x["symbol"]),
            "note": f"{len(enabled)} symbols on the roster, {len(stale)} "
                    f"stale entries disabled ({matched} league rows matched)"}


class FavSetupUpdate(BaseModel):
    sig: str
    label: str | None = None
    params: dict | None = None
    status: str | None = None
    remove: bool = False


@app.get("/api/favsetups")
def get_favsetups():
    """Server-side favourite setups (shared across devices; feeds the League)."""
    return runlog.fav_setups_list()


@app.post("/api/favsetups")
def post_favsetups(u: FavSetupUpdate):
    if u.remove:
        runlog.fav_setups_delete(u.sig)
    else:
        runlog.fav_setups_set(u.sig, u.label, u.params, u.status)
    return {"ok": True, "favs": runlog.fav_setups_list()}


LEAGUE_JSON = Path("data/meta/setup_league.json")
EXPLORER_STATE = Path("data/meta/explorer_state.json")
SECTORS_JSON = Path("data/meta/sectors.json")


@app.get("/api/paper")
def get_paper():
    """Paper-trading history + daily balance for the portal. Open trades
    carry the scanner's last seen price and unrealized P&L."""
    from collections import defaultdict
    from zoneinfo import ZoneInfo

    from specula import paper
    from specula.sweeps import cfg_label

    trades = paper.history()
    try:
        scanner_state = json.loads(
            Path("data/meta/scanner_state.json").read_text(encoding="utf-8"))
    except Exception:
        scanner_state = {}
    last_prices = {s: v.get("last_price")
                   for s, v in scanner_state.get("symbols", {}).items()}
    for t in trades:
        if t["status"] != "open":
            continue
        last = last_prices.get(t["symbol"])
        t["last_price"] = last
        if last:
            sign = 1 if t["side"] == "long" else -1
            t["unreal_usd"] = round(sign * (last - t["entry_price"])
                                    * t["qty"], 2)
            t["unreal_pct"] = round(
                100 * sign * (last - t["entry_price"]) / t["entry_price"], 2)
    ny = ZoneInfo("America/New_York")
    days: dict[str, dict] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    total = 0.0
    wins = closed = 0
    for t in trades:
        t["setup"] = cfg_label(t["cfg"]) if t["cfg"] else "—"
        t["exec_tf"] = (t["cfg"] or {}).get("exec_tf")
        del t["cfg"]
        if t["status"] != "closed" or t["pnl_usd"] is None:
            continue
        closed += 1
        total += t["pnl_usd"]
        if t["pnl_usd"] > 0:
            wins += 1
        day = (datetime.fromisoformat(t["exit_ts"]).astimezone(ny)
               .strftime("%Y-%m-%d"))
        d = days[day]
        d["trades"] += 1
        d["pnl"] += t["pnl_usd"]
        if t["pnl_usd"] > 0:
            d["wins"] += 1
    return {
        "trades": trades,
        "summary": {
            "open": sum(1 for t in trades if t["status"] == "open"),
            "closed": closed,
            "wins": wins,
            "total_pnl": round(total, 2),
            "days": [{"date": k, **{kk: (round(vv, 2) if kk == "pnl" else vv)
                                    for kk, vv in v.items()}}
                     for k, v in sorted(days.items(), reverse=True)],
        },
    }


@app.get("/api/candles_recent/{symbol}")
def get_candles_recent(symbol: str, tf: str = "30min", days: float = 5):
    """Recent candles straight from Alpaca — the lake updates nightly, but
    validating today's paper trades needs same-day bars. RTH-only,
    session-aligned, same shape as /api/candles."""
    if tf not in CANDLE_TFS:
        raise HTTPException(400, f"tf must be one of {sorted(CANDLE_TFS)}")
    import math as _m

    import pandas as pd

    from specula.data import resample_equity
    from specula.scanner import NY, _alpaca_fetch

    sym = symbol.upper()
    end = datetime.now(timezone.utc) - pd.Timedelta(minutes=16)
    bars = _alpaca_fetch([sym], end - pd.Timedelta(days=days), end).get(sym)
    if not bars:
        raise HTTPException(404, f"no recent bars for {sym}")
    df = pd.DataFrame([{
        "ts": b["t"], "open": b["o"], "high": b["h"], "low": b["l"],
        "close": b["c"], "volume": b["v"],
    } for b in bars])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")
    ny = df.index.tz_convert(NY)
    mins = ny.hour * 60 + ny.minute
    df = df[(mins >= 570) & (mins < 960) & (ny.weekday < 5)]
    if df.empty:
        raise HTTPException(404, f"no session bars for {sym}")
    out = resample_equity(df, tf)

    def _f(v):
        v = float(v)
        return round(v, 6) if _m.isfinite(v) else None

    return [{"time": int(ts.timestamp()), "open": _f(r["open"]),
             "high": _f(r["high"]), "low": _f(r["low"]),
             "close": _f(r["close"])}
            for ts, r in zip(out.index, out.to_dict("records"))]


READINESS_JSON = Path("data/meta/readiness.json")


@app.get("/api/readiness")
def get_readiness():
    """Latest readiness-report results (run the readiness job to refresh)."""
    if not READINESS_JSON.exists():
        return {"available": False}
    doc = json.loads(READINESS_JSON.read_text(encoding="utf-8"))
    doc["available"] = True
    return doc


@app.get("/api/sectors")
def get_sectors():
    """symbol -> GICS sector map (scripts/build_sectors.py refreshes it)."""
    if not SECTORS_JSON.exists():
        return {}
    return json.loads(SECTORS_JSON.read_text(encoding="utf-8"))


@app.get("/api/league")
def get_league():
    """Latest Setup League scorecard (run the setup_league job to refresh)."""
    if not LEAGUE_JSON.exists():
        return {"available": False}
    doc = json.loads(LEAGUE_JSON.read_text(encoding="utf-8"))
    doc["available"] = True
    return doc


def _explorer_state() -> dict:
    try:
        return json.loads(EXPLORER_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"state": "pause", "round": 0}


class ExplorerAction(BaseModel):
    action: str  # "start" | "pause"


@app.get("/api/explorer")
def get_explorer():
    st = _explorer_state()
    st["job_running"] = any(
        j["status"] == "running" and j["type"] == "league_explorer"
        for j in JOBS.values())
    return st


@app.post("/api/explorer")
def post_explorer(a: ExplorerAction):
    st = _explorer_state()
    if a.action == "pause":
        st["state"] = "pause"
        EXPLORER_STATE.parent.mkdir(parents=True, exist_ok=True)
        EXPLORER_STATE.write_text(json.dumps(st), encoding="utf-8")
        return {"ok": True, "state": "pause",
                "note": "the explorer stops after its current round"}
    if a.action != "start":
        raise HTTPException(400, "action must be start or pause")
    st["state"] = "run"
    EXPLORER_STATE.parent.mkdir(parents=True, exist_ok=True)
    EXPLORER_STATE.write_text(json.dumps(st), encoding="utf-8")
    note = "explorer running"
    try:
        start_job("league_explorer")
    except HTTPException as e:
        note = f"state set to run; launch deferred ({e.detail})"
    return {"ok": True, "state": "run", "note": note}


@app.get("/api/walkforward")
def get_walkforward():
    if not WALKFORWARD_JSON.exists():
        return {"available": False}
    doc = _json_safe(json.loads(WALKFORWARD_JSON.read_text(encoding="utf-8")))
    return {"available": True, **doc}


@app.get("/data/curves.json")
def get_curves_file():
    for p in (Path("data/meta/curves.json"), Path("web/public/data/curves.json")):
        if p.exists():
            return FileResponse(p, media_type="application/json")
    raise HTTPException(404, "curves not generated yet")


class SettingsUpdate(BaseModel):
    fee_crypto_pct: float | None = None
    fee_stock_pct: float | None = None
    capital_usd: float | None = None
    trade_size_crypto_usd: float | None = None
    trade_size_stock_usd: float | None = None


@app.get("/api/settings")
def settings_get():
    from specula.settings import get_settings
    return get_settings()


@app.post("/api/settings")
def settings_update(u: SettingsUpdate):
    from specula.settings import save_settings
    vals = {k: v for k, v in u.model_dump().items() if v is not None}
    for k, v in vals.items():
        if k.startswith("fee") and not (0 <= v <= 5):
            raise HTTPException(400, f"{k} must be between 0 and 5 (percent per side)")
        if not k.startswith("fee") and v < 0:
            raise HTTPException(400, f"{k} must be >= 0")
    s = save_settings(vals)
    _curve_cache.clear()   # cached curves/trades were built with old settings
    _trades_cache.clear()
    return {"ok": True, "settings": s,
            "note": "applies to new backtests/curves; running jobs and "
                    "historical registry rows keep their recorded fees"}


class AutotradeUpdate(BaseModel):
    symbol: str
    enabled: bool
    size_usd: float | None = None
    force: bool = False


def _oos_pf(symbol: str) -> float | None:
    if not WALKFORWARD_JSON.exists():
        return None
    wf = json.loads(WALKFORWARD_JSON.read_text(encoding="utf-8"))
    best = None
    for d in wf.get("symbols", []):
        if d["symbol"].split("·")[0] != symbol:
            continue
        for s in d["scenarios"][:1]:
            pf = s["aggregate"].get("oos_pf")
            if pf is not None and (best is None or pf > best):
                best = pf
    return best


@app.get("/api/autotrade")
def autotrade_list():
    rows = runlog.autotrade_list()
    for r in rows:
        r["oos_pf"] = _oos_pf(r["symbol"])
    return rows


@app.post("/api/autotrade")
def autotrade_update(u: AutotradeUpdate):
    sym = u.symbol.upper()
    if u.enabled and not u.force:
        pf = _oos_pf(sym)
        if pf is None or pf <= 1.0:
            raise HTTPException(
                400, f"{sym} has no positive out-of-sample verdict "
                     f"(OOS PF: {pf}) — pass force=true to override")
    runlog.autotrade_set(sym, enabled=u.enabled, size_usd=u.size_usd)
    return {"ok": True, "symbol": sym, "enabled": u.enabled}


# ------------------------------------------------------- scheduler (container)

def _scheduled(job_type: str) -> None:
    if any(j["status"] == "running" for j in JOBS.values()):
        print(f"[scheduler] skip {job_type}: another job is running", flush=True)
        return
    try:
        start_job(job_type)
        print(f"[scheduler] launched {job_type}", flush=True)
    except Exception as e:
        print(f"[scheduler] {job_type} failed to launch: {e}", flush=True)


def _explorer_keeper() -> None:
    """Relaunch the explorer if the user left it in 'run' and no job holds
    the slot (it yields around scheduled jobs and container restarts)."""
    if _explorer_state().get("state") != "run":
        return
    _scheduled("league_explorer")


if os.environ.get("SPECULA_SCHEDULER") == "1":
    from apscheduler.schedulers.background import BackgroundScheduler

    _sched = BackgroundScheduler(timezone="Australia/Melbourne")
    _sched.add_job(lambda: _scheduled("daily_update"), "cron",
                   hour=22, minute=15, id="nightly")
    _sched.add_job(lambda: _scheduled("overnight_lab"), "cron",
                   day_of_week="sat", hour=1, minute=0, id="weekly_lab")
    _sched.add_job(_explorer_keeper, "interval", minutes=15,
                   id="explorer_keeper")
    _sched.start()
    print("[scheduler] active: nightly 22:15, weekly lab Sat 01:00, "
          "explorer keeper 15min (Australia/Melbourne)", flush=True)


Path("reports").mkdir(exist_ok=True)
app.mount("/reports", StaticFiles(directory="reports"), name="reports")

# serve the built SPA when present (Docker image / `npm run build`)
if Path("web/dist/index.html").exists():
    app.mount("/", StaticFiles(directory="web/dist", html=True), name="spa")
