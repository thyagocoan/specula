import { useEffect, useMemo, useRef, useState } from 'react'
import CandleChart from '../components/CandleChart.jsx'
import { Pf, Ret, Th, sortRows } from '../components/bits.jsx'
import { groupSetups, isCrypto, num, strategySig, useFavStrategies } from '../data.js'

const TABS = [
  ['all', 'All'],
  ['crypto', 'Crypto'],
  ['stocks', 'Stocks'],
]

const PERIODS = [
  ['all', 'All time', null],
  ['30', 'Month', 30],
  ['7', 'Week', 7],
  ['1', 'Day', 1],
]

const Money = ({ v }) => v == null ? '—' : (
  <span className={v >= 0 ? 'pos' : 'neg'}>
    {v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)}
  </span>
)

// out-of-sample trades + P&L for one symbol within the selected period,
// from the walk-forward equity curve (one point per stitched OOS trade)
function wfGlobalMaxT(wf) {
  let m = null
  for (const d of wf?.symbols || []) {
    for (const s of d.scenarios || []) {
      const e = s.equity
      if (e?.length) {
        const t = Date.parse(e[e.length - 1].t)
        if (!m || t > m) m = t
      }
    }
  }
  return m
}

function wfPeriod(wf, symbol, days) {
  const docs = (wf?.symbols || []).filter(
    (d) => d.symbol === symbol || d.symbol === `${symbol}·lab`)
  let best = null
  for (const d of docs) {
    const s = d.scenarios?.[0]
    if (s?.equity?.length && (!best || s.equity.length > best.equity.length)) {
      best = s
    }
  }
  if (!best) return { trades: null, pnl: null }
  let pts = best.equity
  if (days != null) {
    // window relative to the latest data day across ALL assets, so "last
    // day" means the same calendar window for every symbol
    const refT = wfGlobalMaxT(wf) ?? Date.parse(pts[pts.length - 1].t)
    pts = pts.filter((p) => Date.parse(p.t) >= refT - days * 86400e3)
  }
  if (!pts.length) return { trades: 0, pnl: 0 }
  if (pts.length === 1) return { trades: 1, pnl: 0 }
  return {
    trades: pts.length,
    pnl: 100 * (pts[pts.length - 1].v / pts[0].v - 1),
  }
}

const CHART_TF = { 1: '5min', 7: '30min', 30: '2h', all: '1d' }
const TF_SEC = {
  '1min': 60, '5min': 300, '15min': 900, '30min': 1800,
  '1h': 3600, '2h': 7200, '4h': 14400, '1d': 86400,
}

const fmtTime = (iso, tz) => new Date(iso).toLocaleString('en-AU', {
  timeZone: tz, day: '2-digit', month: 'short',
  hour: '2-digit', minute: '2-digit', hour12: false,
})

// seconds to ADD to a unix time so the chart's UTC-rendered axis reads as
// the chosen timezone (cached per hour — offsets only change at DST edges)
const _tzCache = new Map()
function tzOffset(tz, sec) {
  if (!tz || tz === 'UTC') return 0
  const bucket = `${tz}:${Math.floor(sec / 3600)}`
  let off = _tzCache.get(bucket)
  if (off === undefined) {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit',
    }).formatToParts(new Date(sec * 1000))
      .reduce((o, p) => ((o[p.type] = p.value), o), {})
    off = Date.UTC(+parts.year, +parts.month - 1, +parts.day,
      +parts.hour, +parts.minute) / 1000 - sec
    _tzCache.set(bucket, off)
  }
  return off
}

function SetupCurve({ runId, days, symbol, onClose }) {
  const [trades, setTrades] = useState(null)
  const [candles, setCandles] = useState(null)
  const [pl, setPl] = useState(null)
  const [err, setErr] = useState(null)
  const [tfSel, setTfSel] = useState(null)
  const [tzSel, setTzSel] = useState(null)
  const [focus, setFocus] = useState(null)
  const [ind, setInd] = useState({
    sma50: false, sma200: false, ema21: true, vwap: true, rsi: true,
  })
  const crypto = /USD[TC]$/.test(symbol)
  const marketTz = crypto ? 'UTC' : 'America/New_York'
  const tf = tfSel ?? CHART_TF[days ?? 'all']
  const tz = tzSel ?? marketTz // default: the asset's own market time
  const TZS = crypto
    ? [['UTC', 'UTC (market)'], ['Australia/Melbourne', 'Melbourne']]
    : [['America/New_York', 'New York (market)'],
       ['Australia/Melbourne', 'Melbourne']]

  useEffect(() => {
    setTrades(null); setPl(null); setErr(null)
    ;(async () => {
      try {
        const [tr, pr] = await Promise.all([
          fetch(`/api/trades/${runId}`),
          fetch(`/api/curve/${runId}`),
        ])
        if (!tr.ok) throw new Error('failed to load trade data')
        setTrades(await tr.json())
        if (pr.ok) setPl(await pr.json())
      } catch (e) {
        setErr(String(e.message || e))
      }
    })()
  }, [runId])

  // candles refetch alone on timeframe change, so a selected trade stays
  // selected while you zoom into finer candles
  useEffect(() => {
    setCandles(null)
    ;(async () => {
      try {
        const cr = await fetch(
          `/api/candles/${symbol}?tf=${tf}&indicators=1${days ? `&days=${days}` : ''}`)
        if (!cr.ok) throw new Error('failed to load candles')
        setCandles(await cr.json())
      } catch (e) {
        setErr(String(e.message || e))
      }
    })()
  }, [days, symbol, tf])

  useEffect(() => { setFocus(null) }, [runId, days])

  const windowTrades = useMemo(() => {
    if (!trades || !candles?.length) return null
    const startSec = candles[0].time
    return trades.filter((t) => Date.parse(t.entry_ts) / 1000 >= startSec)
  }, [trades, candles])

  // chart data with the axis re-based to the chosen timezone
  const displayCandles = useMemo(() => {
    if (!candles) return null
    return candles.map((c) => ({ ...c, time: c.time + tzOffset(tz, c.time) }))
  }, [candles, tz])

  const IND_DEFS = [
    ['ema21', 'EMA 21', '#4f83e0'],
    ['sma50', 'SMA 50', '#f0a13c'],
    ['sma200', 'SMA 200', '#c95b8f'],
    ['vwap', 'VWAP (session)', '#18a0a8'],
    ['rsi', 'RSI 14', '#8a68c9'],
  ]

  const indLines = useMemo(() => {
    if (!displayCandles) return { lines: [], rsi: null }
    const pick = (key) => displayCandles
      .filter((c) => c[key] != null)
      .map((c) => ({ time: c.time, value: c[key] }))
    const lines = IND_DEFS
      .filter(([key]) => key !== 'rsi' && ind[key])
      .map(([key, name, color]) => ({ name, color, points: pick(key) }))
      .filter((l) => l.points.length)
    return { lines, rsi: ind.rsi ? pick('rsi') : null }
  }, [displayCandles, ind])

  const markers = useMemo(() => {
    if (!windowTrades) return []
    const m = []
    for (const t of windowTrades) {
      const long = t.side === 'long'
      const ret = t.net_return_pct ?? t.return_pct
      const focused = focus != null && t.entry_ts === focus.entry_ts &&
        t.exit_ts === focus.exit_ts
      const eSec = Math.floor(Date.parse(t.entry_ts) / 1000)
      m.push({
        time: eSec + tzOffset(tz, eSec),
        position: long ? 'belowBar' : 'aboveBar',
        color: focused ? '#e8b93c' : long ? '#1baf7a' : '#e34948',
        shape: long ? 'arrowUp' : 'arrowDown',
        text: long ? 'L' : 'S',
        size: focused ? 2 : 1,
      })
      if (t.exit_ts) {
        const xSec = Math.floor(Date.parse(t.exit_ts) / 1000)
        m.push({
          time: xSec + tzOffset(tz, xSec),
          position: long ? 'aboveBar' : 'belowBar',
          color: focused ? '#e8b93c' : (ret ?? 0) >= 0 ? '#1baf7a' : '#e34948',
          shape: focused ? 'square' : 'circle',
          text: `${ret > 0 ? '+' : ''}${ret?.toFixed(2)}%`,
          size: focused ? 2 : 1,
        })
      }
    }
    return m
  }, [windowTrades, focus, tz])

  // zoom window around the selected trade (~40 bars either side)
  const range = useMemo(() => {
    if (!focus) return null
    const pad = 40 * (TF_SEC[tf] || 3600)
    const e = Math.floor(Date.parse(focus.entry_ts) / 1000)
    const x = focus.exit_ts ? Math.floor(Date.parse(focus.exit_ts) / 1000) : e
    return { from: e + tzOffset(tz, e) - pad, to: x + tzOffset(tz, x) + pad }
  }, [focus, tf, tz])

  // compound the stake trade by trade: each trade reinvests the running
  // balance, so the next trade uses the result of the previous P&L
  const ledger = useMemo(() => {
    if (!windowTrades?.length) return null
    const start = windowTrades[0].size_usd ?? 1000
    let equity = start
    const rows = windowTrades.map((t) => {
      const stake = equity
      const pnl = t.net_return_pct != null ? stake * t.net_return_pct / 100 : null
      if (pnl != null) equity += pnl
      return { ...t, stake, pnl, equity }
    })
    return { rows, start, final: equity }
  }, [windowTrades])

  return (
    <div className="card">
      <h3>
        {symbol} — triggers on the chart
        <button className="btn ghost" style={{ float: 'right' }}
          onClick={onClose}>close</button>
      </h3>
      {pl && <p className="hint" style={{ margin: '0 0 6px' }}>{pl.label}</p>}
      {err && <p className="hint">{err}</p>}
      {!candles && !err && <p className="hint">loading chart + trades…</p>}
      {candles && (
        <>
          <div className="controls" style={{ marginBottom: 6 }}>
            <div className="select-pill">
              <select value={tf} onChange={(e) => setTfSel(e.target.value)}
                aria-label="chart timeframe">
                {Object.keys(TF_SEC).map((t) => (
                  <option key={t} value={t}>{t} candles</option>
                ))}
              </select>
            </div>
            <div className="select-pill">
              <select value={tz} onChange={(e) => setTzSel(e.target.value)}
                aria-label="chart timezone">
                {TZS.map(([id, label]) => (
                  <option key={id} value={id}>chart time: {label}</option>
                ))}
              </select>
            </div>
            <span className="chip">
              triggers in period: <b style={{ marginLeft: 4 }}>{windowTrades?.length ?? 0}</b>
            </span>
            {ledger && (
              <span className="chip">net P&L in period:{' '}
                <Money v={ledger.final - ledger.start} />{' '}
                <span className="hint">on ${num(ledger.start, 0)} start,
                  compounded, {crypto ? 'Binance' : 'IBKR'} fees</span>
              </span>
            )}
          </div>
          <div style={{ marginBottom: 6 }}>
            {IND_DEFS.map(([key, name, color]) => (
              <label key={key} className="chip"
                style={{ cursor: 'pointer', opacity: ind[key] ? 1 : 0.55 }}>
                <input type="checkbox" checked={ind[key]}
                  onChange={() => setInd({ ...ind, [key]: !ind[key] })}
                  style={{ marginRight: 5 }} />
                <span className="dot" style={{ background: color }} />
                {name}
              </label>
            ))}
          </div>
          <p className="hint" style={{ margin: '0 0 6px' }}>
            ▲ L = long entry (signal fired) · ▼ S = short entry ·
            ● = exit, labelled with the trade's net P&L % · gold ■ = the trade
            selected below — click a trigger row to zoom to it, click again to
            zoom out · RSI draws as the band at the bottom (dotted lines 30/70)
          </p>
          <CandleChart candles={displayCandles} markers={markers} range={range}
            lines={indLines.lines} rsi={indLines.rsi} />
          <h3 style={{ marginTop: 14 }}>Trigger log{' '}
            <span className="hint">(cross-check in TradingView — market time is
              {crypto ? ' UTC' : ' New York'} · P&L is net of{' '}
              {crypto ? 'Binance' : 'IBKR'} fees · each trade stakes the
              running balance)</span></h3>
          <table className="grid">
            <thead>
              <tr>
                <th className="txt">Entry (Melbourne)</th>
                <th className="txt">Entry ({crypto ? 'UTC' : 'New York'})</th>
                <th className="txt">Side</th><th>Entry px</th>
                <th className="txt">Exit ({crypto ? 'UTC' : 'New York'})</th>
                <th>Exit px</th><th>Stake $</th><th>P&L %</th>
                <th>P&L $</th><th>Balance $</th>
              </tr>
            </thead>
            <tbody>
              {(ledger?.rows || []).slice().reverse().slice(0, 100).map((t, i) => {
                const focused = focus != null && t.entry_ts === focus.entry_ts &&
                  t.exit_ts === focus.exit_ts
                return (
                  <tr key={i} className="selectable"
                    style={focused ? { background: 'rgba(232,185,60,.14)' } : undefined}
                    onClick={() => setFocus(focused ? null : t)}>
                    <td className="txt">{fmtTime(t.entry_ts, 'Australia/Melbourne')}</td>
                    <td className="txt">{fmtTime(t.entry_ts, marketTz)}</td>
                    <td className="txt">{t.side}</td>
                    <td>{t.entry_price}</td>
                    <td className="txt">{t.exit_ts ? fmtTime(t.exit_ts, marketTz) : 'open'}</td>
                    <td>{t.exit_price ?? '—'}</td>
                    <td>{num(t.stake, 2)}</td>
                    <td>{t.net_return_pct != null ? <Ret v={t.net_return_pct} /> : '—'}</td>
                    <td><Money v={t.pnl} /></td>
                    <td>{num(t.equity, 2)}</td>
                  </tr>
                )
              })}
            </tbody>
            {ledger && (
              <tfoot>
                <tr>
                  <td className="txt" colSpan={6}>
                    <b>Total — {ledger.rows.length} trades, started
                      ${num(ledger.start, 0)}</b>
                  </td>
                  <td />
                  <td>
                    <Ret v={100 * (ledger.final / ledger.start - 1)} />
                  </td>
                  <td><Money v={ledger.final - ledger.start} /></td>
                  <td><b>{num(ledger.final, 2)}</b></td>
                </tr>
              </tfoot>
            )}
          </table>
        </>
      )}
    </div>
  )
}

function FavStrip({ favs, onOpen, onRemove }) {
  if (!favs.length) return null
  return (
    <div className="card">
      <h3>Favourite strategies{' '}
        <span className="hint">(click to compare across every asset)</span></h3>
      <div>
        {favs.map((f) => (
          <span key={f.sig} className="chip" style={{ marginBottom: 6 }}>
            <a href="#" onClick={(e) => { e.preventDefault(); onOpen(f) }}>
              ★ {f.label}
            </a>
            <button className="btn ghost" title="remove favourite"
              style={{ marginLeft: 6, padding: '0 6px' }}
              onClick={() => onRemove(f.sig)}>×</button>
          </span>
        ))}
      </div>
    </div>
  )
}

// one favourite strategy applied to every asset that has a logged run for it
function StrategyBoard({ sig, label, runs, onBack, onAsset }) {
  const [sort, setSort] = useState({ col: 'pf_low', dir: 'desc' })
  const rows = useMemo(() => {
    const groups = groupSetups(runs.filter((r) => strategySig(r) === sig))
    const bySym = new Map()
    for (const g of groups) {
      const prev = bySym.get(g.symbol)
      if (!prev || (g.pf_low ?? -1) > (prev.pf_low ?? -1)) bySym.set(g.symbol, g)
    }
    return [...bySym.values()]
  }, [runs, sig])
  const sorted = useMemo(() => sortRows(rows, sort.col, sort.dir), [rows, sort])
  const winners = rows.filter((r) => (r.pf_low ?? 0) > 1).length

  return (
    <>
      <p style={{ margin: '0 0 14px' }}>
        <button className="btn ghost" onClick={onBack}>← back</button>
      </p>
      <div className="card">
        <h3>★ {label} — across all assets</h3>
        <p className="hint" style={{ marginTop: 0 }}>
          logged on <b>{rows.length}</b> assets · profitable (PF&gt;1 @low fee)
          on <b>{winners}</b> · assets without a row were never swept with this
          exact setup · in-sample numbers — check the asset's walk-forward
          verdict before trusting it · click a row for the full asset review
        </p>
        <table className="grid">
          <thead>
            <tr>
              <Th id="symbol" sort={sort} setSort={setSort} txt>Asset</Th>
              <Th id="n_trades" sort={sort} setSort={setSort}>Trades</Th>
              <Th id="win_rate" sort={sort} setSort={setSort}>Win %</Th>
              <Th id="pf_low" sort={sort} setSort={setSort}>PF @low fee</Th>
              <Th id="pf_high" sort={sort} setSort={setSort}>PF @high fee</Th>
              <Th id="total_return" sort={sort} setSort={setSort}>Return</Th>
              <Th id="max_dd" sort={sort} setSort={setSort}>Max DD</Th>
              <Th id="sharpe" sort={sort} setSort={setSort}>Sharpe</Th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => (
              <tr key={s.symbol} className="selectable"
                onClick={() => onAsset(s.symbol)}>
                <td className="txt"><b>{s.symbol}</b></td>
                <td>{s.n_trades}</td>
                <td>{num(s.win_rate, 1)}</td>
                <td><Pf v={s.pf_low} /></td>
                <td><Pf v={s.pf_high} /></td>
                <td><Ret v={s.total_return} /></td>
                <td>{num(s.max_dd, 1)}%</td>
                <td>{num(s.sharpe, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function AssetReview({ symbol, runs, wf, days, favs, toggleFav }) {
  const [selectedRun, setSelectedRun] = useState(null)
  const [runTrades, setRunTrades] = useState({})
  const fetched = useRef(new Set())
  // favourited strategies are pinned on top for every asset, even when they
  // wouldn't make this asset's top 15 by profit factor
  const setups = useMemo(() => {
    const all = groupSetups(runs.filter((r) => r.symbol === symbol))
      .filter((s) => s.pf_low != null)
      .sort((a, b) => b.pf_low - a.pf_low)
    const favSigs = new Set(favs.map((f) => f.sig))
    const isFav = (s) => favSigs.has(strategySig(s.runs[0]))
    return [...all.filter(isFav), ...all.filter((s) => !isFav(s)).slice(0, 15)]
  }, [runs, symbol, favs])
  const wfDocs = (wf?.symbols || []).filter(
    (d) => d.symbol === symbol || d.symbol === `${symbol}·lab`)

  // the setup curves/journal treat as this asset's best (top PF, ≥30 trades)
  const favKey = useMemo(() => {
    const byPf = [...setups].sort((a, b) => b.pf_low - a.pf_low)
    return (byPf.find((s) => s.n_trades >= 30) ?? byPf[0])?.key
  }, [setups])

  // the setup with the most winning trades
  const winnerKey = useMemo(() => {
    let best = null
    for (const s of setups) {
      if (s.wins != null && (!best || s.wins > best.wins)) best = s
    }
    return best?.key
  }, [setups])

  // pull each setup's real trades (few at a time; server caches them) so the
  // table can show activity for whatever time window is selected
  useEffect(() => {
    let alive = true
    const ids = setups.map((s) => s.byFee?.[s.fees?.[0]]?.run_id)
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
  }, [setups])

  // "last week" etc. is anchored to the newest trade of this asset, so the
  // window matches the data's last day, not the wall clock
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

  const periodLabel = PERIODS.find(([, , d]) => d === days)?.[1] ?? 'All time'

  return (
    <>
      {wfDocs.length > 0 && (
        <div className="card">
          <h3>Out-of-sample verdict (walk-forward) — the numbers that count</h3>
          <table className="grid">
            <thead>
              <tr><th className="txt">Source</th><th>Fee/side</th><th>OOS trades</th>
                <th>OOS PF</th><th>Win %</th><th>OOS return</th></tr>
            </thead>
            <tbody>
              {wfDocs.flatMap((d) => d.scenarios.map((s) => (
                <tr key={d.symbol + s.fee}>
                  <td className="txt">{d.symbol.endsWith('·lab') ? 'lab strategies' : 'core grid'}</td>
                  <td>{(s.fee * 100).toFixed(2)}%</td>
                  <td>{s.aggregate.oos_trades}</td>
                  <td><Pf v={s.aggregate.oos_pf} /></td>
                  <td>{num(s.aggregate.oos_win_rate_pct, 1)}</td>
                  <td><Ret v={s.aggregate.oos_return_pct} /></td>
                </tr>
              )))}
            </tbody>
          </table>
        </div>
      )}

      {selectedRun && (
        <SetupCurve runId={selectedRun} days={days} symbol={symbol}
          onClose={() => setSelectedRun(null)} />
      )}

      <div className="card">
        <h3>Top strategies for {symbol}{' '}
          <span className="hint">(☆ = favourite a strategy to compare it across
            every asset · click a row for its chart, trigger log and period
            activity)</span></h3>
        <table className="grid">
          <thead>
            <tr>
              <th className="txt">Setup</th><th>Trades</th><th>Wins</th>
              <th>Win %</th>
              <th>PF @low fee</th><th>PF @high fee</th><th>Return</th>
              <th>Max DD</th><th>Sharpe</th>
              <th>Trades ({periodLabel})</th><th>P&L $ ({periodLabel})</th>
            </tr>
          </thead>
          <tbody>
            {setups.map((s) => {
              const runId = s.byFee?.[s.fees?.[0]]?.run_id
              const tr = runTrades[runId]
              const sig = strategySig(s.runs[0])
              const isFav = favs.some((f) => f.sig === sig)
              let pn = null, pPnl = null
              if (tr) {
                const win = (days != null && refT)
                  ? tr.filter((t) => Date.parse(t.entry_ts) >= refT - days * 86400e3)
                  : tr
                pn = win.length
                pPnl = win.reduce((a, t) => a + (t.pnl_usd || 0), 0)
              }
              return (
                <tr key={s.key} className={runId ? 'selectable' : ''}
                  onClick={() => runId && setSelectedRun(runId)}>
                  <td className="txt">
                    <span title={isFav
                      ? 'remove from favourites'
                      : 'favourite — compare this strategy across every asset'}
                      onClick={(e) => { e.stopPropagation(); toggleFav(sig, s.label) }}
                      style={{
                        cursor: 'pointer', marginRight: 6,
                        color: isFav ? '#e8b93c' : 'var(--muted)',
                      }}>
                      {isFav ? '★' : '☆'}
                    </span>
                    {s.key === favKey && (
                      <span className="badge done"
                        title="this asset's best setup — used by curves & journal"
                        style={{ marginRight: 6 }}>best</span>
                    )}
                    {s.key === winnerKey && (
                      <span className="badge done"
                        title="most winning trades on this asset"
                        style={{ marginRight: 6, background: '#4f83e0' }}>
                        most wins</span>
                    )}
                    {s.label}
                  </td>
                  <td>{s.n_trades}</td>
                  <td>{s.wins ?? '—'}</td>
                  <td>{num(s.win_rate, 1)}</td>
                  <td><Pf v={s.pf_low} /></td>
                  <td><Pf v={s.pf_high} /></td>
                  <td><Ret v={s.total_return} /></td>
                  <td>{num(s.max_dd, 1)}%</td>
                  <td>{num(s.sharpe, 2)}</td>
                  <td>{tr ? pn : <span className="hint">…</span>}</td>
                  <td>{tr ? <Money v={pPnl} /> : <span className="hint">…</span>}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

export default function Overview({ runs: allRuns, generatedAt, asset, setAsset }) {
  const [tab, setTab] = useState('all')
  const [period, setPeriod] = useState('all')
  const [sort, setSort] = useState({ col: 'pf_low', dir: 'desc' })
  const [wf, setWf] = useState(null)
  const [board, setBoard] = useState(null) // {sig, label} → cross-asset view
  const [favs, toggleFav] = useFavStrategies()

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/walkforward')
        if (r.ok) {
          const d = await r.json()
          if (d.available) setWf(d)
        }
      } catch { /* none yet */ }
    })()
  }, [])

  const days = PERIODS.find(([id]) => id === period)?.[2] ?? null

  const tabRuns = useMemo(
    () => (tab === 'all' ? allRuns
      : allRuns.filter((r) => isCrypto(r.symbol) === (tab === 'crypto'))),
    [allRuns, tab],
  )
  const tabSymbols = useMemo(
    () => [...new Set(tabRuns.map((r) => r.symbol))].sort(),
    [tabRuns],
  )

  const rows = useMemo(() => {
    const grouped = groupSetups(tabRuns.filter((r) => (r.n_trades ?? 0) >= 30))
      .filter((s) => s.pf_low != null)
    // one row per asset: its best setup, plus period OOS stats
    const bestByAsset = new Map()
    for (const s of grouped) {
      const prev = bestByAsset.get(s.symbol)
      if (!prev || s.pf_low > prev.pf_low) bestByAsset.set(s.symbol, s)
    }
    return [...bestByAsset.values()].map((s) => {
      const p = wfPeriod(wf, s.symbol, days)
      return { ...s, period_trades: p.trades, period_pnl: p.pnl }
    })
  }, [tabRuns, wf, days])

  // with a time window selected, show only assets that actually triggered
  const visible = useMemo(
    () => (days == null ? rows : rows.filter((r) => (r.period_trades || 0) > 0)),
    [rows, days],
  )
  const sorted = useMemo(() => sortRows(visible, sort.col, sort.dir),
    [visible, sort])
  const totTrades = visible.reduce((a, r) => a + (r.period_trades || 0), 0)
  const periodLabel = PERIODS.find(([id]) => id === period)?.[1]

  return (
    <div>
      <h1 className="page-title">Overview</h1>
      <p className="page-sub">Registry snapshot · {generatedAt || 'live'}</p>
      <div className="controls">
        <div className="tabs">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? 'active' : ''}
              onClick={() => { setTab(id); setAsset('all') }}>
              {label}
            </button>
          ))}
        </div>
        <div className="select-pill">
          <select value={asset} onChange={(e) => setAsset(e.target.value)}
            aria-label="asset">
            <option value="all">All assets ({tabSymbols.length})</option>
            {tabSymbols.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
        <div className="tabs">
          {PERIODS.map(([id, label]) => (
            <button key={id} className={period === id ? 'active' : ''}
              onClick={() => {
                setPeriod(id)
                if (id !== 'all') setSort({ col: 'period_pnl', dir: 'desc' })
              }}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {board ? (
        <StrategyBoard sig={board.sig} label={board.label} runs={allRuns}
          onBack={() => setBoard(null)}
          onAsset={(s) => { setBoard(null); setAsset(s) }} />
      ) : asset !== 'all' ? (
        <>
          <p style={{ margin: '0 0 14px' }}>
            <button className="btn ghost" onClick={() => setAsset('all')}>
              ← all assets
            </button>
          </p>
          <FavStrip favs={favs} onOpen={setBoard}
            onRemove={(sig) => toggleFav(sig)} />
          <AssetReview symbol={asset} runs={tabRuns} wf={wf} days={days}
            favs={favs} toggleFav={toggleFav} />
        </>
      ) : (
        <>
        <FavStrip favs={favs} onOpen={setBoard}
          onRemove={(sig) => toggleFav(sig)} />
        <div className="card">
          <h3>Top setups — best per asset</h3>
          <p className="hint" style={{ marginTop: 0 }}>
            {days == null
              ? `${visible.length} assets with qualifying setups (≥30 trades)`
              : `${visible.length} assets triggered a setup in the last
                 ${periodLabel.toLowerCase()} (of ${rows.length} tracked)`} ·
            out-of-sample daytrades: <b>{totTrades.toLocaleString()}</b> ·
            click a symbol for the full review
          </p>
          <table className="grid">
            <thead>
              <tr>
                <Th id="symbol" sort={sort} setSort={setSort} txt>Asset</Th>
                <th className="txt">Best setup (in-sample)</th>
                <Th id="n_trades" sort={sort} setSort={setSort}>Trades</Th>
                <Th id="win_rate" sort={sort} setSort={setSort}>Win %</Th>
                <Th id="pf_low" sort={sort} setSort={setSort}>PF @low fee</Th>
                <Th id="pf_high" sort={sort} setSort={setSort}>PF @high fee</Th>
                <Th id="total_return" sort={sort} setSort={setSort}>Return</Th>
                <Th id="period_trades" sort={sort} setSort={setSort}>
                  OOS trades ({periodLabel})</Th>
                <Th id="period_pnl" sort={sort} setSort={setSort}>
                  OOS P&L ({periodLabel})</Th>
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 300).map((s) => (
                <tr key={s.symbol} className="selectable"
                  onClick={() => setAsset(s.symbol)}>
                  <td className="txt"><a href="#" onClick={(e) => {
                    e.preventDefault(); setAsset(s.symbol)
                  }}><b>{s.symbol}</b></a></td>
                  <td className="txt">{s.label}</td>
                  <td>{s.n_trades}</td>
                  <td>{num(s.win_rate, 1)}</td>
                  <td><Pf v={s.pf_low} /></td>
                  <td><Pf v={s.pf_high} /></td>
                  <td><Ret v={s.total_return} /></td>
                  <td>{s.period_trades ?? '—'}</td>
                  <td>{s.period_pnl != null ? <Ret v={s.period_pnl} /> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
      )}
    </div>
  )
}
