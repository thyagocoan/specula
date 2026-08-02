import { useEffect, useMemo, useState } from 'react'
import { Pf, Th, sortRows } from '../components/bits.jsx'
import { num } from '../data.js'

const Money = ({ v }) => v == null ? '—' : (
  <span className={v >= 0 ? 'pos' : 'neg'}>
    {v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)}
  </span>
)

// Setup League: candidate setups validated across the whole universe with a
// holdout split. The holdout columns are the only ones that matter for
// approval — train numbers include the data the setups were discovered on.
export default function League() {
  const [doc, setDoc] = useState(null)
  const [favs, setFavs] = useState([])
  const [err, setErr] = useState(null)
  const [msg, setMsg] = useState(null)
  const [sort, setSort] = useState({ col: 'hold_pf', dir: 'desc' })
  const [cls, setCls] = useState('all')

  async function load() {
    try {
      const [lr, fr] = await Promise.all([
        fetch('/api/league'), fetch('/api/favsetups'),
      ])
      if (lr.ok) setDoc(await lr.json())
      if (fr.ok) setFavs(await fr.json())
    } catch (e) {
      setErr(String(e.message || e))
    }
  }

  useEffect(() => { load() }, [])

  async function setStatus(row, status) {
    setMsg(null)
    const r = await fetch('/api/favsetups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(status == null
        ? { sig: row.sig, remove: true }
        : { sig: row.sig, label: row.label, params: row.params, status }),
    })
    setMsg(r.ok
      ? { ok: true, text: status === 'approved' ? `approved: ${row.label}` : `updated: ${row.label}` }
      : { ok: false, text: 'update failed' })
    load()
  }

  async function launch() {
    setMsg(null)
    const r = await fetch('/api/jobs/setup_league', { method: 'POST' })
    const d = await r.json().catch(() => ({}))
    setMsg(r.ok
      ? { ok: true, text: 'league run started — follow it on the Execute page' }
      : { ok: false, text: d?.detail || 'launch failed (another job running?)' })
  }

  const statusOf = (sig) => favs.find((f) => f.sig === sig)?.status
  const approved = favs.filter((f) => f.status === 'approved')

  // flatten the chosen class's stats onto the row so sorting and rendering
  // read the same fields whatever the tab
  const rows = useMemo(() => {
    if (!doc?.configs) return []
    let flat = doc.configs
    if (cls !== 'all') {
      flat = doc.configs
        .filter((r) => r.classes?.[cls])
        .map((r) => ({ ...r, ...r.classes[cls] }))
        .filter((r) => r.assets_logged > 0)
    }
    return sortRows(flat, sort.col, sort.dir)
  }, [doc, sort, cls])

  return (
    <div>
      <h1 className="page-title">Setup League</h1>
      <p className="page-sub">
        Every candidate setup (your ★ favourites + auto-picked consistent
        configs) backtested on ALL assets at your venue fees, then judged
        only on the last {doc?.holdout_days ?? 60} days of data — a period
        the setups were not selected on. Approve the survivors to build your
        monitored shortlist.
      </p>

      <div className="controls">
        <div className="tabs">
          {[['all', 'All'], ['stock', 'Stocks'], ['crypto', 'Crypto']].map(([id, label]) => (
            <button key={id} className={cls === id ? 'active' : ''}
              onClick={() => setCls(id)}>
              {label}
            </button>
          ))}
        </div>
        <button className="btn" onClick={launch}>Run the league now</button>
      </div>

      {err && <div className="card"><span className="neg">{err}</span></div>}
      {msg && (
        <div className="card">
          <span className={msg.ok ? 'pos' : 'neg'}>{msg.text}</span>
        </div>
      )}

      <div className="card">
        <h3>Approved shortlist ({approved.length})</h3>
        {approved.length === 0
          ? <p className="hint">nothing approved yet — run the league and
              approve the top holdout performers below</p>
          : (
            <div>
              {approved.map((f) => (
                <span key={f.sig} className="chip" style={{ marginBottom: 6 }}>
                  ✅ {f.label}
                  <button className="btn ghost" style={{ marginLeft: 6, padding: '0 6px' }}
                    onClick={() => setStatus({ sig: f.sig, label: f.label }, 'favourite')}>
                    demote
                  </button>
                </span>
              ))}
            </div>
          )}
      </div>

      {!doc?.available ? (
        <div className="card">
          <p className="hint">
            no league results yet — star a few setups on the Setups or
            Overview pages, then hit "Run the league now" (it backtests
            every candidate on every asset; expect ~15–40 minutes).
          </p>
        </div>
      ) : (
        <div className="card">
          <h3>Scorecard</h3>
          <p className="hint" style={{ marginTop: 0 }}>
            generated {doc.generated_at} · holdout = trades after{' '}
            {doc.cutoff?.slice(0, 10)} · eligible = ≥30 holdout trades on ≥5
            assets · rank is by holdout pooled PF
          </p>
          <div style={{ overflowX: 'auto' }}>
            <table className="grid">
              <thead>
                <tr>
                  <Th id="rank" sort={sort} setSort={setSort}>Rank</Th>
                  <th className="txt">Setup</th>
                  <th className="txt">Source</th>
                  <Th id="assets_logged" sort={sort} setSort={setSort}>Assets</Th>
                  <Th id="train_pf" sort={sort} setSort={setSort}>Train PF</Th>
                  <Th id="train_trades" sort={sort} setSort={setSort}>Train trades</Th>
                  <Th id="hold_pf" sort={sort} setSort={setSort}>Holdout PF</Th>
                  <Th id="hold_trades" sort={sort} setSort={setSort}>Holdout trades</Th>
                  <Th id="hold_pnl_usd" sort={sort} setSort={setSort}>Holdout P&L $</Th>
                  <Th id="hold_assets_pf_gt1" sort={sort} setSort={setSort}>Assets PF&gt;1</Th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const st = statusOf(r.sig)
                  return (
                    <tr key={r.sig}
                      style={!r.eligible ? { opacity: 0.5 } : undefined}>
                      <td>{r.rank ?? '—'}</td>
                      <td className="txt">{r.label}</td>
                      <td className="txt hint">{r.source}</td>
                      <td>{r.assets_logged}</td>
                      <td><Pf v={r.train_pf} /></td>
                      <td>{r.train_trades}</td>
                      <td><Pf v={r.hold_pf} /></td>
                      <td>{r.hold_trades}</td>
                      <td><Money v={r.hold_pnl_usd} /></td>
                      <td>{r.hold_assets_pf_gt1}/{r.hold_assets}</td>
                      <td>
                        {st === 'approved'
                          ? <button className="btn ghost"
                              onClick={() => setStatus(r, 'favourite')}>
                              ✅ approved</button>
                          : <button className="btn ghost"
                              disabled={!r.eligible}
                              title={r.eligible ? '' : 'not eligible (too few holdout trades/assets)'}
                              onClick={() => setStatus(r, 'approved')}>
                              approve</button>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="hint">
            trades/P&amp;L use ${num(doc.settings?.trade_size_stock_usd, 0)}
            /stock and ${num(doc.settings?.trade_size_crypto_usd, 0)}/crypto
            trades at {num(doc.settings?.fee_stock_pct, 3)}% /
            {num(doc.settings?.fee_crypto_pct, 2)}% per side. Approval keeps a
            setup in the shortlist the live scanner will monitor (next phase).
          </p>
        </div>
      )}
    </div>
  )
}
