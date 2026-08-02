"""Append-only registry of every backtest ever run.

Each executed config becomes one row in data/meta/backtest_runs.parquet:
run_id, timestamp, git commit, sweep tag, full params as JSON (enough to
reproduce the run via backtest.build_portfolio), and the result metrics.
"""

import json
import math
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def _finite(v):
    """Non-finite floats become None so params stay strict-JSON-safe."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v

REGISTRY = Path("data/meta/backtest_runs.parquet")
WEB_DATA = Path("web/public/data/runs.json")
REPORTS = Path("reports")

CFG_COLS = ["symbol", "strategy", "setup_tf", "exec_tf"]


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
    new = pd.DataFrame(rows)
    if REGISTRY.exists():
        new = pd.concat([pd.read_parquet(REGISTRY), new], ignore_index=True)
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(REGISTRY)
    export_web(new)
    return new


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


def load() -> pd.DataFrame:
    return pd.read_parquet(REGISTRY)


def get_cfg(run_id: str) -> dict:
    df = load()
    match = df[df["run_id"] == run_id]
    if match.empty:
        raise KeyError(f"run_id {run_id} not found in {REGISTRY}")
    return json.loads(match.iloc[0]["params"])
