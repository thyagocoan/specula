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

// Chronological trade journal grouped by ISO week (Melbourne time) with a
// concurrency sweep and an execution cap: with "max open trades" set, the
// replay skips any signal that fires while the cap is full — no reprocessing,
// it's pure arithmetic over the already-computed trade times.
export default function Journal() {
  const [doc, setDoc] = useState(null)
  const [err, setErr] = useState(null)
  const [cls, setCls] = useState('all')
  const [view, setView] = useState('charts')
  const [cap, setCap] = useState(0) // 0 = unlimited
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/journal')
        if (!r.ok) throw new Error(`API error ${r.status}`)
        setDoc(await r.json())
      } catch (e) {
        setErr(String(e.message || e))
      }
    })()
  }, [])

  const model = useMemo(() => {
    if (!doc) return null
    const isCrypto = (s) => /USD[TC]$/.test(s)
    const source = doc.trades.filter((t) =>
      cls === 'all' ? true : cls === 'crypto' ? isCrypto(t.symbol) : !isCrypto(t.symbol))

    // replay in entry order; when the cap is full, the signal is skipped
    let open = []
    let skippedTotal = 0
    const trades = source.map((t0) => {
      const t = { ...t0 }
      const entryMs = Date.parse(t.entry_ts)
      open = open.filter((o) => o.exit_ms > entryMs)
      if (cap > 0 && open.length >= cap) {
        t._skipped = true
        skippedTotal += 1
      } else {
        open.push({
          exit_ms: t.exit_ts ? Date.parse(t.exit_ts) : Infinity,
          size: t.size_usd,
        })
        t._concurrent = open.length
        t._capital = open.reduce((a, o) => a + o.size, 0)
      }
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
      if (t.pnl_usd != null) {
        w.pnl += t.pnl_usd
        w.closed += 1
        if (t.pnl_usd > 0) w.wins += 1
      }
      if (t._concurrent > w.maxConc) w.maxConc = t._concurrent
      if (t._capital > w.peakCap) w.peakCap = t._capital
    }

    // bankroll: seed with the peak capital ever needed, then carry each
    // week's result into the next week's starting amount
    const asc = [...map.values()].sort((a, b) => a.key.localeCompare(b.key))
    const seed = Math.max(...asc.map((w) => w.peakCap), 0) ||
      (cls === 'crypto' ? 100 : 1000)
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
      y.cum = y.trades.filter((t) => t.pnl_usd != null)
        .map((t) => ({ t: t.entry_ts, v: cum += t.pnl_usd }))
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
  }, [doc, cls, cap])

  if (err) return <div className="card"><span className="neg">{err}</span></div>
  if (!doc) {
    return (
      <div>
        <h1 className="page-title">Journal</h1>
        <p className="page-sub">
          assembling every trade of each asset's best setup — the first load
          after a restart backtests each asset once, give it a minute…
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
        One setup per asset — its best logged strategy ({doc.scope}; the exact
        list is on the Charts tab). Trades replay in the order they fired; set
        "max open" to simulate limited capital — capped-out signals are skipped
        instantly, nothing reprocesses. P&amp;L is net of your venue fees.
      </p>

      <div className="controls">
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
          <select value={cap} onChange={(e) => setCap(Number(e.target.value))}
            aria-label="max open trades">
            {CAPS.map((c) => (
              <option key={c} value={c}>
                {c === 0 ? 'max open: unlimited' : `max open: ${c} trade${c > 1 ? 's' : ''}`}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="card">
        <h3>Capacity &amp; result</h3>
        <p style={{ margin: 0 }}>
          Executed <b>{model.taken}</b> trades
          {model.skippedTotal > 0 && <> · <span className="neg">
            {model.skippedTotal} skipped by the cap</span></>} ·
          peak <b>{model.maxConc}</b> open at once · capital needed{' '}
          <b>${num(model.peakCap, 0)}</b> · total P&amp;L{' '}
          <Money v={model.final - model.seed} /> on ${num(model.seed, 0)}{' '}
          (<Ret v={model.seed ? 100 * (model.final / model.seed - 1) : null} />)
        </p>
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
            <h3>What the journal trades — one best setup per asset</h3>
            <p className="hint" style={{ marginTop: 4 }}>
              scope: {doc.scope}. With autotrade roster assets enabled, the
              journal switches to exactly those.
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
                      <th className="txt">Side</th>
                      <th>Entry</th>
                      <th className="txt">Exit (Melbourne)</th>
                      <th>Exit</th>
                      <th>P&amp;L $</th>
                      <th>P&amp;L %</th>
                      <th>Open</th>
                    </tr>
                  </thead>
                  <tbody>
                    {w.trades.slice().reverse().map((t, i) => {
                      const ret = t.net_return_pct ?? t.return_pct
                      return (
                        <tr key={i} style={t._skipped ? { opacity: 0.45 } : undefined}>
                          <td className="txt">{fmtDay(t.entry_ts)}</td>
                          <td className="txt">{fmtTime(t.entry_ts, MEL)}</td>
                          <td className="txt"><b>{t.symbol}</b></td>
                          <td className="txt">{t.side}</td>
                          <td>{t.entry_price}</td>
                          <td className="txt">{t.exit_ts ? fmtTime(t.exit_ts, MEL) : 'open'}</td>
                          <td>{t.exit_price ?? '—'}</td>
                          <td>{t._skipped ? '—' : <Money v={t.pnl_usd} />}</td>
                          <td>{t._skipped ? '—' : ret != null
                            ? <span className={ret >= 0 ? 'pos' : 'neg'}>{num(ret, 2)}%</span>
                            : '—'}</td>
                          <td>{t._skipped
                            ? <span className="neg">skipped</span>
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
