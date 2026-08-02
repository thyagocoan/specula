import { useEffect, useMemo, useState } from 'react'
import Line from '../components/Line.jsx'
import { Pf, Ret } from '../components/bits.jsx'
import { num } from '../data.js'

const MEL = 'Australia/Melbourne'

const fmtTime = (iso, tz) => new Date(iso).toLocaleString('en-AU', {
  timeZone: tz, day: '2-digit', month: 'short',
  hour: '2-digit', minute: '2-digit', hour12: false,
})

const fmtDay = (iso) => new Date(iso).toLocaleString('en-AU', {
  timeZone: MEL, weekday: 'short',
})

// Melbourne-local calendar date (YYYY-MM-DD) of a UTC timestamp
const melDate = (iso) =>
  new Date(iso).toLocaleDateString('en-CA', { timeZone: MEL })

// ISO-8601 week number of a YYYY-MM-DD date (weeks start Monday)
function isoWeek(ymd) {
  const [y, m, d] = ymd.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  const dayNum = (date.getUTCDay() + 6) % 7 // Mon=0
  date.setUTCDate(date.getUTCDate() - dayNum + 3) // nearest Thursday
  const isoYear = date.getUTCFullYear()
  const jan4 = new Date(Date.UTC(isoYear, 0, 4))
  const week = 1 + Math.round(
    ((date - jan4) / 86400000 - 3 + ((jan4.getUTCDay() + 6) % 7)) / 7,
  )
  return { year: isoYear, week }
}

function weekRangeLabel(ymd) {
  const [y, m, d] = ymd.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  const monday = new Date(date)
  monday.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7))
  const sunday = new Date(monday)
  sunday.setUTCDate(monday.getUTCDate() + 6)
  const f = (dt) => dt.toLocaleDateString('en-AU', {
    timeZone: 'UTC', day: 'numeric', month: 'short',
  })
  return `${f(monday)} – ${f(sunday)}`
}

const Money = ({ v }) => v == null ? '—' : (
  <span className={v >= 0 ? 'pos' : 'neg'}>
    {v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)}
  </span>
)

// weekly P&L bar strip for one year: green above / red below the zero line
function WeekBars({ weeks }) {
  const W = 900, H = 170, padB = 26, padT = 12
  const plotH = H - padT - padB
  const zero = padT + plotH / 2
  const max = Math.max(...weeks.map((w) => Math.abs(w.pnl)), 1)
  const bw = W / weeks.length
  const labelEvery = Math.max(1, Math.ceil(weeks.length / 16))
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', display: 'block' }}
      role="img" aria-label="weekly P&L">
      <line x1={0} x2={W} y1={zero} y2={zero} stroke="var(--grid)" />
      {weeks.map((w, i) => {
        const h = Math.max(1.5, (Math.abs(w.pnl) / max) * (plotH / 2))
        const up = w.pnl >= 0
        return (
          <g key={w.key}>
            <rect x={i * bw + bw * 0.15} width={bw * 0.7}
              y={up ? zero - h : zero} height={h} rx="1.5"
              fill={up ? '#1baf7a' : '#e34948'} opacity="0.9">
              <title>{`W${w.week} (${w.range}): ${w.pnl < 0 ? '-' : ''}$${Math.abs(w.pnl).toFixed(2)} · ${w.taken.length} trades`}</title>
            </rect>
            {i % labelEvery === 0 && (
              <text x={i * bw + bw / 2} y={H - 8} textAnchor="middle"
                fontSize="10" fill="var(--muted)">W{w.week}</text>
            )}
          </g>
        )
      })}
    </svg>
  )
}

const CLASSES = [
  ['all', 'All'],
  ['stock', 'Stocks'],
  ['crypto', 'Crypto'],
]

const VIEWS = [
  ['charts', 'Charts'],
  ['trades', 'Trades'],
]

const CAPS = [0, 1, 2, 3, 5, 10]
const WEEK_WINDOWS = [[0, 'All time'], [4, 'Last 4 weeks'],
  [8, 'Last 8 weeks'], [12, 'Last 12 weeks'], [26, 'Last 26 weeks']]
const SPLITS = [[1, '1 trade (100%)'], [2, '2 trades (50% each)'],
  [3, '3 trades (33% each)'], [4, '4 trades (25% each)'],
  [5, '5 trades (20% each)']]

// Chronological trade journal grouped by ISO week (Melbourne time) with a
// concurrency sweep and an execution cap: with "max open trades" set, the
// replay skips any signal that fires while the cap is full — no reprocessing,
// it's pure arithmetic over the already-computed trade times.
export default function Journal() {
  const [doc, setDoc] = useState(null)
  const [approved, setApproved] = useState(null) // null = loading
  const [selSig, setSelSig] = useState('all')
  const [err, setErr] = useState(null)
  const [cls, setCls] = useState('all')
  const [view, setView] = useState('charts')
  const [cap, setCap] = useState(0) // 0 = unlimited
  const [weeksWin, setWeeksWin] = useState(0) // 0 = all time
  const [mode, setMode] = useState('fixed') // fixed sizes | capital-limited
  const [startAmt, setStartAmt] = useState(1000)
  const [split, setSplit] = useState(1)
  const [showAll, setShowAll] = useState(false)
  const [fees, setFees] = useState({ stock: 0.035, crypto: 0.10 })

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/settings')
        if (r.ok) {
          const s = await r.json()
          setFees({ stock: s.fee_stock_pct, crypto: s.fee_crypto_pct })
        }
      } catch { /* defaults stand */ }
    })()
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/favsetups')
        const favs = r.ok ? await r.json() : []
        const appr = favs.filter((f) => f.status === 'approved')
        setApproved(appr)
        if (appr.length === 1) setSelSig(appr[0].sig)
      } catch {
        setApproved([])
      }
    })()
  }, [])

  useEffect(() => {
    if (!approved || approved.length === 0) return
    let alive = true
    setDoc(null)
    setErr(null)
    ;(async () => {
      try {
        const r = await fetch(`/api/journal?sig=${encodeURIComponent(selSig)}`)
        if (!r.ok) {
          const d = await r.json().catch(() => ({}))
          throw new Error(d?.detail || `API error ${r.status}`)
        }
        if (alive) setDoc(await r.json())
      } catch (e) {
        if (alive) setErr(String(e.message || e))
      }
    })()
    return () => { alive = false }
  }, [approved, selSig])

  const model = useMemo(() => {
    if (!doc) return null
    const isCrypto = (s) => /USD[TC]$/.test(s)
    let source = doc.trades.filter((t) =>
      cls === 'all' ? true : cls === 'crypto' ? isCrypto(t.symbol) : !isCrypto(t.symbol))

    // window: keep only the last N calendar weeks (the replay then starts
    // fresh inside the window with the configured amount)
    const weekKey = (t) => {
      const { year, week } = isoWeek(melDate(t.entry_ts))
      return `${year}-W${String(week).padStart(2, '0')}`
    }
    if (weeksWin > 0 && source.length) {
      const keys = [...new Set(source.map(weekKey))].sort().slice(-weeksWin)
      const keep = new Set(keys)
      source = source.filter((t) => keep.has(weekKey(t)))
    }

    const start = Number(startAmt) > 0 ? Number(startAmt) : 1000
    let open = [] // {exit_ms, size(committed), pnl}
    let skippedTotal = 0
    let cash = start
    const trades = source.map((t0) => {
      const t = { ...t0 }
      const entryMs = Date.parse(t.entry_ts)
      open = open.filter((o) => {
        if (o.exit_ms > entryMs) return true
        cash += o.size + (o.pnl || 0) // position closed → capital released
        return false
      })
      const ret = (t.net_return_pct ?? t.return_pct)
      if (mode === 'capital') {
        // stake = split share of total equity, but only if that much cash
        // is actually free — otherwise the signal waits for a release and,
        // since entries can't wait, is skipped
        const totalEq = cash + open.reduce((a, o) => a + o.size, 0)
        const desired = totalEq / split
        if (cash + 1e-9 < desired || desired <= 0) {
          t._skipped = 'no free capital'
          skippedTotal += 1
          return t
        }
        // net_return already carries the %-fee; small stakes additionally
        // hit IBKR's $0.35/order minimum — charge the shortfall per side
        const crypto = isCrypto(t.symbol)
        const feePct = crypto ? fees.crypto : fees.stock
        const minFee = crypto ? 0 : 0.35
        const extraPct = 2 * Math.max(0, (minFee / desired) * 100 - feePct)
        const retNet = ret != null ? ret - extraPct : null
        t._stake = desired
        t._pnl = retNet != null ? desired * retNet / 100 : null
        cash -= desired
        open.push({
          exit_ms: t.exit_ts ? Date.parse(t.exit_ts) : Infinity,
          size: desired, pnl: t._pnl || 0,
        })
      } else {
        if (cap > 0 && open.length >= cap) {
          t._skipped = 'max open reached'
          skippedTotal += 1
          return t
        }
        t._stake = t.size_usd
        t._pnl = t.pnl_usd
        open.push({
          exit_ms: t.exit_ts ? Date.parse(t.exit_ts) : Infinity,
          size: t.size_usd, pnl: 0,
        })
      }
      t._concurrent = open.length
      t._capital = open.reduce((a, o) => a + o.size, 0)
      return t
    })

    const map = new Map()
    for (const t of trades) {
      const ymd = melDate(t.entry_ts)
      const { year, week } = isoWeek(ymd)
      const key = `${year}-W${String(week).padStart(2, '0')}`
      if (!map.has(key)) {
        map.set(key, {
          key, year, week, range: weekRangeLabel(ymd), trades: [], taken: [],
          pnl: 0, wins: 0, closed: 0, skipped: 0, maxConc: 0, peakCap: 0,
        })
      }
      const w = map.get(key)
      w.trades.push(t)
      if (t._skipped) {
        w.skipped += 1
        continue
      }
      w.taken.push(t)
      if (t._pnl != null) {
        w.pnl += t._pnl
        w.closed += 1
        if (t._pnl > 0) w.wins += 1
      }
      if (t._concurrent > w.maxConc) w.maxConc = t._concurrent
      if (t._capital > w.peakCap) w.peakCap = t._capital
    }

    // bankroll: the configured start, carried week to week
    const asc = [...map.values()].sort((a, b) => a.key.localeCompare(b.key))
    const seed = mode === 'capital'
      ? start
      : (Number(startAmt) > 0 ? Number(startAmt)
        : Math.max(...asc.map((w) => w.peakCap), 0) || 1000)
    let bal = seed
    for (const w of asc) {
      w.start = bal
      bal += w.pnl
      w.end = bal
      w.retPct = w.start ? (100 * w.pnl) / w.start : null
    }

    // one chart group per year, from executed trades only
    const byYear = new Map()
    for (const w of asc) {
      if (!byYear.has(w.year)) {
        byYear.set(w.year, { year: w.year, weeks: [], trades: [] })
      }
      const y = byYear.get(w.year)
      y.weeks.push(w)
      y.trades.push(...w.taken)
    }
    for (const y of byYear.values()) {
      y.trades.sort((a, b) => a.entry_ts.localeCompare(b.entry_ts))
      let cum = 0
      y.cum = y.trades.filter((t) => t._pnl != null)
        .map((t) => ({ t: t.entry_ts, v: cum += t._pnl }))
      y.total = cum
    }

    return {
      weeks: asc.slice().reverse(),
      years: [...byYear.values()].sort((a, b) => b.year - a.year),
      seed,
      final: bal,
      skippedTotal,
      taken: trades.length - skippedTotal,
      maxConc: Math.max(0, ...asc.map((w) => w.maxConc)),
      peakCap: Math.max(0, ...asc.map((w) => w.peakCap)),
    }
  }, [doc, cls, cap, weeksWin, mode, startAmt, split, fees])

  if (err) return <div className="card"><span className="neg">{err}</span></div>
  if (approved != null && approved.length === 0) {
    return (
      <div>
        <h1 className="page-title">Journal</h1>
        <p className="page-sub">
          the journal shows League-approved setups only — nothing is
          approved yet. Run the league, approve the holdout survivors, and
          come back.
        </p>
      </div>
    )
  }
  if (!doc) {
    return (
      <div>
        <h1 className="page-title">Journal</h1>
        <p className="page-sub">
          assembling every trade of the selected approved setup — the first
          load backtests each of its assets once, give it a minute…
        </p>
      </div>
    )
  }

  const { weeks, years } = model
  const visible = showAll ? weeks : weeks.slice(0, 8)

  return (
    <div>
      <h1 className="page-title">Journal</h1>
      <p className="page-sub">
        League-approved setups only ({doc.scope}). Trades replay in the order
        they fired; set "max open" to simulate limited capital — capped-out
        signals are skipped instantly, nothing reprocesses. P&amp;L is net of
        your venue fees.
      </p>

      <div className="controls">
        <div className="select-pill">
          <select value={selSig} onChange={(e) => setSelSig(e.target.value)}
            aria-label="approved setup">
            {(approved?.length ?? 0) > 1 && (
              <option value="all">All approved setups ({approved.length})</option>
            )}
            {(approved || []).map((f) => (
              <option key={f.sig} value={f.sig}>{f.label}</option>
            ))}
          </select>
        </div>
        <div className="tabs">
          {VIEWS.map(([id, label]) => (
            <button key={id} className={view === id ? 'active' : ''}
              onClick={() => setView(id)}>
              {label}
            </button>
          ))}
        </div>
        <div className="tabs">
          {CLASSES.map(([id, label]) => (
            <button key={id} className={cls === id ? 'active' : ''}
              onClick={() => setCls(id)}>
              {label}
            </button>
          ))}
        </div>
        <div className="select-pill">
          <select value={weeksWin}
            onChange={(e) => setWeeksWin(Number(e.target.value))}
            aria-label="weeks window">
            {WEEK_WINDOWS.map(([n, label]) => (
              <option key={n} value={n}>{label}</option>
            ))}
          </select>
        </div>
        <div className="select-pill">
          <select value={mode} onChange={(e) => setMode(e.target.value)}
            aria-label="sizing mode">
            <option value="fixed">sizing: fixed per trade</option>
            <option value="capital">sizing: limited capital</option>
          </select>
        </div>
        <label className="chip" style={{ cursor: 'text' }}>
          start $
          <input type="number" min="1" step="100" value={startAmt}
            onChange={(e) => setStartAmt(e.target.value)}
            style={{ width: 90, marginLeft: 6 }} />
        </label>
        {mode === 'capital' ? (
          <div className="select-pill">
            <select value={split} onChange={(e) => setSplit(Number(e.target.value))}
              aria-label="capital split">
              {SPLITS.map(([n, label]) => (
                <option key={n} value={n}>split: {label}</option>
              ))}
            </select>
          </div>
        ) : (
          <div className="select-pill">
            <select value={cap} onChange={(e) => setCap(Number(e.target.value))}
              aria-label="max open trades">
              {CAPS.map((c) => (
                <option key={c} value={c}>
                  {c === 0 ? 'max open: unlimited' : `max open: ${c} trade${c > 1 ? 's' : ''}`}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="card">
        <h3>Capacity &amp; result</h3>
        <p style={{ margin: 0 }}>
          Executed <b>{model.taken}</b> trades
          {model.skippedTotal > 0 && <> · <span className="neg">
            {model.skippedTotal} skipped ({mode === 'capital'
              ? 'waiting for capital' : 'max-open cap'})</span></>} ·
          peak <b>{model.maxConc}</b> open at once · peak capital committed{' '}
          <b>${num(model.peakCap, 0)}</b> · started ${num(model.seed, 0)} →
          finished <b>${num(model.final, 2)}</b> · P&amp;L{' '}
          <Money v={model.final - model.seed} />{' '}
          (<Ret v={model.seed ? 100 * (model.final / model.seed - 1) : null} />)
        </p>
        {mode === 'capital' && (
          <p className="hint" style={{ marginBottom: 0 }}>
            each trade stakes 1/{split} of the account; a signal only opens
            when that much cash is free — otherwise it's skipped until a
            position closes and releases capital. Fees per trade: IBKR{' '}
            {num(fees.stock, 3)}%/side with a $0.35/order minimum (the
            minimum is charged when the stake is small enough to matter) ·
            Binance {num(fees.crypto, 2)}%/side.
          </p>
        )}
      </div>

      {weeks.length === 0 && (
        <div className="card"><span className="hint">no trades in scope</span></div>
      )}

      {view === 'charts' && (
        <>
          {years.map((y) => (
            <div className="card" key={y.year}>
              <h3>{y.year} — weekly P&amp;L and cumulative</h3>
              <p className="hint" style={{ marginTop: 4 }}>
                {y.trades.length} trades · year P&amp;L <Money v={y.total} /> ·
                bars = P&amp;L per week, line = running total after every trade
              </p>
              <WeekBars weeks={y.weeks} />
              {y.cum.length > 1 && (
                <Line yLabel="cumulative P&L (USD)" height={200} series={[
                  { name: 'cumulative P&L $', color: 'var(--series-1)', points: y.cum },
                ]} />
              )}
            </div>
          ))}

          <div className="card">
            <h3>What the journal trades</h3>
            <p className="hint" style={{ marginTop: 4 }}>
              {doc.scope} — asset ranking comes from the league's per-asset
              results (re-run the league after approving to refresh them).
            </p>
            <table className="grid">
              <thead>
                <tr>
                  <th className="txt">Asset</th>
                  <th className="txt">Strategy (best logged run)</th>
                  <th>PF (in-sample)</th><th>OOS PF</th>
                </tr>
              </thead>
              <tbody>
                {(doc.setups || []).map((s) => (
                  <tr key={s.symbol}>
                    <td className="txt"><b>{s.symbol}</b></td>
                    <td className="txt">{s.label}</td>
                    <td><Pf v={s.pf} /></td>
                    <td><Pf v={s.oos_pf} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {view === 'trades' && (
        <>
          {visible.map((w) => (
            <div className="card" key={w.key}>
              <h3>
                Week {w.week} · {w.range} {w.year}
              </h3>
              <p className="hint" style={{ marginTop: 4 }}>
                started <b>${num(w.start, 2)}</b> → ended <b>${num(w.end, 2)}</b> ·
                P&amp;L <Money v={w.pnl} /> (<Ret v={w.retPct} />) ·
                {w.taken.length} trades ({w.wins}/{w.closed} wins
                {w.skipped > 0 && <>, <span className="neg">{w.skipped} skipped</span></>}) ·
                max {w.maxConc} open · peak capital ${num(w.peakCap, 0)}
              </p>
              <div style={{ overflowX: 'auto' }}>
                <table className="grid">
                  <thead>
                    <tr>
                      <th className="txt">Day</th>
                      <th className="txt">Entry (Melbourne)</th>
                      <th className="txt">Symbol</th>
                      {doc.multi && <th className="txt">Setup</th>}
                      <th className="txt">Side</th>
                      <th>Entry</th>
                      <th className="txt">Exit (Melbourne)</th>
                      <th>Exit</th>
                      <th>Stake $</th>
                      <th>P&amp;L $</th>
                      <th>P&amp;L %</th>
                      <th>Open</th>
                    </tr>
                  </thead>
                  <tbody>
                    {w.trades.slice().reverse().map((t, i) => {
                      const ret = t._pnl != null && t._stake
                        ? (100 * t._pnl) / t._stake
                        : (t.net_return_pct ?? t.return_pct)
                      return (
                        <tr key={i} style={t._skipped ? { opacity: 0.45 } : undefined}>
                          <td className="txt">{fmtDay(t.entry_ts)}</td>
                          <td className="txt">{fmtTime(t.entry_ts, MEL)}</td>
                          <td className="txt"><b>{t.symbol}</b></td>
                          {doc.multi && <td className="txt hint">{t.setup}</td>}
                          <td className="txt">{t.side}</td>
                          <td>{t.entry_price}</td>
                          <td className="txt">{t.exit_ts ? fmtTime(t.exit_ts, MEL) : 'open'}</td>
                          <td>{t.exit_price ?? '—'}</td>
                          <td>{t._skipped ? '—' : num(t._stake, 2)}</td>
                          <td>{t._skipped ? '—' : <Money v={t._pnl} />}</td>
                          <td>{t._skipped ? '—' : ret != null
                            ? <span className={ret >= 0 ? 'pos' : 'neg'}>{num(ret, 2)}%</span>
                            : '—'}</td>
                          <td>{t._skipped
                            ? <span className="neg" title={t._skipped}>skipped</span>
                            : t._concurrent > 1 ? <b>{t._concurrent}</b> : t._concurrent}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ))}

          {!showAll && weeks.length > 8 && (
            <button className="btn ghost" onClick={() => setShowAll(true)}>
              show all {weeks.length} weeks
            </button>
          )}
        </>
      )}
    </div>
  )
}
