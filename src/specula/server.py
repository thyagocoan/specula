"""Local API behind the Specula web portal.

Serves the backtest registry live, launches allowlisted jobs as subprocesses
(with captured logs), and exposes walk-forward results and report HTMLs.

Start from the repo root:
    uv run uvicorn specula.server:app --port 8756
"""

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from specula import runlog

load_dotenv()
app = FastAPI(title="specula-api")

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
}

JOBS: dict[str, dict] = {}
LOG_DIR = Path("data/meta/job_logs")
WALKFORWARD_JSON = Path("data/meta/walkforward.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _watch(job_id: str, proc: subprocess.Popen, log_handle) -> None:
    rc = proc.wait()
    log_handle.close()
    job = JOBS[job_id]
    job["status"] = "done" if rc == 0 else "failed"
    job["returncode"] = rc
    job["finished_at"] = _now()


@app.get("/api/runs")
def get_runs():
    return runlog.payload(runlog.load())


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


@app.get("/api/walkforward")
def get_walkforward():
    if not WALKFORWARD_JSON.exists():
        return {"available": False}
    return {"available": True, **json.loads(WALKFORWARD_JSON.read_text(encoding="utf-8"))}


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


if os.environ.get("SPECULA_SCHEDULER") == "1":
    from apscheduler.schedulers.background import BackgroundScheduler

    _sched = BackgroundScheduler(timezone="Australia/Melbourne")
    _sched.add_job(lambda: _scheduled("daily_update"), "cron",
                   hour=22, minute=15, id="nightly")
    _sched.add_job(lambda: _scheduled("overnight_lab"), "cron",
                   day_of_week="sat", hour=1, minute=0, id="weekly_lab")
    _sched.start()
    print("[scheduler] active: nightly 22:15, weekly lab Sat 01:00 "
          "(Australia/Melbourne)", flush=True)


Path("reports").mkdir(exist_ok=True)
app.mount("/reports", StaticFiles(directory="reports"), name="reports")

# serve the built SPA when present (Docker image / `npm run build`)
if Path("web/dist/index.html").exists():
    app.mount("/", StaticFiles(directory="web/dist", html=True), name="spa")
