import { useEffect, useMemo, useState } from 'react'
import Line from '../components/Line.jsx'
import { Pf, Ret } from '../components/bits.jsx'
import { num } from '../data.js'

const FEE_COLORS = ['var(--series-1)', 'var(--series-2)']
const feeName = (f) => `fee ${(f * 100).toFixed(2)}%/side`

export default function WalkForward() {
  const [doc, setDoc] = useState(null)
  const [err, setErr] = useState(null)
  const [symbol, setSymbol] = useState(null)

  useEffect(() => {
    ;(async () => {
      try {
        let r = await fetch('/api/walkforward')
        if (r.ok) {
          const d = await r.json()
          if (d.available) return setDoc(d)
        }
        r = await fetch('/data/walkforward.json')
        if (r.ok) return setDoc(await r.json())
        setErr('not-run')
      } catch {
        setErr('not-run')
      }
    })()
  }, [])

  const symbols = useMemo(() => (doc?.symbols || []).map((s) => s.symbol), [doc])
  const current = useMemo(() => {
    const want = symbol ?? (symbols.includes('ALL-EQUITIES') ? 'ALL-EQUITIES' : symbols[0])
    return (doc?.symbols || []).find((s) => s.symbol === want)
  }, [doc, symbol, symbols])

  if (err === 'not-run') {
    return (
      <div>
        <h1 className="page-title">Walk-forward</h1>
        <p className="page-sub">No walk-forward results yet — launch one from the Execute page.</p>
      </div>
    )
  }
  if (!doc || !current) return <p className="page-sub">loading…</p>

  const m = doc.method

  return (
    <div>
      <h1 className="page-title">Walk-forward validation</h1>
      <p className="page-sub">
        Rolling {m.train_days}d train / {m.test_days}d test, step {m.step_days}d ·
        winner per fold = {m.selection} (min {m.min_train_trades} train trades) ·
        all metrics below are <b>out-of-sample</b> · generated {doc.generated_at}
      </p>

      <div className="filters">
        <label>symbol
          <select value={current.symbol} onChange={(e) => setSymbol(e.target.value)}>
            {symbols.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        {current.symbol === 'ALL-EQUITIES' && (
          <span className="hint">stitched OOS trades of every equity symbol — the portfolio view</span>
        )}
      </div>

      {current.scenarios.map((s) => (
        <div key={s.fee}>
          <h3 style={{ margin: '18px 0 10px' }}>{feeName(s.fee)}</h3>
          <div className="tiles">
            <div className="tile"><div className="k">OOS trades</div><div className="v">{s.aggregate.oos_trades}</div></div>
            <div className="tile"><div className="k">OOS profit factor</div>
              <div className="v"><Pf v={s.aggregate.oos_pf} /></div></div>
            <div className="tile"><div className="k">OOS win rate</div><div className="v">{num(s.aggregate.oos_win_rate_pct, 1)}%</div></div>
            <div className="tile"><div className="k">OOS return (compounded)</div>
              <div className="v"><Ret v={s.aggregate.oos_return_pct} /></div></div>
            {s.aggregate.folds_total != null && (
              <div className="tile"><div className="k">Param stability</div>
                <div className="v">{s.aggregate.distinct_winners}</div>
                <div className="d">distinct winners over {s.aggregate.folds_with_winner} folds</div></div>
            )}
          </div>
        </div>
      ))}

      <div className="card">
        <h3>Out-of-sample equity — {current.symbol} (1.0 = start, trade-by-trade compounding)</h3>
        <Line yLabel="equity multiple" series={current.scenarios.map((s, i) => ({
          name: feeName(s.fee),
          color: FEE_COLORS[i % FEE_COLORS.length],
          points: s.equity,
        })).filter((s) => s.points.length > 1)} />
      </div>

      {current.scenarios.filter((s) => s.folds?.length).map((s) => (
        <div className="card" key={s.fee}>
          <h3>Folds — {feeName(s.fee)}</h3>
          <table className="grid">
            <thead>
              <tr>
                <th className="txt">Train window</th><th className="txt">Test window</th>
                <th className="txt">Winner (picked on train only)</th>
                <th>Train PF</th><th>Train n</th><th>Test n</th><th>Test PF</th><th>Test return</th>
              </tr>
            </thead>
            <tbody>
              {s.folds.map((f, i) => (
                <tr key={i}>
                  <td className="txt">{f.train_start} → {f.train_end}</td>
                  <td className="txt">{f.train_end} → {f.test_end}</td>
                  <td className="txt">{f.winner ?? <span className="hint">no qualifier</span>}</td>
                  <td>{num(f.train_pf)}</td>
                  <td>{f.train_trades ?? '—'}</td>
                  <td>{f.test_trades ?? '—'}</td>
                  <td><Pf v={f.test_pf} /></td>
                  <td><Ret v={f.test_return_pct} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}
