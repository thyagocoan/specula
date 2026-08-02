"""Ingest the top-20 highest-volume Binance USDT pairs with full-window history.

Candidates are ranked by 24h quote volume (stablecoins/wrapped/gold excluded);
a candidate only qualifies if its 1m archive exists for the window start month
(--start), so freshly listed pairs can't sneak in with partial history. The
first 20 qualifiers are downloaded (checksum-verified) and built into bronze.

Usage:
    uv run python scripts/ingest_crypto_top20.py --start 2025-07 \
        --candidates BTCUSDT,ETHUSDT,...
"""

import argparse
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "https://data.binance.vision/data/spot/monthly/klines"


def has_history(symbol: str, month: str) -> bool:
    url = f"{BASE}/{symbol}/1m/{symbol}-1m-{month}.zip.CHECKSUM"
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True
    except urllib.error.HTTPError:
        return False
    except urllib.error.URLError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-07")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--candidates", required=True, help="comma-separated, volume-ranked")
    args = ap.parse_args()

    selected = []
    for sym in [s.strip() for s in args.candidates.split(",") if s.strip()]:
        if len(selected) >= args.count:
            break
        if has_history(sym, args.start):
            selected.append(sym)
            print(f"[keep] {sym}", flush=True)
        else:
            print(f"[skip] {sym}: no {args.start} archive (listed later)", flush=True)

    print(f"\nselected {len(selected)}: {','.join(selected)}\n", flush=True)
    for sym in selected:
        for script, extra in [
            ("scripts/download_binance_raw.py", ["--symbol", sym, "--start", args.start]),
            ("scripts/build_bronze_binance.py", ["--symbol", sym]),
        ]:
            r = subprocess.run([sys.executable, script, *extra])
            if r.returncode != 0:
                print(f"[error] {script} failed for {sym}", flush=True)
    print("crypto ingest complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
