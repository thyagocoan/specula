"""Append-only registry of every backtest ever run.

Each executed config becomes one row in data/meta/backtest_runs.parquet:
run_id, timestamp, git commit, sweep tag, full params as JSON (enough to
reproduce the run via backtest.build_portfolio), and the result metrics.
"""

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REGISTRY = Path("data/meta/backtest_runs.parquet")

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
        "params": json.dumps(cfg, sort_keys=True),
    }
    row.update(metrics)
    return row


def append(rows: list[dict]) -> pd.DataFrame:
    new = pd.DataFrame(rows)
    if REGISTRY.exists():
        new = pd.concat([pd.read_parquet(REGISTRY), new], ignore_index=True)
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(REGISTRY)
    return new


def load() -> pd.DataFrame:
    return pd.read_parquet(REGISTRY)


def get_cfg(run_id: str) -> dict:
    df = load()
    match = df[df["run_id"] == run_id]
    if match.empty:
        raise KeyError(f"run_id {run_id} not found in {REGISTRY}")
    return json.loads(match.iloc[0]["params"])
