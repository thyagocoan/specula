import { useEffect, useMemo, useState } from 'react'
import CandleChart from '../components/CandleChart.jsx'
import { Pf } from '../components/bits.jsx'
import { num } from '../data.js'

const TF_SEC = {
  '1min': 60, '5min': 300, '15min': 900, '30min': 1800,
  '1h': 3600, '2h': 7200, '4h': 14400, '1d': 86400,
}
const TV_INTERVAL = {
  '1min': '1', '5min': '5', '15min': '15', '30min': '30',
  '1h': '60', '2h': '120', '4h': '240', '1d': 'D',
}

// axis re-based to NY so the chart reads in market time
const _tzCache = new Map()
function nyShift(sec) {
  const bucket = Math.floor(sec / 3600)
  let off = _tzCache.get(bucket)
  if (off === undefined) {
    const p = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', hourCycle: 'h23', year: 'numeric',
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    }).formatToParts(new Date(sec * 1000))
      .reduce((o, x) => ((o[x.type] = x.value), o), {})
    off = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) / 1000
      - sec
    _tzCache.set(bucket, off)
  }
  return off
}

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

// one timeframe pane of the validation grid: candles + the setup's
// Bollinger bands + fill markers snapped to bars + level lines
const PANE_DAYS = { '2h': 25, '1h': 15, '30min': 10, '5min': 5 }

function TradePane({ trade, tf }) {
  const [candles, setCandles] = useState(null)
  const [err, setErr] = useState(null)
  const dev = trade.bb_dev || 2.0

  useEffect(() => {
    setCandles(null); setErr(null)
    ;(async () => {
      try {
        const r = await fetch(`/api/candles_recent/${trade.symbol}?tf=${tf}`
          + `&days=${PANE_DAYS[tf] || 10}&bb=${dev}`)
        if (!r.ok) {
          const d = await r.json().catch(() => ({}))
          throw new Error(d?.detail || `API error ${r.status}`)
        }
        setCandles(await r.json())
      } catch (e) {
        setErr(String(e.message || e))
      }
    })()
  }, [trade.id, tf])

  const view = useMemo(() => {
    if (!candles) return null
    const shifted = candles.map((c) => ({
      ...c, time: c.time + nyShift(c.time),
    }))
    const times = shifted.map((c) => c.time)
    const snap = (sec) => { // markers must sit on an existing bar
      let lo = 0
      for (let i = 0; i < times.length; i++) {
        if (times[i] <= sec) lo = i
        else break
      }
      return times[lo]
    }
    const long = trade.side === 'long'
    const e = Math.floor(Date.parse(trade.entry_ts) / 1000)
    const markers = [{
      time: snap(e + nyShift(e)),
      position: long ? 'belowBar' : 'aboveBar',
      color: long ? '#1baf7a' : '#e34948',
      shape: long ? 'arrowUp' : 'arrowDown',
      text: long ? 'L' : 'S', size: 2,
    }]
    if (trade.exit_ts) {
      const x = Math.floor(Date.parse(trade.exit_ts) / 1000)
      markers.push({
        time: snap(x + nyShift(x)),
        position: long ? 'aboveBar' : 'belowBar',
        color: (trade.pnl_pct ?? 0) >= 0 ? '#1baf7a' : '#e34948',
        shape: 'circle',
        text: `${trade.pnl_pct > 0 ? '+' : ''}${(trade.pnl_pct ?? 0).toFixed(2)}%`,
        size: 2,
      })
    }
    const pick = (key) => shifted.filter((c) => c[key] != null)
      .map((c) => ({ time: c.time, value: c[key] }))
    const lines = [
      { name: `BB mid`, color: '#4f83e0', points: pick('bb_mid') },
      { name: `BB +${dev}σ`, color: '#8a68c9', points: pick('bb_up') },
      { name: `BB -${dev}σ`, color: '#18a0a8', points: pick('bb_lo') },
    ].filter((l) => l.points.length)
    const pad = 25 * (TF_SEC[tf] || 1800)
    const x = trade.exit_ts
      ? Math.floor(Date.parse(trade.exit_ts) / 1000)
      : Math.floor(Date.now() / 1000)
    const range = { from: e + nyShift(e) - pad, to: x + nyShift(x) + pad }
    return { shifted, markers, lines, range }
  }, [candles, trade])

  const priceLines = useMemo(() => {
    const out = [{ price: trade.entry_price,
      title: `entry ${num(trade.entry_price, 4)}`, color: '#e8b93c' }]
    if (trade.exit_price != null) {
      out.push({ price: trade.exit_price,
        title: `exit ${num(trade.exit_price, 4)}`, color: '#e8b93c' })
    }
    if (trade.sl != null) {
      out.push({ price: trade.sl, title: `stop ${num(trade.sl, 4)}`,
        color: '#e34948' })
    }
    if (trade.tp != null) {
      out.push({ price: trade.tp, title: `target ${num(trade.tp, 4)}`,
        color: '#1baf7a' })
    }
    return out
  }, [trade])

  const role = tf === trade.setup_tf ? 'setup TF'
    : tf === trade.exec_tf ? 'exec TF' : null

  return (
    <div style={{ minWidth: 0 }}>
      <p style={{ margin: '0 0 4px' }}>
        <b>{tf}</b>{' '}
        {role && <span className="badge done">{role}</span>}{' '}
        <span className="hint">BB(20, {dev})</span>
      </p>
      {err && <p className="neg">{err}</p>}
      {!candles && !err && <p className="hint">loading…</p>}
      {view && (
        <CandleChart candles={view.shifted} markers={view.markers}
          range={view.range} priceLines={priceLines} lines={view.lines}
          height={300} />
      )}
    </div>
  )
}

// validate one paper trade across four timeframes at once, with the
// setup's own Bollinger bands on every pane
function PaperTradeChart({ trade, onClose }) {
  const tvUrl = `https://www.tradingview.com/chart/?symbol=${
    encodeURIComponent(trade.symbol)}&interval=${
    TV_INTERVAL[trade.exec_tf] || '30'}`

  return (
    <div className="card">
      <h3>
        {trade.symbol} {trade.side} — trade #{trade.id} across timeframes{' '}
        <span className="hint">(New York time)</span>
        <span style={{ float: 'right' }}>
          <a className="btn ghost" href={tvUrl} target="_blank"
            rel="noreferrer"
            title="opens TradingView on this symbol — level lines are drawn here (TradingView URLs can't carry drawings)">
            Open in TradingView ↗
          </a>{' '}
          <button className="btn ghost" onClick={onClose}>close</button>
        </span>
      </h3>
      <p className="hint" style={{ margin: '0 0 8px' }}>
        {trade.setup} · gold = entry/exit fills · red = stop · green = fixed
        target{trade.tp == null ? ' (this setup targets the moving midband)' : ''}
        {' '}· each pane computes BB(20, {trade.bb_dev || 2.0}) on its own
        timeframe — validate the pattern on the setup TF, the fill on the
        faster ones
      </p>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14,
      }}>
        {['2h', '1h', '30min', '5min'].map((tf) => (
          <TradePane key={tf} trade={trade} tf={tf} />
        ))}
      </div>
    </div>
  )
}

// live paper-trading results: daily balance + full trade history
function PaperHistory() {
  const [doc, setDoc] = useState(null)
  const [sym, setSym] = useState('all')
  const [setup, setSetup] = useState('all')
  const [focus, setFocus] = useState(null) // trade shown on the chart

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
                      <tr key={t.id} className="selectable"
                        onClick={() => setFocus(
                          focus?.id === t.id ? null : t)}>
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

      {focus && (
        <PaperTradeChart trade={focus} onClose={() => setFocus(null)} />
      )}

      <div className="card">
        <h3>Trade history{' '}
          <span className="hint">(click a trade to see it on the chart with
            entry, exit, stop and target)</span></h3>
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
                  <tr key={t.id} className="selectable"
                    style={focus?.id === t.id
                      ? { background: 'rgba(232,185,60,.14)' } : undefined}
                    onClick={() => setFocus(focus?.id === t.id ? null : t)}>
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
  const [tab, setTab] = useState('trading')

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

      <div className="controls">
        <div className="tabs">
          {[['trading', 'Paper trading'], ['config', 'Configuration']].map(([id, label]) => (
            <button key={id} className={tab === id ? 'active' : ''}
              onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <MarketClock />

      {tab === 'trading' && <PaperHistory />}

      {tab === 'config' && <>
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
      </>}
    </div>
  )
}
