"""League Explorer — endless setup discovery, one round at a time.

Each round: sample a batch of NEVER-TRIED configs (70% mutations of the
current leaderboard, 30% random draws across every strategy family incl.
trailing-stop exits), re-evaluate the current leaders + favourites so their
post-discovery stats stay fresh, run everything on ALL assets, and merge
into the League store. New configs land in "To review" automatically.

Control: data/meta/explorer_state.json {"state": "run"|"pause"} — set from
the League page. The loop checks it between rounds; "pause" exits cleanly
after the current round. The explorer also yields around the nightly update
(21:50–23:59 Melbourne) and the Saturday lab (00:45–01:15); the server's
keeper relaunches it afterwards while state stays "run".

Run: uv run python scripts/league_explorer.py [--batch 200] [--once]
"""

import argparse
import copy
import json
import random
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

STATE = Path("data/meta/explorer_state.json")
MEL = ZoneInfo("Australia/Melbourne")

TF_PAIRS = [("5min", "5min"), ("15min", "5min"), ("15min", "15min"),
            ("30min", "15min"), ("1h", "15min"), ("1h", "30min"),
            ("2h", "30min"), ("4h", "1h")]

EXITS = (
    [{"kind": "fixed_r", "sl": sl, "tp": tp}
     for sl in (0.005, 0.01, 0.015) for tp in (0.005, 0.01, 0.02)]
    + [{"kind": "trail", "sl": d} for d in (0.005, 0.0075, 0.01, 0.015)]
    + [{"kind": "time", "max_bars": n, "sl": 0.01} for n in (24, 48)]
)

MA_FAST = [5, 8, 13, 21, 34, 55]
MA_SLOW = [21, 34, 55, 89, 144, 200]


def rand_entry(rng: random.Random) -> dict:
    kind = rng.choice(["ma_cross", "donchian", "boll", "macd", "mom",
                       "vwap", "orb", "rsi_cross"])
    if kind == "ma_cross":
        f = rng.choice(MA_FAST)
        s = rng.choice([x for x in MA_SLOW if x > f])
        return {"kind": kind, "ma_type": rng.choice(["sma", "ema"]),
                "fast": f, "slow": s}
    if kind == "donchian":
        return {"kind": kind, "window": rng.choice([10, 20, 40, 55])}
    if kind == "boll":
        return {"kind": kind, "window": 20, "dev": rng.choice([2.0, 2.5]),
                "mode": rng.choice(["trend", "revert"])}
    if kind == "macd":
        return {"kind": kind, **rng.choice([
            {"fast": 12, "slow": 26, "signal": 9},
            {"fast": 5, "slow": 35, "signal": 5}])}
    if kind == "mom":
        return {"kind": kind, "window": rng.choice([10, 20]),
                "thr": rng.choice([0.005, 0.01])}
    if kind == "vwap":
        return {"kind": kind, "mode": rng.choice(["revert", "cross"]),
                "band_k": rng.choice([1.5, 2.0, 2.5])}
    if kind == "orb":
        return {"kind": kind, "range_min": rng.choice([15, 30, 60])}
    return {"kind": "rsi_cross", "window": rng.choice([7, 14]),
            **rng.choice([{"lo": 20, "hi": 80}, {"lo": 30, "hi": 70}])}


def rand_config(rng: random.Random) -> dict:
    roll = rng.random()
    if roll < 0.72:  # lab families
        setup_tf, exec_tf = rng.choice(TF_PAIRS)
        entry = rand_entry(rng)
        if entry["kind"] in ("vwap", "orb"):  # exec-frame signals
            setup_tf = exec_tf
        return {"strategy": "lab", "setup_tf": setup_tf, "exec_tf": exec_tf,
                "entry": entry, "exit": copy.deepcopy(rng.choice(EXITS))}
    if roll < 0.9:  # fffd (incl. trailing targets)
        setup_tf, exec_tf = rng.choice(TF_PAIRS)
        target = rng.choice(["midband", "upper", "r1", "r2", "trail"])
        cfg = {"strategy": "fffd", "setup_tf": setup_tf, "exec_tf": exec_tf,
               "dev": rng.choice([1.5, 2.0, 2.5, 3.0]),
               "strict": rng.random() < 0.5, "target": target}
        if target == "trail":
            cfg["trail"] = rng.choice([0.005, 0.01, 0.015, "structural"])
        return cfg
    setup_tf, exec_tf = rng.choice(TF_PAIRS)
    return {"strategy": "didi", "setup_tf": setup_tf, "exec_tf": exec_tf,
            "tol_bars": rng.choice([1, 2]),
            "adx_filter": rng.random() < 0.5,
            "sl": rng.choice([0.005, 0.01]), "tp": rng.choice([0.005, 0.01])}


def mutate(params: dict, rng: random.Random) -> dict:
    p = copy.deepcopy(params)
    if p.get("strategy") == "lab":
        op = rng.random()
        if op < 0.4:
            p["exit"] = copy.deepcopy(rng.choice(EXITS))
        elif op < 0.7:
            p["entry"] = {**rand_entry(rng)}
            if p["entry"]["kind"] != params.get("entry", {}).get("kind"):
                # keep the family, resample its params only
                p["entry"] = rand_entry(rng)
        else:
            p["setup_tf"], p["exec_tf"] = rng.choice(TF_PAIRS)
            if p.get("entry", {}).get("kind") in ("vwap", "orb"):
                p["setup_tf"] = p["exec_tf"]
    else:
        fresh = rand_config(rng)
        if fresh.get("strategy") == params.get("strategy"):
            p = fresh
        else:
            p["setup_tf"], p["exec_tf"] = rng.choice(TF_PAIRS)
    return p


def telegram(text: str) -> None:
    import os

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data,
            timeout=10)
    except Exception:
        pass


def state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"state": "pause", "round": 0}


def save_state(**updates) -> dict:
    st = {**state(), **updates,
          "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st), encoding="utf-8")
    return st


def in_yield_window() -> str | None:
    now = datetime.now(MEL)
    if now.hour == 21 and now.minute >= 50 or now.hour in (22, 23):
        return "nightly update window (21:50–24:00 Melbourne)"
    if now.weekday() == 5 and now.hour == 0 and now.minute >= 45:
        return "weekly lab window (Sat 00:45+ Melbourne)"
    return None


def one_round(batch: int, rng: random.Random) -> None:
    from specula import league, runlog

    store = league.load_store()
    known = {r["sig"]: r for r in (store or {}).get("configs", [])}
    fseen = league.first_seen_map()

    # leaders + favourites re-evaluate every round → post-discovery stays live
    leaders = sorted(
        (r for r in known.values() if r.get("rank")),
        key=lambda r: r["rank"])[:40]
    configs = {r["sig"]: {"sig": r["sig"], "label": r["label"],
                          "params": r["params"], "source": r["source"]}
               for r in leaders}
    keep_sigs = set()
    for f in runlog.fav_setups_list():
        if not f["params"]:
            continue
        sig = league.sig_of(f["params"])
        keep_sigs.add(sig)
        params = {k: v for k, v in f["params"].items()
                  if k not in ("symbol", "fee")}
        configs[sig] = {"sig": sig, "label": f["label"] or league.label_of(params),
                        "params": params, "source": "favourite"}

    # fresh batch: mutations of leaders + random exploration, never repeated
    tried = set(known) | set(configs)
    leader_params = [r["params"] for r in leaders] or None
    added, attempts = 0, 0
    while added < batch and attempts < batch * 60:
        attempts += 1
        if leader_params and rng.random() < 0.7:
            params = mutate(rng.choice(leader_params), rng)
            source = "explorer (mutation)"
        else:
            params = rand_config(rng)
            source = "explorer (random)"
        sig = league.sig_of(params)
        if sig in tried:
            continue
        tried.add(sig)
        configs[sig] = {"sig": sig, "label": league.label_of(params),
                        "params": params, "source": source}
        added += 1

    st = state()
    n_round = st.get("round", 0) + 1
    prev_best = max((r.get("hold_pf") or 0 for r in known.values()
                     if r.get("eligible")), default=0)
    print(f"[round {n_round}] {added} new configs + "
          f"{len(configs) - added} re-evaluated leaders/favourites",
          flush=True)

    rows, meta = league.evaluate(
        list(configs.values()), first_seen_map=fseen,
        registry_keep_sigs=keep_sigs)
    if rows:
        league.merge_store(rows, meta)

    save_state(round=n_round)
    fresh = [r for r in rows if r["sig"] not in known]
    best_new = max((r for r in fresh if r["eligible"] and r["hold_pf"]),
                   key=lambda r: r["hold_pf"], default=None)
    if best_new:
        print(f"[round {n_round}] best new: hold PF {best_new['hold_pf']:.2f} "
              f"({best_new['hold_trades']} trades) — {best_new['label']}",
              flush=True)
        if best_new["hold_pf"] > max(prev_best, 1.1):
            telegram(f"🔭 Explorer round {n_round}: new leaderboard entry — "
                     f"{best_new['label']} · holdout PF "
                     f"{best_new['hold_pf']:.2f} over "
                     f"{best_new['hold_trades']} trades "
                     f"(${best_new['hold_pnl_usd']:.0f}). Review it on the "
                     f"League page.")
    print(f"[round {n_round}] done", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--once", action="store_true",
                    help="run a single round regardless of state")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    if args.once:
        one_round(args.batch, rng)
        return

    save_state(state="run")
    while True:
        st = state()
        if st.get("state") != "run":
            print("[explorer] paused — exiting cleanly", flush=True)
            return
        window = in_yield_window()
        if window:
            print(f"[explorer] yielding to {window} — the keeper restarts "
                  f"me afterwards", flush=True)
            return
        one_round(args.batch, rng)


if __name__ == "__main__":
    main()
