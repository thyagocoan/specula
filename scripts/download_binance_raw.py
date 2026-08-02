"""Download raw Binance 1m kline archives (spot) into data/raw/.

Stdlib only — runs without any installed dependencies.
Idempotent: already-verified files are skipped. Every file is sha256-checked
against its sibling .CHECKSUM before being considered complete.

Usage:
    python scripts/download_binance_raw.py --symbol BTCUSDT --start 2025-07
"""

import argparse
import hashlib
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

BASE = "https://data.binance.vision/data/spot"
RETRIES = 3


def month_range(start: str, end: str) -> list[str]:
    y, m = (int(p) for p in start.split("-"))
    ey, em = (int(p) for p in end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def fetch(url: str) -> bytes:
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
        time.sleep(2**attempt)
    raise RuntimeError(f"failed after {RETRIES} retries: {url}: {last_err}")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_one(url: str, dest: Path) -> str:
    """Download url + its .CHECKSUM, verify, mark with .ok sentinel. Returns status."""
    ok_marker = dest.with_suffix(dest.suffix + ".ok")
    if ok_marker.exists():
        return "skip"
    try:
        checksum_line = fetch(url + ".CHECKSUM").decode().strip()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "missing"
        raise
    expected = checksum_line.split()[0]
    data = fetch(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    actual = sha256_of(tmp)
    if actual != expected:
        tmp.unlink()
        raise RuntimeError(f"checksum mismatch for {dest.name}: {actual} != {expected}")
    tmp.replace(dest)
    ok_marker.write_text(expected)
    return "done"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1m")
    ap.add_argument("--start", default="2025-07", help="first month, YYYY-MM")
    ap.add_argument("--data-root", default="data")
    args = ap.parse_args()

    today = date.today()
    last_full_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    raw_dir = Path(args.data_root) / "raw" / "binance" / "spot" / args.symbol / args.interval

    jobs: list[tuple[str, Path]] = []
    for ym in month_range(args.start, last_full_month):
        fname = f"{args.symbol}-{args.interval}-{ym}.zip"
        jobs.append((f"{BASE}/monthly/klines/{args.symbol}/{args.interval}/{fname}", raw_dir / fname))
    # current month: daily files up to T-1
    d = today.replace(day=1)
    while d < today:
        fname = f"{args.symbol}-{args.interval}-{d.isoformat()}.zip"
        jobs.append((f"{BASE}/daily/klines/{args.symbol}/{args.interval}/{fname}", raw_dir / fname))
        d += timedelta(days=1)

    counts = {"done": 0, "skip": 0, "missing": 0}
    for url, dest in jobs:
        status = download_one(url, dest)
        counts[status] += 1
        print(f"[{status:>7}] {dest.name}", flush=True)
        # monthly archive not published yet -> fall back to that month's daily files
        if status == "missing" and "/monthly/" in url:
            ym = dest.stem[-7:]
            y, m = int(ym[:4]), int(ym[5:])
            d = date(y, m, 1)
            while d < min(today, date(y + m // 12, m % 12 + 1, 1)):
                fname = f"{args.symbol}-{args.interval}-{d.isoformat()}.zip"
                s = download_one(
                    f"{BASE}/daily/klines/{args.symbol}/{args.interval}/{fname}", raw_dir / fname
                )
                counts[s] += 1
                print(f"[{s:>7}] {fname}", flush=True)
                d += timedelta(days=1)

    print(f"\nfinished: {counts['done']} downloaded, {counts['skip']} skipped, "
          f"{counts['missing']} not yet published", flush=True)
    return 0 if (counts["done"] + counts["skip"]) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
