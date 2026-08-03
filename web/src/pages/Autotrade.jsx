import { useEffect, useMemo, useState } from 'react'
import { Pf } from '../components/bits.jsx'
import { num } from '../data.js'

const Money = ({ v }) => v == null ? '—' : (
  <span className={v >= 0 ? 'pos' : 'neg'}>
    {v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)}
  </span>
)

const fmtNY = (iso) => iso ? new Date(iso).toLocaleString('en-AU', {
  timeZone: 'America/New_York', day: '2-digit', month: 'short',
  hour: '2-digit', minute: '2-digit', hour12: false,
}) : '—'

// live New York clock + market-open countdown (regular session 09:30–16:00;
// US holidays not modelled — on holidays the scanner simply sees no bars)
function MarketClock() {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const p = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hourCycle: 'h23', weekday: 'short',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date(now))
    .reduce((o, x) => ((o[x.type] = x.value), o), {})
  const daySec = (+p.hour) * 3600 + (+p.minute) * 60 + (+p.second)
  const WD = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 }
  const wd = WD[p.weekday]
  const OPEN = 9.5 * 3600
  const CLOSE = 16 * 3600
  const isWeekday = wd <= 4

  let state, delta
  if (isWeekday && daySec >= OPEN && daySec < CLOSE) {
    state = 'open'
    delta = CLOSE - daySec
  } else if (isWeekday && daySec < OPEN) {
    state = 'preopen'
    delta = OPEN - daySec
  } else {
    state = 'closed'
    const fullDays = wd === 4 ? 2 : wd === 5 ? 1 : 0 // Fri→Sat+Sun, Sat→Sun
    delta = (86400 - daySec) + fullDays * 86400 + OPEN
  }
  const hh = Math.floor(delta / 3600)
  const mm = Math.floor((delta % 3600) / 60)
  const ss = delta % 60
  const cd = `${hh}h ${String(mm).padStart(2, '0')}m ${String(ss).padStart(2, '0')}s`

  const nyClock = new Date(now).toLocaleTimeString('en-GB', {
    timeZone: 'America/New_York', hour12: false,
  })
  const nyDate = new Date(now).toLocaleDateString('en-AU', {
    timeZone: 'America/New_York', weekday: 'long', day: 'numeric',
    month: 'short',
  })

  return (
    <div className="card">
      <div style={{
        display: 'flex', gap: 24, alignItems: 'baseline', flexWrap: 'wrap',
      }}>
        <span>
          <span className="hint">New York · {nyDate} · </span>
          <b style={{ fontSize: 26, fontVariantNumeric: 'tabular-nums' }}>
            {nyClock}</b>
        </span>
        {state === 'open' && (
          <span><span className="badge done">MARKET OPEN</span>{' '}
            <span className="hint">closes in</span>{' '}
            <b style={{ fontVariantNumeric: 'tabular-nums' }}>{cd}</b></span>
        )}
        {state === 'preopen' && (
          <span><span className="badge running">PRE-MARKET</span>{' '}
            <span className="hint">opens in</span>{' '}
            <b style={{ fontVariantNumeric: 'tabular-nums' }}>{cd}</b></span>
        )}
        {state === 'closed' && (
          <span><span className="badge failed">CLOSED</span>{' '}
            <span className="hint">opens in</span>{' '}
            <b style={{ fontVariantNumeric: 'tabular-nums' }}>{cd}</b></span>
        )}
        <span className="hint">
          scanner trades 09:30–15:30 NY · flat by 15:55
        </span>
      </div>
    </div>
  )
}

// live paper-trading results: daily balance + full trade history
function PaperHistory() {
  const [doc, setDoc] = useState(null)
  const [sym, setSym] = useState('all')
  const [setup, setSetup] = useState('all')

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const r = await fetch('/api/paper')
        if (r.ok && alive) setDoc(await r.json())
      } catch { /* offline */ }
    }
    load()
    const t = setInterval(load, 30000) // live view during the session
    return () => { alive = false; clearInterval(t) }
  }, [])

  const symbols = useMemo(
    () => [...new Set((doc?.trades || []).map((t) => t.symbol))].sort(),
    [doc])
  const setups = useMemo(
    () => [...new Set((doc?.trades || []).map((t) => t.setup))].sort(),
    [doc])
  const rows = useMemo(() => (doc?.trades || []).filter((t) =>
    (sym === 'all' || t.symbol === sym)
    && (setup === 'all' || t.setup === setup)), [doc, sym, setup])

  if (!doc) return null
  const s = doc.summary

  return (
    <>
      <div className="card">
        <h3>Paper trading — live results{' '}
          <span className="hint">(refreshes every 30s · times are New
            York)</span></h3>
        <div className="tiles">
          <div className="tile"><div className="k">Open positions</div>
            <div className="v">{s.open}</div></div>
          <div className="tile"><div className="k">Closed trades</div>
            <div className="v">{s.closed}</div>
            <div className="d">{s.wins} winners</div></div>
          <div className="tile"><div className="k">Total P&L</div>
            <div className="v"><Money v={s.total_pnl} /></div></div>
          <div className="tile"><div className="k">Win rate</div>
            <div className="v">{s.closed
              ? `${(100 * s.wins / s.closed).toFixed(0)}%` : '—'}</div></div>
        </div>
        {s.days.length > 0 && (
          <table className="grid" style={{ marginTop: 10 }}>
            <thead>
              <tr><th className="txt">Session (NY)</th><th>Trades</th>
                <th>Wins</th><th>Day P&L</th></tr>
            </thead>
            <tbody>
              {s.days.map((d) => (
                <tr key={d.date}>
                  <td className="txt">{d.date}</td>
                  <td>{d.trades}</td>
                  <td>{d.wins}/{d.trades}</td>
                  <td><Money v={d.pnl} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {(() => {
        const open = (doc.trades || []).filter((t) => t.status === 'open')
        if (!open.length) return null
        return (
          <div className="card">
            <h3>Open positions — live progress{' '}
              <span className="hint">(prices ~15 min delayed)</span></h3>
            <div style={{ overflowX: 'auto' }}>
              <table className="grid">
                <thead>
                  <tr>
                    <th className="txt">Opened (NY)</th>
                    <th className="txt">Symbol</th>
                    <th className="txt">Setup</th>
                    <th className="txt">Side</th>
                    <th>Size $</th>
                    <th>Entry px</th>
                    <th>Now</th>
                    <th>Unreal P&L $</th>
                    <th>Unreal %</th>
                    <th>Stop</th>
                    <th className="txt">Target</th>
                  </tr>
                </thead>
                <tbody>
                  {open.map((t) => {
                    const stopDist = t.sl && t.last_price
                      ? Math.abs(100 * (t.last_price - t.sl) / t.last_price)
                      : null
                    return (
                      <tr key={t.id}>
                        <td className="txt">{fmtNY(t.entry_ts)}</td>
                        <td className="txt"><b>{t.symbol}</b></td>
                        <td className="txt hint" style={{
                          maxWidth: 280, whiteSpace: 'normal',
                        }}>{t.setup}</td>
                        <td className="txt">{t.side}</td>
                        <td>{num(t.qty * t.entry_price, 0)}</td>
                        <td>{num(t.entry_price, 4)}</td>
                        <td>{t.last_price ? num(t.last_price, 4) : '—'}</td>
                        <td><Money v={t.unreal_usd} /></td>
                        <td>{t.unreal_pct != null
                          ? <span className={t.unreal_pct >= 0 ? 'pos' : 'neg'}>
                            {num(t.unreal_pct, 2)}%</span>
                          : '—'}</td>
                        <td>{t.sl ? `${num(t.sl, 4)}${stopDist != null
                          ? ` (${num(stopDist, 2)}% away)` : ''}` : '—'}</td>
                        <td className="txt hint">{t.tp
                          ? num(t.tp, 4) : 'midband (dynamic)'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )
      })()}

      <div className="card">
        <h3>Trade history</h3>
        <div className="filters">
          <label>asset
            <select value={sym} onChange={(e) => setSym(e.target.value)}>
              <option value="all">All ({symbols.length})</option>
              {symbols.map((x) => <option key={x}>{x}</option>)}
            </select>
          </label>
          <label>setup
            <select value={setup} onChange={(e) => setSetup(e.target.value)}>
              <option value="all">All</option>
              {setups.map((x) => <option key={x}>{x}</option>)}
            </select>
          </label>
          <span className="hint">{rows.length} trades</span>
        </div>
        {rows.length === 0 ? (
          <p className="hint">no paper trades yet — they appear here the
            moment the scanner opens one</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="grid">
              <thead>
                <tr>
                  <th className="txt">Entry (NY)</th>
                  <th className="txt">Symbol</th>
                  <th className="txt">Setup</th>
                  <th className="txt">Side</th>
                  <th>Size $</th>
                  <th>Entry px</th>
                  <th className="txt">Exit (NY)</th>
                  <th>Exit px</th>
                  <th className="txt">Reason</th>
                  <th>P&L $</th>
                  <th>P&L %</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 300).map((t) => (
                  <tr key={t.id}>
                    <td className="txt">{fmtNY(t.entry_ts)}</td>
                    <td className="txt"><b>{t.symbol}</b></td>
                    <td className="txt hint" style={{
                      maxWidth: 320, whiteSpace: 'normal',
                    }}>{t.setup}</td>
                    <td className="txt">{t.side}</td>
                    <td>{num(t.qty * t.entry_price, 0)}</td>
                    <td>{num(t.entry_price, 4)}</td>
                    <td className="txt">{t.status === 'open'
                      ? <span className="badge running">open</span>
                      : fmtNY(t.exit_ts)}</td>
                    <td>{t.exit_price != null ? num(t.exit_price, 4) : '—'}</td>
                    <td className="txt hint">{t.exit_reason ?? '—'}</td>
                    <td><Money v={t.pnl_usd} /></td>
                    <td>{t.pnl_pct != null
                      ? <span className={t.pnl_pct >= 0 ? 'pos' : 'neg'}>
                        {num(t.pnl_pct, 2)}%</span>
                      : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}

function SettingsCard() {
  const [s, setS] = useState(null)
  const [saved, setSaved] = useState(null)

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/settings')
        if (r.ok) setS(await r.json())
      } catch { /* offline */ }
    })()
  }, [])

  async function save() {
    setSaved(null)
    const r = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s),
    })
    const d = await r.json()
    setSaved(r.ok ? { ok: true, text: 'saved — applies to new backtests, curves and paper trades' }
      : { ok: false, text: d?.detail || 'save failed' })
  }

  if (!s) return null
  const field = (key, label, step) => (
    <label>{label}
      <input type="number" step={step} value={s[key]}
        onChange={(e) => setS({ ...s, [key]: Number(e.target.value) })} />
    </label>
  )
  return (
    <div className="card">
      <h3>Backtest &amp; trading settings</h3>
      <div className="filters">
        {field('fee_crypto_pct', 'crypto fee %/side (Binance)', 0.01)}
        {field('fee_stock_pct', 'stock fee %/side (IBKR)', 0.005)}
        {field('capital_usd', 'capital (USD)', 1000)}
        {field('trade_size_crypto_usd', 'crypto size/trade (USD)', 50)}
        {field('trade_size_stock_usd', 'stock size/trade (USD)', 100)}
        <button className="btn" onClick={save}>Save</button>
      </div>
      <p className="hint">
        Venues: Binance for crypto, Interactive Brokers for stocks. Backtests
        run two fee scenarios: your fee and a 2.5× stressed fee (the two PF
        columns across the portal). Nightly reprocessing, sweeps, curve and
        trigger views all pick these up; already-logged registry rows keep
        the fee they were run with. Size 0 = invest full capital per trade.
      </p>
      {saved && <p className={saved.ok ? 'pos' : 'neg'}>{saved.text}</p>}
    </div>
  )
}

// Which assets trade automatically (paper mode), each on its validated
// best strategy. Enabling requires a positive out-of-sample verdict.
export default function Autotrade() {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState(null)
  const [symbol, setSymbol] = useState('')
  const [size, setSize] = useState(1000)
  const [msg, setMsg] = useState(null)

  async function load() {
    try {
      const r = await fetch('/api/autotrade')
      if (!r.ok) throw new Error(r.status === 404
        ? 'endpoint not available yet — arrives with the server cutover'
        : `API error ${r.status}`)
      setRows(await r.json())
      setErr(null)
    } catch (e) {
      setErr(String(e.message || e))
    }
  }

  useEffect(() => { load() }, [])

  async function save(sym, enabled, sizeUsd, force = false) {
    setMsg(null)
    const r = await fetch('/api/autotrade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sym, enabled, size_usd: sizeUsd, force }),
    })
    if (!r.ok) {
      const detail = (await r.json())?.detail || `error ${r.status}`
      setMsg({ ok: false, text: detail })
    } else {
      setMsg({ ok: true, text: `${sym} ${enabled ? 'enabled' : 'disabled'}` })
    }
    load()
  }

  return (
    <div>
      <h1 className="page-title">Autotrade (paper)</h1>
      <p className="page-sub">
        Enabled assets are scanned live and traded in paper mode on their
        walk-forward-validated strategy. Nightly reprocessing is scoped to
        this roster. Kill switch: /pause on Telegram.
      </p>

      {err && <div className="card"><span className="hint">{err}</span></div>}
      {msg && (
        <div className="card">
          <span className={msg.ok ? 'pos' : 'neg'}>{msg.text}</span>
        </div>
      )}

      <MarketClock />

      <PaperHistory />

      <SettingsCard />

      <div className="card">
        <h3>Add asset</h3>
        <div className="filters">
          <label>symbol
            <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder="e.g. LLY" style={{ minWidth: 110 }} />
          </label>
          <label>size (USD per trade)
            <input type="number" min="10" value={size}
              onChange={(e) => setSize(Number(e.target.value) || 1000)} />
          </label>
          <button className="btn" disabled={!symbol}
            onClick={() => save(symbol, true, size)}>Enable</button>
        </div>
        <p className="hint">
          Enabling requires a positive out-of-sample profit factor for the
          symbol — the button will refuse assets that failed walk-forward.
        </p>
      </div>

      <div className="card">
        <h3>Roster</h3>
        {!rows || rows.length === 0 ? (
          <p className="hint">no assets enabled yet</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th className="txt">Symbol</th><th className="txt">State</th>
                <th>OOS PF</th><th>Size/trade</th><th className="txt">Added</th><th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol}>
                  <td className="txt"><b>{r.symbol}</b></td>
                  <td className="txt">{r.enabled
                    ? <span className="badge done">ON</span>
                    : <span className="badge failed">off</span>}</td>
                  <td><Pf v={r.oos_pf} /></td>
                  <td>${num(r.size_usd, 0)}</td>
                  <td className="txt hint">{r.added_at?.slice(0, 10)}</td>
                  <td>
                    <button className="btn ghost"
                      onClick={() => save(r.symbol, !r.enabled, r.size_usd)}>
                      {r.enabled ? 'disable' : 'enable'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
