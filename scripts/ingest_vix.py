"""Refresh the daily VIX close series (data/meta/vix.csv).

Source: FRED (VIXCLS, no key needed). Runs standalone and as a step of the
nightly daily_update. The VIX gate uses the PRIOR day's close, so a
same-day refresh is never load-bearing.
"""

import csv
import io
import sys
import urllib.request
from pathlib import Path

OUT = Path("data/meta/vix.csv")

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "specula/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def main() -> None:
    rows = []
    raw = fetch(FRED)
    for rec in csv.DictReader(io.StringIO(raw)):
        val = rec.get("VIXCLS") or rec.get("value")
        if val and val != ".":
            rows.append((rec.get("DATE") or rec.get("observation_date"),
                         float(val)))
    if len(rows) < 100:
        print(f"[vix] only {len(rows)} rows — refusing to overwrite", flush=True)
        sys.exit(1)
    rows.sort()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "close"])
        w.writerows(rows)
    print(f"[vix] wrote {OUT}: {len(rows)} days, "
          f"{rows[0][0]} → {rows[-1][0]} (last close {rows[-1][1]})",
          flush=True)


if __name__ == "__main__":
    main()
