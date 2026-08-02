"""Gap-filler: wait for the running lab to finish, then redo the MA megasweep
(memory-fixed), re-score, re-run walk-forward on candidates, and refresh the
portal. Sends a Telegram note when done. Safe to leave running — it polls the
job API and only starts once nothing else is running.

Usage:
    uv run python scripts/rerun_ma_after_lab.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

API = "http://127.0.0.1:8756"
MAX_WAIT_H = 8

STEPS = [
    ("MA megasweep (retry)", ["scripts/megasweep_ma.py"], 2 * 3600),
    ("scoring", ["scripts/lab_sweep.py", "--score"], 900),
    ("walk-forward (candidates)",
     ["scripts/walkforward.py", "--candidates", "data/meta/lab_candidates.json"],
     3600),
    ("equity curves", ["scripts/export_curves.py"], 1800),
    ("web export", ["scripts/export_web_data.py"], 600),
]


def busy() -> bool:
    try:
        with urllib.request.urlopen(f"{API}/api/jobs", timeout=5) as r:
            jobs = json.loads(r.read())
        return any(j["status"] == "running" for j in jobs)
    except Exception:
        return False  # API down -> nothing the API launched can be running


def notify(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data),
            timeout=30,
        )
    except Exception:
        pass


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    t0 = time.monotonic()
    print("waiting for running jobs to finish...", flush=True)
    while busy():
        if time.monotonic() - t0 > MAX_WAIT_H * 3600:
            notify("[FAILED] MA megasweep retry never started: lab still "
                   f"running after {MAX_WAIT_H}h")
            return 1
        time.sleep(60)
    print(f"clear after {(time.monotonic() - t0) / 60:.0f} min — starting",
          flush=True)

    failures = []
    for label, cmd, cap in STEPS:
        t = time.monotonic()
        print(f"===== {label} =====", flush=True)
        try:
            r = subprocess.run([sys.executable, *cmd], timeout=cap)
            ok = r.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
        print(f"[{'ok' if ok else 'FAIL'}] {label} "
              f"({(time.monotonic() - t) / 60:.1f} min)", flush=True)
        if not ok:
            failures.append(label)

    mins = (time.monotonic() - t0) / 60
    if failures:
        notify(f"[DONE with issues] MA megasweep retry finished in {mins:.0f} min; "
               f"failed: {', '.join(failures)}")
    else:
        notify(f"[DONE] MA megasweep retry + rescoring finished in {mins:.0f} min. "
               "Full lab results (incl. MA-cross family) are on the portal — "
               "or ask me /best and /wf.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
