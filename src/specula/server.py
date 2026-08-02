"""Local API behind the Specula web portal.

Serves the backtest registry live, launches allowlisted jobs as subprocesses
(with captured logs), and exposes walk-forward results and report HTMLs.

Start from the repo root:
    uv run uvicorn specula.server:app --port 8756
"""

import json
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from specula import runlog

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


@app.get("/api/walkforward")
def get_walkforward():
    if not WALKFORWARD_JSON.exists():
        return {"available": False}
    return {"available": True, **json.loads(WALKFORWARD_JSON.read_text(encoding="utf-8"))}


Path("reports").mkdir(exist_ok=True)
app.mount("/reports", StaticFiles(directory="reports"), name="reports")
