import { useEffect, useMemo, useState } from 'react'
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

const CLASSES = [
  ['all', 'All'],
  ['stock', 'Stocks'],
  ['crypto', 'Crypto'],
]

// Chronological trade journal grouped by ISO week (Melbourne time) with a
// concurrency sweep: at every entry we count how many positions were already
// open and how much capital was committed — the "do I need more money to take
// every signal?" answer.
export default function Journal() {
  const [doc, setDoc] = useState(null)
  const [err, setErr] = useState(null)
  const [cls, setCls] = useState('all')
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

  const weeks = useMemo(() => {
    if (!doc) return null
    const isCrypto = (s) => /USD[TC]$/.test(s)
    const trades = doc.trades.filter((t) =>
      cls === 'all' ? true : cls === 'crypto' ? isCrypto(t.symbol) : !isCrypto(t.symbol))

    // sweep in entry order: how many positions already open (and $ committed)
    // the moment each new trade fires
    let open = [] // [{exit_ms, size}]
    for (const t of trades) {
      const entryMs = new Date(t.entry_ts).getTime()
      open = open.filter((o) => o.exit_ms > entryMs)
      const exitMs = t.exit_ts ? new Date(t.exit_ts).getTime() : Infinity
      open.push({ exit_ms: exitMs, size: t.size_usd })
      t._concurrent = open.length
      t._capital = open.reduce((a, o) => a + o.size, 0)
    }

    const map = new Map()
    for (const t of trades) {
      const ymd = melDate(t.entry_ts)
      const { year, week } = isoWeek(ymd)
      const key = `${year}-W${String(week).padStart(2, '0')}`
      if (!map.has(key)) {
        map.set(key, {
          key, year, week, range: weekRangeLabel(ymd), trades: [],
          pnl: 0, wins: 0, closed: 0, maxConc: 0, peakCap: 0,
        })
      }
      const w = map.get(key)
      w.trades.push(t)
      if (t.pnl_usd != null) {
        w.pnl += t.pnl_usd
        w.closed += 1
        if (t.pnl_usd > 0) w.wins += 1
      }
      if (t._concurrent > w.maxConc) w.maxConc = t._concurrent
      if (t._capital > w.peakCap) w.peakCap = t._capital
    }
    return [...map.values()].sort((a, b) => b.key.localeCompare(a.key))
  }, [doc, cls])

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

  const visible = showAll ? weeks : weeks.slice(0, 8)
  const globalMaxConc = Math.max(0, ...weeks.map((w) => w.maxConc))
  const globalPeakCap = Math.max(0, ...weeks.map((w) => w.peakCap))

  return (
    <div>
      <h1 className="page-title">Journal</h1>
      <p className="page-sub">
        Every trade of each asset's best strategy, week by week in the order
        they fired ({doc.scope}: {doc.symbols.join(', ')}). "Open" = positions
        already held when the trade fired — if it's often above 1, taking every
        signal needs more capital than one trade size. Trades are the full
        backtest history (use it for timing and capacity; the OOS verdict for
        performance lives on Walk-forward).
      </p>

      <div className="controls">
        <div className="tabs">
          {CLASSES.map(([id, label]) => (
            <button key={id} className={cls === id ? 'active' : ''}
              onClick={() => setCls(id)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>Capacity across all weeks</h3>
        <p>
          Peak simultaneous positions: <b>{globalMaxConc}</b> · peak capital
          committed at once: <b>${num(globalPeakCap, 0)}</b>. Size one trade at
          your per-class setting and keep at least the peak amount available to
          never skip a signal.
        </p>
      </div>

      {weeks.length === 0 && (
        <div className="card"><span className="hint">no trades in scope</span></div>
      )}

      {visible.map((w) => (
        <div className="card" key={w.key}>
          <h3>
            Week {w.week} · {w.range} {w.year}
          </h3>
          <p className="hint" style={{ marginTop: 4 }}>
            {w.trades.length} trades · {w.wins}/{w.closed} wins ·
            P&amp;L <Money v={w.pnl} /> · max {w.maxConc} open at once ·
            peak capital ${num(w.peakCap, 0)}
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
                {w.trades.map((t, i) => (
                  <tr key={i}>
                    <td className="txt">{fmtDay(t.entry_ts)}</td>
                    <td className="txt">{fmtTime(t.entry_ts, MEL)}</td>
                    <td className="txt"><b>{t.symbol}</b></td>
                    <td className="txt">{t.side}</td>
                    <td>{t.entry_price}</td>
                    <td className="txt">{t.exit_ts ? fmtTime(t.exit_ts, MEL) : 'open'}</td>
                    <td>{t.exit_price ?? '—'}</td>
                    <td><Money v={t.pnl_usd} /></td>
                    <td>{t.return_pct != null
                      ? <span className={t.return_pct >= 0 ? 'pos' : 'neg'}>{num(t.return_pct, 2)}%</span>
                      : '—'}</td>
                    <td>{t._concurrent > 1
                      ? <b>{t._concurrent}</b>
                      : t._concurrent}</td>
                  </tr>
                ))}
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
    </div>
  )
}
