import { useEffect, useMemo, useState } from 'react'
import Line from '../components/Line.jsx'
import { Pf, Ret } from '../components/bits.jsx'
import { num } from '../data.js'

const FEE_COLORS = ['var(--series-1)', 'var(--series-2)']
const feeName = (f) => `fee ${(f * 100).toFixed(2)}%/side`

const Money = ({ v }) => v == null ? '—' : (
  <span className={v >= 0 ? 'pos' : 'neg'}>
    {v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)}
  </span>
)

// month buckets in each market's local time (NY for stocks, UTC for crypto)
const marketMonth = (iso, symbol) => new Date(iso)
  .toLocaleDateString('en-CA', {
    timeZone: /USD[TC]$/.test(symbol) ? 'UTC' : 'America/New_York',
  }).slice(0, 7)

function pooledPf(pnls) {
  const wins = pnls.filter((p) => p > 0).reduce((a, b) => a + b, 0)
  const losses = -pnls.filter((p) => p < 0).reduce((a, b) => a + b, 0)
  if (!losses) return wins ? null : null
  return wins / losses
}

// month-by-month consistency of a FIXED approved setup: no re-selection to
// simulate, so the honest walk-forward view is simply how the config did in
// every calendar month, on its top league assets, at venue fees
function ApprovedConsistency() {
  const [approved, setApproved] = useState(null)
  const [sel, setSel] = useState('all')
  const [sym, setSym] = useState('all')
  const [doc, setDoc] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/favsetups')
        const favs = r.ok ? await r.json() : []
        const appr = favs.filter((f) => f.status === 'approved')
        setApproved(appr)
        if (appr.length === 1) setSel(appr[0].sig)
      } catch {
        setApproved([])
      }
    })()
  }, [])

  useEffect(() => {
    if (!approved?.length) return
    let alive = true
    setDoc(null); setErr(null); setSym('all')
    ;(async () => {
      try {
        const r = await fetch(`/api/journal?sig=${encodeURIComponent(sel)}`)
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
  }, [approved, sel])

  const model = useMemo(() => {
    if (!doc) return null
    const trades = doc.trades.filter((t) =>
      (sym === 'all' || t.symbol === sym) && t.pnl_usd != null)
    const byMonth = new Map()
    let cum = 0
    const curve = []
    for (const t of trades) {
      const key = marketMonth(t.entry_ts, t.symbol)
      if (!byMonth.has(key)) {
        byMonth.set(key, { key, n: 0, wins: 0, pnl: 0, pnls: [] })
      }
      const m = byMonth.get(key)
      m.n += 1
      m.pnl += t.pnl_usd
      m.pnls.push(t.pnl_usd)
      if (t.pnl_usd > 0) m.wins += 1
      cum += t.pnl_usd
      curve.push({ t: t.entry_ts, v: cum })
    }
    const months = [...byMonth.values()]
    for (const m of months) m.pf = pooledPf(m.pnls)
    const pos = months.filter((m) => m.pnl > 0).length
    return {
      trades: trades.length,
      pnl: cum,
      pf: pooledPf(trades.map((t) => t.pnl_usd)),
      months: months.sort((a, b) => b.key.localeCompare(a.key)),
      pos,
      curve,
    }
  }, [doc, sym])

  if (approved == null) return <p className="page-sub">loading…</p>
  if (approved.length === 0) {
    return (
      <div className="card">
        <p className="hint">
          nothing approved yet — approve setups on the Setups (League) page
          and this view fills itself in.
        </p>
      </div>
    )
  }
  if (err) return <div className="card"><span className="neg">{err}</span></div>

  return (
    <>
      <div className="filters" style={{ marginBottom: 12 }}>
        <label>setup
          <select value={sel} onChange={(e) => setSel(e.target.value)}>
            {approved.length > 1 && (
              <option value="all">All approved ({approved.length})</option>
            )}
            {approved.map((f) => (
              <option key={f.sig} value={f.sig}>{f.label}</option>
            ))}
          </select>
        </label>
        <label>asset
          <select value={sym} onChange={(e) => setSym(e.target.value)}>
            <option value="all">All ({doc?.symbols?.length ?? '…'})</option>
            {(doc?.symbols || []).map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
      </div>

      {!doc ? (
        <p className="hint">assembling trades (first load backtests each
          asset once, give it a minute)…</p>
      ) : (
        <>
          <div className="tiles">
            <div className="tile"><div className="k">Trades</div>
              <div className="v">{model.trades}</div></div>
            <div className="tile"><div className="k">Pooled PF (net fees)</div>
              <div className="v"><Pf v={model.pf} /></div></div>
            <div className="tile"><div className="k">Total P&L</div>
              <div className="v"><Money v={model.pnl} /></div></div>
            <div className="tile"><div className="k">Positive months</div>
              <div className="v">{model.pos}/{model.months.length}</div>
              <div className="d">the consistency number that matters</div></div>
          </div>

          {model.curve.length > 1 && (
            <div className="card">
              <h3>Cumulative P&L (USD, per-class trade sizes)</h3>
              <Line yLabel="cumulative P&L $" height={220} series={[{
                name: 'cumulative P&L $', color: 'var(--series-1)',
                points: model.curve,
              }]} />
            </div>
          )}

          <div className="card">
            <h3>Month by month</h3>
            <table className="grid">
              <thead>
                <tr>
                  <th className="txt">Month</th><th>Trades</th><th>Wins</th>
                  <th>PF</th><th>P&L $</th>
                </tr>
              </thead>
              <tbody>
                {model.months.map((m) => (
                  <tr key={m.key}>
                    <td className="txt">{m.key}</td>
                    <td>{m.n}</td>
                    <td>{m.wins}/{m.n}</td>
                    <td><Pf v={m.pf} /></td>
                    <td><Money v={m.pnl} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="hint">
              caveat: the whole backtest window includes the data these
              setups were discovered on — for the untainted verdicts use the
              League's holdout and post-discovery columns; this view is about
              month-to-month steadiness and drawdown texture.
            </p>
          </div>
        </>
      )}
    </>
  )
}

function AdaptiveGrid() {
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
    return <p className="page-sub">No adaptive walk-forward results yet — launch one from the Execute page.</p>
  }
  if (!doc || !current) return <p className="page-sub">loading…</p>

  const m = doc.method

  return (
    <>
      <p className="page-sub">
        Rolling {m.train_days}d train / {m.test_days}d test, step {m.step_days}d ·
        winner per fold = {m.selection} (min {m.min_train_trades} train trades) ·
        all metrics below are <b>out-of-sample</b> · generated {doc.generated_at}.
        There is no setup selector here by design: the process re-picks the
        winning setup every fold — the fold table shows what won when, and
        "distinct winners" tells you how unstable that choice was.
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
    </>
  )
}

const TABS = [
  ['approved', 'Approved setups'],
  ['adaptive', 'Adaptive grid'],
]

export default function WalkForward() {
  const [tab, setTab] = useState('approved')

  return (
    <div>
      <h1 className="page-title">Walk-forward validation</h1>
      <p className="page-sub">
        Two different questions: <b>Approved setups</b> — is each fixed,
        league-approved setup steady month after month? <b>Adaptive grid</b> —
        if you re-picked the best recent setup every month per symbol, would
        that process make money out-of-sample?
      </p>
      <div className="controls">
        <div className="tabs">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? 'active' : ''}
              onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {tab === 'approved' ? <ApprovedConsistency /> : <AdaptiveGrid />}
    </div>
  )
}
