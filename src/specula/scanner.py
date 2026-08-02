"""Live signal scanner for autotrade roster symbols.

Every cycle (60s): pull recent 1m bars (Binance REST for crypto — real time;
Alpaca for stocks — free tier delays the last 15 min), evaluate the symbol's
strategy on the live window, alert when a setup arms, open a paper position
when the trigger breaks, and manage open positions (stop / target / EOD).

v1 evaluates the walk-forward-validated families (fffd, didi). Lab-type
configs (ma_cross/orb/vwap/rsi) are announced as unsupported once and
skipped — they join after tonight's lab results are reviewed.

Pause state lives in data/meta/scanner_state.json (the bot's /pause and
/resume write it; risk-guard breaches set it automatically).
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")

from specula import mtf, paper, runlog
from specula.data import is_equity, resample_ohlcv

STATE = Path("data/meta/scanner_state.json")
WF = Path("data/meta/walkforward.json")
POLL_SECONDS = 60
ARM_VALIDITY_SETUP_BARS = 2


def _load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"paused": False, "symbols": {}}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), encoding="utf-8")


def set_paused(paused: bool) -> None:
    s = _load_state()
    s["paused"] = paused
    _save_state(s)


def is_paused() -> bool:
    return bool(_load_state().get("paused"))


# ----------------------------------------------------------------- live bars

def crypto_bars(symbol: str, limit: int = 720) -> pd.DataFrame:
    url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
           f"&interval=1m&limit={limit}")
    with urllib.request.urlopen(url, timeout=20) as r:
        raw = json.loads(r.read())
    df = pd.DataFrame(
        [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]),
          float(k[5])] for k in raw],
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


def stock_bars(symbol: str) -> pd.DataFrame | None:
    key = os.environ.get("ALPACA_KEY_ID")
    sec = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        return None
    end = datetime.now(timezone.utc) - pd.Timedelta(minutes=16)
    start = end - pd.Timedelta(days=8)  # enough for SMA20 on a 1h setup TF
    params = urllib.parse.urlencode({
        "symbols": symbol, "timeframe": "1Min",
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "limit": 10000, "adjustment": "raw", "feed": "sip",
    })
    req = urllib.request.Request(
        f"https://data.alpaca.markets/v2/stocks/bars?{params}",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        bars = json.loads(r.read()).get("bars", {}).get(symbol, [])
    if not bars:
        return None
    df = pd.DataFrame([{
        "ts": b["t"], "open": b["o"], "high": b["h"], "low": b["l"],
        "close": b["c"], "volume": b["v"],
    } for b in bars])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


# ------------------------------------------------------------- cfg resolution

def resolve_cfg(row: dict) -> dict | None:
    """Roster row -> strategy cfg: explicit cfg, else walk-forward winner."""
    if row.get("cfg"):
        return row["cfg"]
    if not WF.exists():
        return None
    wf = json.loads(WF.read_text(encoding="utf-8"))
    for doc in wf.get("symbols", []):
        if doc["symbol"].split("·")[0] != row["symbol"]:
            continue
        for scen in doc["scenarios"][:1]:
            for fold in reversed(scen.get("folds", [])):
                if fold.get("winner_params"):
                    return fold["winner_params"]
    return None


# ------------------------------------------------------------- signal checks

def latest_setup_signal(cfg: dict, bars_1m: pd.DataFrame):
    """Most recent armed signal for fffd/didi cfgs on the live window.
    Returns (direction, trigger, stop, signal_ts) or None."""
    setup_df = resample_ohlcv(bars_1m, cfg["setup_tf"])
    if len(setup_df) < 30:
        return None
    if cfg["strategy"] == "fffd":
        sig, _, _, _ = mtf.fffd_setup_signals(
            setup_df, dev=cfg["dev"], strict=cfg["strict"])
    elif cfg["strategy"] == "didi":
        sig, _, _ = mtf.didi_setup_signals(
            setup_df, tol_bars=cfg.get("tol_bars", 1),
            adx_filter=cfg.get("adx_filter", False))
    else:
        return "unsupported"
    if sig.empty:
        return None
    last = sig.iloc[-1]
    ts = pd.Timestamp(last["ts"])
    tf = mtf.tf_delta(cfg["setup_tf"])
    age_bars = (setup_df.index[-1] - ts) / tf
    if age_bars > ARM_VALIDITY_SETUP_BARS:
        return None
    stop = last["stop"]
    if pd.isna(stop):  # didi has no structural stop -> percent from cfg
        stop = None
    return (int(last["dir"]), float(last["trigger"]),
            None if stop is None else float(stop), str(ts))


def band_exit_level(cfg: dict, bars_1m: pd.DataFrame, side: str) -> float | None:
    """Live Bollinger exit level for fffd band targets, from the last
    COMPLETED setup bar (the live bar is still forming)."""
    setup_df = resample_ohlcv(bars_1m, cfg["setup_tf"])
    close = setup_df["close"]
    if len(close) < 22:
        return None
    mid = close.rolling(20).mean()
    if cfg.get("target") == "midband":
        band = mid
    else:  # target "upper": long exits at upper band, short at lower
        sd = close.rolling(20).std(ddof=0)
        dev = cfg.get("dev", 2.0)
        band = mid + dev * sd if side == "long" else mid - dev * sd
    val = band.iloc[-2]
    return None if pd.isna(val) else float(val)


def scan_once(send) -> None:
    state = _load_state()
    roster = [r for r in runlog.autotrade_list() if r["enabled"]]
    if not roster:
        return

    for row in roster:
        symbol = row["symbol"]
        sym_state = state["symbols"].setdefault(symbol, {})
        try:
            bars = (stock_bars(symbol) if is_equity(symbol)
                    else crypto_bars(symbol))
            if bars is None or len(bars) < 120:
                continue
            price = float(bars["close"].iloc[-1])
            sym_state["last_price"] = price

            # manage open positions first (works even while paused)
            for pos in paper.open_positions(symbol):
                hit = None
                if pos["side"] == "long":
                    if pos["sl"] and price <= pos["sl"]:
                        hit = (pos["sl"], "stop-loss")
                    elif pos["tp"] and price >= pos["tp"]:
                        hit = (pos["tp"], "take-profit")
                else:
                    if pos["sl"] and price >= pos["sl"]:
                        hit = (pos["sl"], "stop-loss")
                    elif pos["tp"] and price <= pos["tp"]:
                        hit = (pos["tp"], "take-profit")
                # fffd band targets have no fixed tp — track the live band
                cfgp = pos.get("cfg") or {}
                if (hit is None and cfgp.get("strategy") == "fffd"
                        and cfgp.get("target") in ("midband", "upper")):
                    lvl = band_exit_level(cfgp, bars, pos["side"])
                    if lvl is not None:
                        if pos["side"] == "long" and price >= lvl:
                            hit = (lvl, "band-target")
                        elif pos["side"] == "short" and price <= lvl:
                            hit = (lvl, "band-target")
                # intraday only: stocks go flat before the close
                if hit is None and is_equity(symbol):
                    ny = datetime.now(NY)
                    if (ny.hour == 15 and ny.minute >= 55) or ny.hour >= 16:
                        hit = (price, "eod-flat")
                if hit:
                    closed = paper.close_position(pos["id"], hit[0], hit[1])
                    if closed:
                        send(f"[PAPER CLOSE] {symbol} {closed['side']} "
                             f"{closed['reason']} @ {closed['exit_price']:.4f} "
                             f"-> {closed['pnl_usd']:+.2f} USD "
                             f"({closed['pnl_pct']:+.2f}%)")

            if state.get("paused"):
                continue

            cfg = resolve_cfg(row)
            if cfg is None:
                continue
            res = latest_setup_signal(cfg, bars)
            if res == "unsupported":
                if not sym_state.get("warned_unsupported"):
                    sym_state["warned_unsupported"] = True
                    send(f"[SCANNER] {symbol}: strategy kind "
                         f"'{cfg.get('entry', {}).get('kind', cfg.get('strategy'))}' "
                         "not in scanner v1 — skipping")
                continue
            if res is None:
                continue
            direction, trigger, stop, sig_ts = res
            side = "long" if direction > 0 else "short"

            if sym_state.get("alerted_signal") != sig_ts:
                sym_state["alerted_signal"] = sig_ts
                sym_state["armed"] = {"ts": sig_ts, "trigger": trigger,
                                      "stop": stop, "side": side}
                send(f"[ARMED] {symbol} {side} ({cfg['strategy']} "
                     f"{cfg['setup_tf']}) trigger {trigger:.4f}"
                     + (f", stop {stop:.4f}" if stop else "")
                     + f", now {price:.4f}")

            armed = sym_state.get("armed")
            if armed and armed["ts"] == sig_ts and not sym_state.get(
                    f"fired_{sig_ts}"):
                crossed = (price > trigger if side == "long" else price < trigger)
                if crossed and is_equity(symbol):
                    # no fresh entries near the close (mirror the backtest's
                    # 15:45 cutoff; live stock data runs ~15 min delayed)
                    ny = datetime.now(NY)
                    if (ny.hour == 15 and ny.minute >= 30) or ny.hour >= 16:
                        crossed = False
                if crossed:
                    sym_state[f"fired_{sig_ts}"] = True
                    block = paper.risk_check()
                    if block:
                        send(f"[BLOCKED] {symbol} {side} trigger hit but: "
                             f"{block}. Scanner paused.")
                        state["paused"] = True
                        continue
                    sl_price = stop
                    if sl_price is None and cfg.get("sl"):
                        sl_price = (trigger * (1 - cfg["sl"]) if side == "long"
                                    else trigger * (1 + cfg["sl"]))
                    tp_price = None
                    if cfg.get("tp"):
                        tp_price = (trigger * (1 + cfg["tp"]) if side == "long"
                                    else trigger * (1 - cfg["tp"]))
                    elif cfg.get("target") in ("r1", "r2") and sl_price:
                        k = 1.0 if cfg["target"] == "r1" else 2.0
                        risk = abs(trigger - sl_price)
                        tp_price = (trigger + k * risk if side == "long"
                                    else trigger - k * risk)
                    pos = paper.open_position(symbol, side, trigger,
                                              row["size_usd"], sl_price,
                                              tp_price, cfg)
                    send(f"[PAPER OPEN] {symbol} {side} @ "
                         f"{pos['entry_price']:.4f} size "
                         f"${row['size_usd']:.0f}"
                         + (f", sl {sl_price:.4f}" if sl_price else "")
                         + (f", tp {tp_price:.4f}" if tp_price else ""))
        except Exception as e:
            print(f"[scanner] {symbol}: {type(e).__name__}: {e}", flush=True)

    _save_state(state)


def run_loop(send) -> None:
    print("[scanner] started", flush=True)
    while True:
        try:
            scan_once(send)
        except Exception as e:
            print(f"[scanner] cycle error: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_SECONDS)
