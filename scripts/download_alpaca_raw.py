"""Download raw Alpaca 1-minute stock bars into data/raw/.

Fetches SIP-feed minute bars per symbol per month, in both `raw` and `all`
(split+dividend adjusted) variants, saving the untouched JSON payloads gzipped.
Idempotent: complete months already on disk are skipped; the current month is
always refetched. Paginates until next_page_token is null and stays under the
free-tier rate limit (200 req/min).

Usage:
    uv run python scripts/download_alpaca_raw.py --start 2025-07
"""

import argparse
import gzip
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

SYMBOLS = ["NVDA", "AAPL", "GOOGL", "MSFT", "AMZN", "AVGO", "META", "TSLA", "BRK.B", "LLY"]
ADJUSTMENTS = ["raw", "all"]
REQ_INTERVAL = 0.35  # ~170 req/min, under the 200/min cap
SIP_DELAY = timedelta(minutes=16)  # free tier withholds most recent 15 min

_last_request = 0.0


def throttled_get(client: httpx.Client, url: str, params: dict) -> httpx.Response:
    global _last_request
    for attempt in range(6):
        wait = REQ_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            resp = client.get(url, params=params)
        except httpx.HTTPError:  # timeouts, transient transport errors
            time.sleep(2 ** (attempt + 1))
            continue
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"failed after retries: {url} {params}")


def month_bounds(ym: str) -> tuple[datetime, datetime]:
    y, m = int(ym[:4]), int(ym[5:])
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y + m // 12, m % 12 + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
    return start, end


def month_range(start: str, end: str) -> list[str]:
    y, m = int(start[:4]), int(start[5:])
    ey, em = int(end[:4]), int(end[5:])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def fetch_month(client: httpx.Client, base: str, symbol: str, ym: str, adjustment: str) -> dict:
    start, end = month_bounds(ym)
    now = datetime.now(timezone.utc)
    end = min(end, now - SIP_DELAY)
    bars: list[dict] = []
    pages = 0
    params = {
        "symbols": symbol,
        "timeframe": "1Min",
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "limit": 10000,
        "adjustment": adjustment,
        "feed": "sip",
    }
    page_token = None
    while True:
        if page_token:
            params["page_token"] = page_token
        payload = throttled_get(client, base + "/v2/stocks/bars", params).json()
        bars.extend(payload.get("bars", {}).get(symbol, []))
        pages += 1
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    return {
        "symbol": symbol,
        "month": ym,
        "adjustment": adjustment,
        "feed": "sip",
        "fetched_at": now.isoformat(),
        "pages": pages,
        "bars": bars,
    }


def main() -> int:
    load_dotenv()
    key_id = os.environ["ALPACA_KEY_ID"]
    secret = os.environ["ALPACA_SECRET_KEY"]
    base = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets")
    data_root = Path(os.environ.get("DATA_ROOT", "./data"))

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-07", help="first month, YYYY-MM")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    args = ap.parse_args()

    today = date.today()
    current_ym = today.strftime("%Y-%m")
    prev_ym = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    # always refetch the current and previous month: a month downloaded
    # mid-month would otherwise stay incomplete forever after rollover
    refetch = {current_ym, prev_ym}
    months = month_range(args.start, current_ym)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
    counts = {"done": 0, "skip": 0}
    t0 = time.monotonic()
    with httpx.Client(headers=headers, timeout=60) as client:
        for symbol in symbols:
            for adjustment in ADJUSTMENTS:
                out_dir = (
                    data_root / "raw" / "alpaca" / "stocks" / "1min"
                    / f"adjustment={adjustment}" / f"symbol={symbol}"
                )
                for ym in months:
                    dest = out_dir / f"{ym}.json.gz"
                    if dest.exists() and ym not in refetch:
                        counts["skip"] += 1
                        continue
                    payload = fetch_month(client, base, symbol, ym, adjustment)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_suffix(".gz.part")
                    with gzip.open(tmp, "wt", encoding="utf-8") as f:
                        json.dump(payload, f, separators=(",", ":"))
                    tmp.replace(dest)
                    counts["done"] += 1
                    print(
                        f"[done] {symbol} {ym} {adjustment}: {len(payload['bars'])} bars "
                        f"({payload['pages']} pages)",
                        flush=True,
                    )

    mins = (time.monotonic() - t0) / 60
    print(f"\nfinished in {mins:.1f} min: {counts['done']} fetched, {counts['skip']} skipped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
