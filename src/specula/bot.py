"""Telegram interface to Specula: structured commands + Claude-powered Q&A.

Structured commands (/status, /best, /wf, /jobs) are answered directly from
the registry and result files — fast and free. Free-text questions go to
Claude Haiku with read-only tools over the same data. The bot only talks to
TELEGRAM_CHAT_ID; anyone else is refused. It never executes trades or jobs —
actions stay on the portal and, later, explicit commands.
"""

import json
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from anthropic import beta_tool

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Australia/Melbourne")
except Exception:  # tzdata missing — fall back to UTC display
    LOCAL_TZ = timezone.utc


def local_time(ts_iso: str | None) -> str:
    """UTC ISO timestamp -> Melbourne local display."""
    if not ts_iso:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_iso)
        return dt.astimezone(LOCAL_TZ).strftime("%a %d %b %H:%M")
    except Exception:
        return ts_iso


DB = Path("data/meta/registry.sqlite")
WF = Path("data/meta/walkforward.json")
API = "http://127.0.0.1:8756"
MODEL = "claude-haiku-4-5"

_client: anthropic.Anthropic | None = None
_tg_token = ""
_chat_id = ""


# --------------------------------------------------------------- data access

def _label(params: dict) -> str:
    s = params.get("strategy")
    if s == "lab":
        e = params.get("entry", {})
        x = params.get("exit", {})
        bits = [e.get("kind", "?")] + [
            f"{k}={v}" for k, v in e.items() if k != "kind"
        ] + [f"exit:{x.get('kind')}"]
        return " ".join(str(b) for b in bits)
    if s == "didi":
        return (f"Didi {params.get('setup_tf')}->{params.get('exec_tf')} "
                f"sl{params.get('sl')} tp{params.get('tp')}"
                + (" ADX" if params.get("adx_filter") else ""))
    if s == "fffd":
        return (f"FFFD {params.get('setup_tf')}->{params.get('exec_tf')} "
                f"{'strict' if params.get('strict') else 'loose'} "
                f"dev{params.get('dev')} {params.get('target')}")
    return json.dumps(params)[:60]


@beta_tool
def get_system_status() -> str:
    """Current system status: job API health, running/recent jobs, registry
    size, and freshness timestamps of walk-forward results and data files."""
    out = {}
    try:
        with urllib.request.urlopen(f"{API}/api/jobs", timeout=3) as r:
            jobs = json.loads(r.read())
        out["api"] = "up"
        out["running_jobs"] = [j["label"] for j in jobs if j["status"] == "running"]
        out["recent_jobs"] = [f"{j['label']}: {j['status']}" for j in jobs[:3]]
    except Exception:
        out["api"] = "down (portal API not running)"
    if DB.exists():
        con = sqlite3.connect(DB)
        out["registry_runs"] = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        out["last_run_logged"] = con.execute(
            "SELECT MAX(created_at) FROM runs").fetchone()[0]
        con.close()
    if WF.exists():
        wf = json.loads(WF.read_text(encoding="utf-8"))
        out["walkforward_generated"] = wf.get("generated_at")
        out["walkforward_symbols"] = len(wf.get("symbols", []))
    out["now_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["now_melbourne"] = datetime.now(LOCAL_TZ).strftime("%a %d %b %H:%M")
    return json.dumps(out)


@beta_tool
def best_setups(symbol: str = "", top: int = 5, min_trades: int = 30) -> str:
    """Best backtested setups from the registry, ranked by profit factor.
    These are IN-SAMPLE results. Args: symbol (empty = all assets), top
    (number of rows), min_trades (minimum trade count filter)."""
    if not DB.exists():
        return "no registry yet"
    con = sqlite3.connect(DB)
    q = ("SELECT symbol, params, n_trades, win_rate_pct, profit_factor, "
         "total_return_pct, max_dd_pct, sweep_tag FROM runs "
         "WHERE n_trades >= ? AND profit_factor IS NOT NULL")
    args: list = [min_trades]
    if symbol:
        q += " AND symbol = ?"
        args.append(symbol.upper())
    q += " ORDER BY profit_factor DESC LIMIT ?"
    args.append(int(top))
    rows = []
    for r in con.execute(q, args):
        p = json.loads(r[1])
        rows.append({
            "symbol": r[0], "setup": _label(p), "fee": p.get("fee"),
            "n_trades": r[2], "win_pct": r[3], "profit_factor": r[4],
            "return_pct": r[5], "max_dd_pct": r[6], "sweep": r[7],
        })
    con.close()
    return json.dumps({"note": "in-sample results", "rows": rows})


@beta_tool
def walkforward_results(symbol: str = "") -> str:
    """Out-of-sample walk-forward verdicts — the trustworthy performance
    numbers. Args: symbol (empty = summary of all symbols)."""
    if not WF.exists():
        return "no walk-forward results yet"
    wf = json.loads(WF.read_text(encoding="utf-8"))
    docs = wf.get("symbols", [])
    if symbol:
        docs = [d for d in docs if d["symbol"].upper().startswith(symbol.upper())]
    out = []
    for d in docs:
        for s in d["scenarios"]:
            a = s["aggregate"]
            out.append({
                "symbol": d["symbol"], "fee": s["fee"],
                "oos_trades": a["oos_trades"], "oos_pf": a["oos_pf"],
                "oos_win_pct": a["oos_win_rate_pct"],
                "oos_return_pct": a["oos_return_pct"],
            })
    return json.dumps({"note": "out-of-sample (walk-forward)",
                       "generated_at": wf.get("generated_at"), "rows": out})


@beta_tool
def registry_overview() -> str:
    """Registry composition: runs per sweep stage and per asset class."""
    if not DB.exists():
        return "no registry yet"
    con = sqlite3.connect(DB)
    by_tag = dict(con.execute(
        "SELECT sweep_tag, COUNT(*) FROM runs GROUP BY sweep_tag").fetchall())
    symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM runs")]
    con.close()
    crypto = [s for s in symbols if s.endswith(("USDT", "USDC"))]
    return json.dumps({
        "runs_by_sweep": by_tag,
        "n_symbols": len(symbols),
        "crypto_symbols": len(crypto),
        "stock_symbols": len(symbols) - len(crypto),
    })


# step estimates (minutes) for the overnight lab — rough, for progress display
LAB_STEPS = [
    ("equity bronze/silver", 25), ("quality report", 10), ("MA megasweep", 45),
    ("lab coarse", 60), ("scoring", 3), ("lab refine", 45),
    ("walk-forward (candidates)", 40), ("equity curves", 15), ("web export", 3),
]


def _progress_text() -> str:
    try:
        with urllib.request.urlopen(f"{API}/api/jobs", timeout=5) as r:
            jobs = json.loads(r.read())
    except Exception:
        return "portal API is not reachable"
    running = [j for j in jobs if j["status"] == "running"]
    if not running:
        last = jobs[0] if jobs else None
        return (f"no job running. last: {last['label']} -> {last['status']}"
                if last else "no job running and none launched this session")
    j = running[0]
    try:
        with urllib.request.urlopen(f"{API}/api/jobs/{j['id']}?tail=500",
                                    timeout=5) as r:
            log = json.loads(r.read()).get("log_tail", "")
    except Exception:
        log = ""

    est = dict(LAB_STEPS)
    total_est = sum(est.values())
    done, done_minutes = [], 0.0
    current = None
    for line in log.splitlines():
        if line.startswith("====="):
            name = line.strip("= ").rsplit(" (cap", 1)[0].strip()
            current = name
        for pre in ("[ok] ", "[FAIL] ", "[timeout] "):
            if line.startswith(pre):
                name = line[len(pre):].rsplit(" (", 1)[0].strip()
                done.append(name)
                try:
                    done_minutes += float(line.rsplit("(", 1)[1].split(" min")[0])
                except Exception:
                    pass
    if current in done:
        current = None
    if not est or j["type"] != "overnight_lab":
        started = datetime.fromisoformat(j["started_at"])
        mins = (datetime.now(timezone.utc) - started).total_seconds() / 60
        return f"{j['label']}: running for {mins:.0f} min (no step estimates)"

    completed_est = sum(est.get(d, 5) for d in done)
    started = datetime.fromisoformat(j["started_at"])
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 60
    cur_est = est.get(current, 10) if current else 0
    cur_elapsed = min(max(0.0, elapsed - done_minutes), 0.95 * cur_est)
    frac = min(0.99, (completed_est + cur_elapsed) / total_est)
    remaining = max(1, total_est * (1 - frac))
    bar = "#" * round(frac * 12)
    step_n = len(done) + (1 if current else 0)
    lines = [
        j["label"],
        f"[{bar:<12}] {frac * 100:.0f}% (rough estimate)",
        f"step {step_n}/{len(LAB_STEPS)}: {current or 'finishing'}",
        f"elapsed {elapsed / 60:.1f}h, ~{remaining / 60:.1f}h remaining",
    ]
    return "\n".join(lines)


@beta_tool
def job_progress() -> str:
    """Progress of the currently running background job: percent complete,
    current pipeline step, elapsed time, and rough time remaining."""
    return _progress_text()


TOOLS = [get_system_status, best_setups, walkforward_results,
         registry_overview, job_progress]

SYSTEM = """You are Specula's assistant on Telegram. Specula is a private \
trading-strategy research system: a 1-minute bar data lake (top-20 crypto \
pairs since Jul 2025 + S&P 500 stocks), a backtest registry, and a discovery \
funnel (broad sweeps -> exit refinement -> filters -> walk-forward validation).

Rules:
- Use the tools to answer with real data; never invent numbers.
- Always distinguish IN-SAMPLE results (registry/best_setups) from \
OUT-OF-SAMPLE walk-forward results, and remind the user OOS is what counts.
- Be concise: short paragraphs or dash lists, plain text (no markdown tables, \
no headers), suitable for a phone screen.
- You are read-only for actions: if asked to trade, run jobs, or change \
anything, say that actions happen via the portal, not chat.
- You DO automatically push a Telegram alert whenever a background job \
finishes (done or failed). If the user asks to be notified when work \
completes, confirm it will happen automatically — no need to check back.
- Fee context: crypto fees 0.04%/0.10% per side; stocks 0.01%/0.05%.
- The user is in Melbourne, Australia (Australia/Melbourne, UTC+10/+11). \
Stored timestamps are UTC — always convert to Melbourne local time when \
presenting them (get_system_status returns now_utc and now_melbourne to \
anchor the conversion)."""


# ------------------------------------------------------------------ telegram

def tg(method: str, **params) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{_tg_token}/{method}",
                                 data=data)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def send(text: str) -> None:
    for i in range(0, len(text), 3900):
        tg("sendMessage", chat_id=_chat_id, text=text[i:i + 3900])


# ------------------------------------------------------------------ handlers

def handle_command(cmd: str) -> str:
    if cmd.startswith("/progress"):
        return _progress_text()
    if cmd.startswith("/status"):
        s = json.loads(get_system_status.call({}))
        lines = [f"API: {s.get('api')}"]
        if s.get("running_jobs"):
            lines.append("running: " + ", ".join(s["running_jobs"]))
        lines += [
            f"registry: {s.get('registry_runs', 0):,} runs "
            f"(last {local_time(s.get('last_run_logged'))})",
            f"walk-forward: {s.get('walkforward_symbols', 0)} symbols, "
            f"generated {local_time(s.get('walkforward_generated'))}",
            f"local time: {s.get('now_melbourne')}",
        ]
        return "\n".join(lines)
    if cmd.startswith("/best"):
        arg = cmd.split(maxsplit=1)[1].strip() if " " in cmd else ""
        data = json.loads(best_setups.call({"symbol": arg, "top": 5}))
        lines = [f"Top setups{' for ' + arg.upper() if arg else ''} (in-sample):"]
        for r in data["rows"]:
            lines.append(f"- {r['symbol']} {r['setup']} | PF {r['profit_factor']} "
                         f"| {r['n_trades']} trades | {r['return_pct']}%")
        return "\n".join(lines) if data["rows"] else "no qualifying setups"
    if cmd.startswith("/wf"):
        arg = cmd.split(maxsplit=1)[1].strip() if " " in cmd else ""
        data = json.loads(walkforward_results.call({"symbol": arg}))
        if isinstance(data, str):
            return data
        lines = ["Walk-forward (out-of-sample, low fee):"]
        seen = set()
        for r in data["rows"]:
            if r["symbol"] in seen:
                continue
            seen.add(r["symbol"])
            lines.append(f"- {r['symbol']}: PF {r['oos_pf']}, "
                         f"{r['oos_return_pct']}% over {r['oos_trades']} trades")
        return "\n".join(lines)
    return ("Commands: /status /progress /best [symbol] /wf [symbol] /help\n"
            "Or just ask me anything about the system in plain language.")


def answer_question(question: str) -> str:
    runner = _client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=1000,
        system=SYSTEM,
        tools=TOOLS,
        messages=[{"role": "user", "content": question}],
        max_iterations=8,
    )
    last = None
    for message in runner:
        last = message
    if last is None:
        return "no answer produced"
    text = "".join(b.text for b in last.content if b.type == "text")
    return text.strip() or "no answer produced"


def _job_watcher() -> None:
    """Push an alert whenever a portal job transitions out of 'running'."""
    seen: dict[str, str] = {}
    first = True
    while True:
        try:
            with urllib.request.urlopen(f"{API}/api/jobs", timeout=5) as r:
                jobs = json.loads(r.read())
            for j in jobs:
                prev = seen.get(j["id"])
                seen[j["id"]] = j["status"]
                if first or prev != "running" or j["status"] == "running":
                    continue
                mark = "DONE" if j["status"] == "done" else "FAILED"
                dur = ""
                if j.get("started_at") and j.get("finished_at"):
                    secs = (datetime.fromisoformat(j["finished_at"])
                            - datetime.fromisoformat(j["started_at"])).total_seconds()
                    dur = f" in {secs / 3600:.1f}h" if secs > 5400 else f" in {secs / 60:.0f}min"
                send(f"[{mark}] {j['label']}{dur}. Ask me for the results or send /status.")
                print(f"[bot] alerted: {j['label']} -> {j['status']}", flush=True)
            first = False
        except Exception:
            pass
        time.sleep(60)


def run(token: str, chat_id: str, client: anthropic.Anthropic) -> None:
    global _client, _tg_token, _chat_id
    _client, _tg_token, _chat_id = client, token, str(chat_id)
    threading.Thread(target=_job_watcher, daemon=True).start()
    offset = 0
    print("[bot] polling for messages, job watcher active", flush=True)
    while True:
        try:
            updates = tg("getUpdates", offset=offset, timeout=50)
        except Exception as e:
            print(f"[bot] poll error: {e}", flush=True)
            time.sleep(5)
            continue
        for u in updates.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            if str(msg.get("chat", {}).get("id")) != _chat_id:
                continue  # not the owner — ignore silently
            print(f"[bot] <- {text[:80]}", flush=True)
            try:
                reply = (handle_command(text) if text.startswith("/")
                         else answer_question(text))
            except Exception as e:
                reply = f"error: {type(e).__name__}: {e}"
            send(reply)
