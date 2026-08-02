import { useEffect, useMemo, useState } from 'react'
import Scatter from '../components/Scatter.jsx'
import { Pf, Ret } from '../components/bits.jsx'
import { groupSetups, num, setupLabel } from '../data.js'

// The research funnel: each stage narrows the candidate set before the next
// spends compute or statistical credibility on it.
const FLOW = [
  {
    n: 1,
    title: 'Data lake',
    tags: [],
    desc: '1-minute bars (Binance public dumps, Alpaca SIP), checksum-verified, raw → bronze → silver Parquet. Every other timeframe is resampled locally — never re-fetched. Equity bars are session-aligned (09:30 ET anchor).',
  },
  {
    n: 2,
    title: 'Broad discovery sweep',
    tags: ['single-tf-v1', 'mtf-v1', 'mtf-equities-v1'],
    desc: 'Both strategies × 19 setup→exec timeframe pairs × core variants × cost scenarios. Stops stay deliberately narrow here (structural for FFFD, a small grid for Didi): every extra grid dimension multiplies the chance the best cell is a fluke. Purpose: find where there is life at all, per asset. Lesson so far: single-timeframe versions mostly lose; higher-TF setup with lower-TF stop-break execution is what works.',
  },
  {
    n: 3,
    title: 'Exit refinement on survivors',
    tags: ['trail-fffd-v1'],
    desc: 'Only for setups that showed life: trailing stops, R-multiple targets, MFE (maximum favorable excursion) analysis to size trail distances from data instead of guessing. Lesson so far: fixed 1R target is best risk-adjusted; a 1–1.5% trail maximizes absolute return; trails ≤0.5% destroy the setup.',
  },
  {
    n: 4,
    title: 'Condition filters',
    tags: ['rsi-filter-v1'],
    desc: 'Multi-timeframe RSI (daily → 15m) snapshotted at each entry, look-ahead safe. Accept a filter only if the effect is monotone across buckets and economically sensible (e.g. don’t fade a move that is still extreme on the daily) — never a cherry-picked magic bucket.',
  },
  {
    n: 5,
    title: 'Walk-forward validation',
    tags: [],
    desc: 'Rolling 120d train / 30d test; the winner is picked on training data only and judged on unseen data. This is the only number to trust — in-sample results are always inflated. Verdict so far: the FFFD family survives at futures fees (OOS PF 1.42), dies at spot fees. See the Walk-forward page.',
  },
  {
    n: 6,
    title: 'Ready for paper trading',
    tags: [],
    desc: 'Only walk-forward survivors qualify, and only under the economics they survived at (futures/maker-level costs). Nothing here yet has earned real capital — the funnel exists to make sure whatever does, earned it honestly.',
  },
]

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

export default function Overview({ runs, generatedAt, onOpenExecute }) {
  const [minTrades, setMinTrades] = useState(20)
  const setups = useMemo(() => groupSetups(runs), [runs])
  const symbols = useMemo(() => new Set(runs.map((r) => r.symbol)), [runs])

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
      <div className="tiles">
        <div className="tile"><div className="k">Backtests logged</div><div className="v">{runs.length.toLocaleString()}</div></div>
        <div className="tile"><div className="k">Distinct setups</div><div className="v">{setups.length.toLocaleString()}</div></div>
        <div className="tile"><div className="k">Assets</div><div className="v">{symbols.size}</div></div>
        <div className="tile">
          <div className="k">Best profit factor (≥30 trades)</div>
          <div className="v">{best ? num(best.profit_factor) : '—'}</div>
          <div className="d">{best ? setupLabel(best) : 'no qualifying run yet'}</div>
        </div>
      </div>

      <ProcessingStatus onOpenExecute={onOpenExecute} />

      <div className="card">
        <h3>Research flow — how a setup earns trust here</h3>
        <ol className="flow">
          {FLOW.map((s) => {
            const count = s.tags.length
              ? runs.filter((r) => s.tags.includes(r.sweep_tag)).length
              : null
            return (
              <li key={s.n}>
                <div className="flow-head">
                  <span className="flow-n">{s.n}</span>
                  <b>{s.title}</b>
                  {count != null && (
                    <span className="hint">{count.toLocaleString()} runs logged</span>
                  )}
                </div>
                <p>{s.desc}</p>
              </li>
            )
          })}
        </ol>
      </div>

      <div className="card">
        <h3>Profit factor vs. trade count — every logged run</h3>
        <div className="filters">
          <label>min trades
            <input type="number" min="1" value={minTrades}
              onChange={(e) => setMinTrades(Number(e.target.value) || 1)} />
          </label>
          <span className="hint">{points.length} runs shown · dots above the dashed line are net winners after costs</span>
        </div>
        <Scatter points={points} />
      </div>

      <div className="card">
        <h3>Top setups (by profit factor at the lowest fee, ≥30 trades)</h3>
        <table className="grid">
          <thead>
            <tr>
              <th className="txt">Setup</th><th className="txt">Asset</th><th>Trades</th>
              <th>Win %</th><th>PF @0.04%</th><th>PF @0.10%</th><th>Return</th><th>Max DD</th><th>Sharpe</th>
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
