import { useEffect, useMemo, useState } from 'react'
import Line from '../components/Line.jsx'
import Scatter from '../components/Scatter.jsx'
import { Pf, Ret } from '../components/bits.jsx'
import { groupSetups, isCrypto, num, setupLabel } from '../data.js'

const TABS = [
  ['all', 'All'],
  ['crypto', 'Crypto'],
  ['stocks', 'Stocks'],
]

const PERIODS = [
  ['all', 'All time', null],
  ['30', 'Last month', 30],
  ['7', 'Last week', 7],
  ['1', 'Last day', 1],
]

const SERIES_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3, #1baf7a)']

function slicedRebased(points, days) {
  if (!points?.length) return null
  let pts = points
  if (days != null) {
    const maxT = Date.parse(points[points.length - 1].t)
    const cutoff = maxT - days * 86400e3
    pts = points.filter((p) => Date.parse(p.t) >= cutoff)
  }
  if (pts.length < 2) return null
  const base = pts[0].v
  return pts.map((p) => ({ t: p.t, v: p.v / base }))
}

function PeriodPnL({ symbols, curves, wf, days, asset }) {
  const series = useMemo(() => {
    const out = []
    if (asset !== 'all') {
      const c = curves?.curves?.[asset]
      const s = slicedRebased(c?.points, days)
      if (s) out.push({ name: `${asset} best setup (in-sample)`, points: s })
      const wfSym = wf?.symbols?.find((x) => x.symbol === asset)
      const oos = slicedRebased(wfSym?.scenarios?.[0]?.equity, days)
      if (oos) out.push({ name: `${asset} walk-forward OOS`, points: oos })
    } else {
      for (const sym of symbols.slice(0, 3)) {
        const s = slicedRebased(curves?.curves?.[sym]?.points, days)
        if (s) out.push({ name: `${sym} best setup`, points: s })
      }
    }
    return out.map((s, i) => ({ ...s, color: SERIES_COLORS[i] }))
  }, [symbols, curves, wf, days, asset])

  return (
    <div className="card">
      <h3>P&L over selected period <span className="hint">(equity rebased to 1.0 at period start)</span></h3>
      {series.length === 0 ? (
        <p className="hint">no trades in this period for the selection</p>
      ) : (
        <>
          <div style={{ marginBottom: 6 }}>
            {series.map((s) => (
              <span key={s.name} className="chip">
                <span className="dot" style={{ background: s.color }} />
                {s.name}: <Ret v={100 * (s.points[s.points.length - 1].v - 1)} />
              </span>
            ))}
          </div>
          <Line yLabel="equity multiple" series={series} height={330} />
        </>
      )}
    </div>
  )
}

function ProcessingStatus({ onOpenExecute }) {
  const [jobs, setJobs] = useState(null)

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const r = await fetch('/api/jobs')
        if (alive && r.ok) setJobs(await r.json())
      } catch {
        if (alive) setJobs(null)
      }
    }
    poll()
    const t = setInterval(poll, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const running = (jobs || []).filter((j) => j.status === 'running')
  const recent = (jobs || []).filter((j) => j.status !== 'running').slice(0, 3)

  return (
    <div className="card">
      <h3>Processing</h3>
      {jobs === null ? (
        <p className="hint">job API offline — static snapshot only</p>
      ) : running.length === 0 && recent.length === 0 ? (
        <p className="hint">idle — nothing launched this API session</p>
      ) : (
        <table className="grid">
          <tbody>
            {running.map((j) => (
              <tr key={j.id}>
                <td className="txt"><span className="badge running">running</span></td>
                <td className="txt">{j.label}</td>
                <td className="txt hint">started {j.started_at?.replace('T', ' ').replace('+00:00', ' UTC')}</td>
              </tr>
            ))}
            {recent.map((j) => (
              <tr key={j.id}>
                <td className="txt"><span className={`badge ${j.status === 'done' ? 'done' : 'failed'}`}>{j.status}</span></td>
                <td className="txt">{j.label}</td>
                <td className="txt hint">finished {j.finished_at?.replace('T', ' ').replace('+00:00', ' UTC')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="hint" style={{ marginBottom: 0 }}>
        launch and inspect jobs on the{' '}
        <a href="#" onClick={(e) => { e.preventDefault(); onOpenExecute?.() }}>Execute page</a>
      </p>
    </div>
  )
}

function BestStrategy({ symbol, symbolRuns, wf }) {
  const setups = useMemo(() => {
    const grouped = groupSetups(symbolRuns).filter((s) => s.pf_low != null)
    const solid = grouped.filter((s) => s.n_trades >= 30)
    return (solid.length ? solid : grouped.filter((s) => s.n_trades >= 10))
      .sort((a, b) => b.pf_low - a.pf_low)
  }, [symbolRuns])

  const best = setups[0]
  const wfDoc = wf?.symbols?.find((s) => s.symbol === symbol)

  if (!best) {
    return (
      <div className="card">
        <h3>Best strategy — {symbol}</h3>
        <p className="hint">no setups with enough trades logged yet</p>
      </div>
    )
  }
  return (
    <div className="card">
      <h3>Best strategy — {symbol} <span className="hint">(in-sample, by PF at lowest fee)</span></h3>
      <p style={{ fontSize: 15, fontWeight: 600, margin: '4px 0 12px' }}>{best.label}</p>
      <div className="tiles">
        <div className="tile"><div className="k">Profit factor (low / high fee)</div>
          <div className="v"><Pf v={best.pf_low} /> <span className="hint">/</span> <Pf v={best.pf_high} /></div></div>
        <div className="tile"><div className="k">Win rate</div><div className="v">{num(best.win_rate, 1)}%</div>
          <div className="d">{best.n_trades} trades</div></div>
        <div className="tile"><div className="k">Return</div><div className="v"><Ret v={best.total_return} /></div>
          <div className="d">max DD {num(best.max_dd, 1)}%</div></div>
        <div className="tile"><div className="k">Sharpe</div><div className="v">{num(best.sharpe)}</div></div>
        {best.report_run && (
          <div className="tile"><div className="k">Chart report</div>
            <div className="d" style={{ marginTop: 8 }}>
              <a href={`/reports/${best.report_run.run_id}.html`} target="_blank" rel="noreferrer">open interactive report</a>
            </div></div>
        )}
      </div>
      {wfDoc ? (
        <>
          <h3 style={{ marginTop: 6 }}>Out-of-sample verdict (walk-forward)</h3>
          <table className="grid">
            <thead>
              <tr><th>Fee/side</th><th>OOS trades</th><th>OOS PF</th><th>OOS win rate</th><th>OOS return</th></tr>
            </thead>
            <tbody>
              {wfDoc.scenarios.map((s) => (
                <tr key={s.fee}>
                  <td>{(s.fee * 100).toFixed(2)}%</td>
                  <td>{s.aggregate.oos_trades}</td>
                  <td><Pf v={s.aggregate.oos_pf} /></td>
                  <td>{num(s.aggregate.oos_win_rate_pct, 1)}%</td>
                  <td><Ret v={s.aggregate.oos_return_pct} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint">walk-forward picks its own winner per fold — the OOS row judges the symbol, not this exact setup</p>
        </>
      ) : (
        <p className="hint">no walk-forward result for this symbol yet — run it from the Execute page</p>
      )}
    </div>
  )
}

export default function Overview({ runs: allRuns, generatedAt, onOpenExecute }) {
  const [minTrades, setMinTrades] = useState(20)
  const [tab, setTab] = useState('all')
  const [asset, setAsset] = useState('all')
  const [period, setPeriod] = useState('all')
  const [wf, setWf] = useState(null)
  const [curves, setCurves] = useState(null)

  useEffect(() => {
    ;(async () => {
      try {
        let r = await fetch('/api/walkforward')
        if (r.ok) {
          const d = await r.json()
          if (d.available) { setWf(d) } else {
            r = await fetch('/data/walkforward.json')
            if (r.ok) setWf(await r.json())
          }
        }
      } catch { /* no walk-forward yet */ }
      try {
        const r = await fetch('/data/curves.json')
        if (r.ok) setCurves(await r.json())
      } catch { /* no curves yet */ }
    })()
  }, [])

  const tabRuns = useMemo(
    () =>
      tab === 'all'
        ? allRuns
        : allRuns.filter((r) => isCrypto(r.symbol) === (tab === 'crypto')),
    [allRuns, tab],
  )
  const tabSymbols = useMemo(
    () => [...new Set(tabRuns.map((r) => r.symbol))].sort(),
    [tabRuns],
  )
  const runs = useMemo(
    () => (asset === 'all' ? tabRuns : tabRuns.filter((r) => r.symbol === asset)),
    [tabRuns, asset],
  )

  const setups = useMemo(() => groupSetups(runs), [runs])
  const viable = runs.filter((r) => (r.n_trades ?? 0) >= 30 && r.profit_factor != null)
  const best = [...viable].sort((a, b) => b.profit_factor - a.profit_factor)[0]

  const points = useMemo(
    () =>
      runs
        .filter((r) => (r.n_trades ?? 0) >= minTrades && r.profit_factor != null)
        .map((r) => ({
          run_id: r.run_id,
          x: r.n_trades,
          y: r.profit_factor,
          ret: r.total_return_pct,
          fee: r.params.fee,
          strategy: r.strategy,
          label: setupLabel(r),
        })),
    [runs, minTrades],
  )

  const top = useMemo(
    () =>
      groupSetups(runs.filter((r) => (r.n_trades ?? 0) >= 30))
        .filter((s) => s.pf_low != null)
        .sort((a, b) => b.pf_low - a.pf_low)
        .slice(0, 8),
    [runs],
  )

  return (
    <div>
      <h1 className="page-title">Overview</h1>
      <p className="page-sub">Registry snapshot · {generatedAt || 'live'}</p>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <div className="tabs" style={{ marginBottom: 18 }}>
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? 'active' : ''}
              onClick={() => { setTab(id); setAsset('all') }}>
              {label}
            </button>
          ))}
        </div>
        <div className="filters" style={{ marginBottom: 18 }}>
          <label>asset
            <select value={asset} onChange={(e) => setAsset(e.target.value)}>
              <option value="all">All ({tabSymbols.length})</option>
              {tabSymbols.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>time
            <select value={period} onChange={(e) => setPeriod(e.target.value)}>
              {PERIODS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
        </div>
      </div>

      {asset !== 'all' && <BestStrategy symbol={asset} symbolRuns={runs} wf={wf} />}

      <div className="tiles">
        <div className="tile"><div className="k">Backtests logged</div><div className="v">{runs.length.toLocaleString()}</div></div>
        <div className="tile"><div className="k">Distinct setups</div><div className="v">{setups.length.toLocaleString()}</div></div>
        <div className="tile"><div className="k">Assets</div><div className="v">{new Set(runs.map((r) => r.symbol)).size}</div></div>
        <div className="tile">
          <div className="k">Best profit factor (≥30 trades)</div>
          <div className="v">{best ? num(best.profit_factor) : '—'}</div>
          <div className="d">{best ? setupLabel(best) : 'no qualifying run yet'}</div>
        </div>
      </div>

      <ProcessingStatus onOpenExecute={onOpenExecute} />

      <div className="row2">
        <div className="card">
          <h3>Profit factor vs. trade count — every logged run</h3>
          <div className="filters">
            <label>min trades
              <input type="number" min="1" value={minTrades}
                onChange={(e) => setMinTrades(Number(e.target.value) || 1)} />
            </label>
            <span className="hint">{points.length} runs shown · full-period stats</span>
          </div>
          <Scatter points={points} height={330} />
        </div>
        <PeriodPnL
          symbols={[...new Set(top.map((s) => s.symbol))]}
          curves={curves}
          wf={wf}
          days={PERIODS.find(([id]) => id === period)?.[2] ?? null}
          asset={asset}
        />
      </div>

      <div className="card">
        <h3>Top setups (by profit factor at the lowest fee, ≥30 trades)</h3>
        <table className="grid">
          <thead>
            <tr>
              <th className="txt">Setup</th><th className="txt">Asset</th><th>Trades</th>
              <th>Win %</th><th>PF @low fee</th><th>PF @high fee</th><th>Return</th><th>Max DD</th><th>Sharpe</th>
            </tr>
          </thead>
          <tbody>
            {top.map((s) => (
              <tr key={s.key}>
                <td className="txt">{s.label}</td>
                <td className="txt">{s.symbol}</td>
                <td>{s.n_trades}</td>
                <td>{num(s.win_rate, 1)}</td>
                <td><Pf v={s.pf_low} /></td>
                <td><Pf v={s.pf_high} /></td>
                <td><Ret v={s.total_return} /></td>
                <td>{num(s.max_dd, 1)}%</td>
                <td>{num(s.sharpe)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
