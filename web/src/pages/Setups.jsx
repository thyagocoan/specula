import { useEffect, useMemo, useRef, useState } from 'react'
import { Pf, Ret, Th, sortRows } from '../components/bits.jsx'
import { groupSetups, num, strategySig, uniqueSorted, useFavStrategies } from '../data.js'

const PERIODS = [
  ['all', 'All time', null],
  ['30', 'Month', 30],
  ['7', 'Week', 7],
  ['1', 'Day', 1],
]

// how many of the top rows get their real trades loaded for period stats
const PERIOD_ROWS = 60

const Money = ({ v }) => v == null ? '—' : (
  <span className={v >= 0 ? 'pos' : 'neg'}>
    {v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)}
  </span>
)

export default function Setups({ runs }) {
  const [symbol, setSymbol] = useState('all')
  const [strategy, setStrategy] = useState('all')
  const [sweep, setSweep] = useState('all')
  const [minTrades, setMinTrades] = useState(30)
  const [period, setPeriod] = useState('all')
  const [sort, setSort] = useState({ col: 'pf_low', dir: 'desc' })
  const [runTrades, setRunTrades] = useState({})
  const fetched = useRef(new Set())
  const [favs, toggleFav] = useFavStrategies()

  const days = PERIODS.find(([id]) => id === period)?.[2] ?? null
  const setups = useMemo(() => groupSetups(runs), [runs])
  const symbols = uniqueSorted(runs.map((r) => r.symbol))
  const sweeps = uniqueSorted(runs.map((r) => r.sweep_tag))
  const strategies = uniqueSorted(runs.map((r) => r.strategy))

  // window anchor: the newest trade seen anywhere, so "last week" means the
  // data's last week for every setup alike
  const refT = useMemo(() => {
    let m = null
    for (const arr of Object.values(runTrades)) {
      for (const t of arr) {
        const x = Date.parse(t.entry_ts)
        if (!m || x > m) m = x
      }
    }
    return m
  }, [runTrades])

  const enriched = useMemo(() => {
    const base = setups.filter(
      (s) =>
        (symbol === 'all' || s.symbol === symbol) &&
        (strategy === 'all' || s.strategy === strategy) &&
        (sweep === 'all' || s.sweep_tag === sweep) &&
        s.n_trades >= minTrades,
    )
    if (days == null) return base
    return base.map((s) => {
      const runId = s.byFee?.[s.fees?.[0]]?.run_id
      const tr = runTrades[runId]
      if (!tr) return { ...s, period_trades: null, period_pnl: null }
      const win = refT
        ? tr.filter((t) => Date.parse(t.entry_ts) >= refT - days * 86400e3)
        : tr
      return {
        ...s,
        period_trades: win.length,
        period_pnl: win.reduce((a, t) => a + (t.pnl_usd || 0), 0),
      }
    })
  }, [setups, symbol, strategy, sweep, minTrades, days, runTrades, refT])

  const sorted = useMemo(() => sortRows(enriched, sort.col, sort.dir),
    [enriched, sort])
  const shown = sorted.slice(0, 200)

  // load real trades for the top rows whenever a time window is active
  useEffect(() => {
    if (days == null) return
    let alive = true
    const ids = sorted.slice(0, PERIOD_ROWS)
      .map((s) => s.byFee?.[s.fees?.[0]]?.run_id)
      .filter((id) => id && !fetched.current.has(id))
    ids.forEach((id) => fetched.current.add(id))
    const queue = [...ids]
    const workers = Array.from({ length: 3 }, async () => {
      while (queue.length && alive) {
        const id = queue.shift()
        try {
          const r = await fetch(`/api/trades/${id}`)
          if (r.ok) {
            const t = await r.json()
            if (alive) setRunTrades((prev) => ({ ...prev, [id]: t }))
          } else {
            fetched.current.delete(id)
          }
        } catch {
          fetched.current.delete(id)
        }
      }
    })
    Promise.all(workers)
    return () => { alive = false }
  }, [days, sorted])

  const periodLabel = PERIODS.find(([id]) => id === period)?.[1]
  const loading = days != null &&
    shown.slice(0, PERIOD_ROWS).some((s) => s.period_trades === null)

  return (
    <div>
      <h1 className="page-title">Setups</h1>
      <p className="page-sub">
        Runs grouped by identical parameters (fee scenarios shown side by side).
        Metrics are from the lowest-fee run; a setup that stays green in both PF
        columns survives the harsher cost model. Pick a time window to see each
        setup's recent trades and USD P&amp;L at your venue fees.
      </p>
      <div className="controls">
        <div className="tabs">
          {PERIODS.map(([id, label]) => (
            <button key={id} className={period === id ? 'active' : ''}
              onClick={() => {
                setPeriod(id)
                if (id !== 'all') setSort({ col: 'period_pnl', dir: 'desc' })
                else setSort({ col: 'pf_low', dir: 'desc' })
              }}>
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="card">
        <div className="filters">
          <label>asset
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              <option value="all">All</option>
              {symbols.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>strategy
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="all">All</option>
              {strategies.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>sweep
            <select value={sweep} onChange={(e) => setSweep(e.target.value)}>
              <option value="all">All</option>
              {sweeps.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>min trades
            <input type="number" min="0" value={minTrades}
              onChange={(e) => setMinTrades(Number(e.target.value) || 0)} />
          </label>
          <span className="hint">
            {enriched.length} setups
            {days != null && (loading
              ? ` · loading ${periodLabel.toLowerCase()} activity for the top ${PERIOD_ROWS} rows…`
              : ` · ${periodLabel.toLowerCase()} activity loaded for the top ${PERIOD_ROWS} rows`)}
          </span>
        </div>
        <table className="grid">
          <thead>
            <tr>
              <Th id="label" sort={sort} setSort={setSort} txt>Setup</Th>
              <Th id="symbol" sort={sort} setSort={setSort} txt>Asset</Th>
              <Th id="n_trades" sort={sort} setSort={setSort}>Trades</Th>
              <Th id="wins" sort={sort} setSort={setSort}>Wins</Th>
              <Th id="win_rate" sort={sort} setSort={setSort}>Win %</Th>
              <Th id="pf_low" sort={sort} setSort={setSort}>PF @low fee</Th>
              <Th id="pf_high" sort={sort} setSort={setSort}>PF @high fee</Th>
              <Th id="avg_trade" sort={sort} setSort={setSort}>Avg trade</Th>
              <Th id="total_return" sort={sort} setSort={setSort}>Return</Th>
              <Th id="max_dd" sort={sort} setSort={setSort}>Max DD</Th>
              <Th id="sharpe" sort={sort} setSort={setSort}>Sharpe</Th>
              {days != null && (
                <Th id="period_trades" sort={sort} setSort={setSort}>
                  Trades ({periodLabel})</Th>
              )}
              {days != null && (
                <Th id="period_pnl" sort={sort} setSort={setSort}>
                  P&L $ ({periodLabel})</Th>
              )}
              <th>Report</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => {
              const sig = strategySig(s.runs[0])
              const isFav = favs.some((f) => f.sig === sig)
              return (
              <tr key={s.key}>
                <td className="txt">
                  <span title={isFav
                    ? 'remove from favourites'
                    : 'favourite — pins this strategy on every asset review'}
                    onClick={() => toggleFav(sig, s.label)}
                    style={{
                      cursor: 'pointer', marginRight: 6,
                      color: isFav ? '#e8b93c' : 'var(--muted)',
                    }}>
                    {isFav ? '★' : '☆'}
                  </span>
                  {s.label}
                </td>
                <td className="txt">{s.symbol}</td>
                <td>{s.n_trades}</td>
                <td>{s.wins ?? '—'}</td>
                <td>{num(s.win_rate, 1)}</td>
                <td><Pf v={s.pf_low} /></td>
                <td><Pf v={s.pf_high} /></td>
                <td>{num(s.avg_trade, 3)}%</td>
                <td><Ret v={s.total_return} /></td>
                <td>{num(s.max_dd, 1)}%</td>
                <td>{num(s.sharpe)}</td>
                {days != null && (
                  <td>{s.period_trades ?? <span className="hint">…</span>}</td>
                )}
                {days != null && (
                  <td>{s.period_pnl != null
                    ? <Money v={s.period_pnl} />
                    : <span className="hint">…</span>}</td>
                )}
                <td>
                  {s.report_run
                    ? <a href={`/reports/${s.report_run.run_id}.html`} target="_blank" rel="noreferrer">open</a>
                    : <span className="hint">—</span>}
                </td>
              </tr>
              )
            })}
          </tbody>
        </table>
        {sorted.length > 200 && <p className="hint">showing first 200 — tighten the filters</p>}
      </div>
    </div>
  )
}
