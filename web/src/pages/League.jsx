import { useEffect, useMemo, useState } from 'react'
import { Info, Pf, Th, sortRows } from '../components/bits.jsx'
import { num } from '../data.js'

const COL_INFO = {
  rank: 'Leaderboard position by holdout pooled PF, among eligible setups '
    + 'only (enough holdout trades and assets). "—" = not eligible.',
  setup: 'The full strategy config: entry family, setup→exec timeframes, '
    + 'parameters, exit rule, and regime gate if any.',
  hold_pf: 'Pooled profit factor over the holdout (the last ~60 days — data '
    + 'the setup was NOT selected on). The main honesty metric: you want '
    + 'BOTH train and holdout ≥ 1.',
  hold_trades: 'Closed trades in the holdout window, pooled across all '
    + 'assets. Eligibility needs 30+.',
  hold_pnl: 'Total holdout P&L in USD at your per-class trade sizes, net of '
    + 'venue fees.',
  hold_sharpe: 'Mean ÷ σ of per-trade P&L in the holdout — risk-adjusted '
    + 'tiebreaker two setups with equal PF can differ on. Higher = smoother. '
    + 'Fills in on the next evaluation round for older rows.',
  breadth: 'Breadth: assets profitable in the holdout / assets with ≥3 '
    + 'holdout trades. Wide breadth = structural edge, narrow = luck or '
    + 'asset-specific.',
  post: 'PF counting ONLY trades on data newer than the day this setup was '
    + 'first tested — the one number endless searching cannot inflate. '
    + 'Accumulates as new market days arrive; "—" = no virgin data yet.',
  train_pf: 'Pooled profit factor over the training window (everything '
    + 'before the holdout cutoff). Setups are discovered on this data, so '
    + 'it flatters — never approve on train alone.',
  train_trades: 'Closed trades in the training window across all assets.',
  assets: 'How many assets the setup was backtested on (stocks-only '
    + 'universe).',
  first_seen: 'When this exact config was first tested — the anchor for the '
    + 'post-discovery column.',
  source: 'Where the candidate came from: your ★ favourites, auto-picked '
    + 'from the registry, a one-off grid, or the explorer (random draw / '
    + 'mutation of a leader).',
}

const Money = ({ v }) => v == null ? '—' : (
  <span className={v >= 0 ? 'pos' : 'neg'}>
    {v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)}
  </span>
)

// run-until-paused control for the League Explorer
function ExplorerCard() {
  const [st, setSt] = useState(null)
  const [note, setNote] = useState(null)

  async function refresh() {
    try {
      const r = await fetch('/api/explorer')
      if (r.ok) setSt(await r.json())
    } catch { /* offline */ }
  }
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [])

  async function act(action) {
    setNote(null)
    const r = await fetch('/api/explorer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
    const d = await r.json().catch(() => ({}))
    setNote(d?.note || (r.ok ? null : 'request failed'))
    refresh()
  }

  const running = st?.state === 'run'
  return (
    <div className="card">
      <h3>
        Explorer — new combinations until you pause{' '}
        {running
          ? <span className="badge running">exploring</span>
          : <span className="badge failed">paused</span>}
      </h3>
      <p className="hint" style={{ marginTop: 4 }}>
        Each round tests ~200 never-repeated setups (mutations of the current
        leaderboard + random draws across every family, trailing stops
        included) on every asset, then drops results into "To review".
        Rounds completed: <b>{st?.round ?? 0}</b>. It yields around the
        nightly update / weekly lab and resumes by itself; pausing takes
        effect when the current round finishes. Trust the{' '}
        <b>Post-disc. PF</b> column for anything the explorer found — it only
        counts data newer than the setup's first test, the one number endless
        searching can't fake.
      </p>
      {running
        ? <button className="btn" onClick={() => act('pause')}>
            Pause after this round</button>
        : <button className="btn" onClick={() => act('start')}>
            Explore until paused ▶</button>}
      {note && <p className="hint" style={{ marginBottom: 0 }}>{note}</p>}
    </div>
  )
}

// live progress for a running league/explorer job (parses "step assets N/M")
function LeagueProgress({ onDone }) {
  const [state, setState] = useState(null)

  useEffect(() => {
    let alive = true
    let wasRunning = false
    async function poll() {
      try {
        const r = await fetch('/api/jobs')
        if (!r.ok) return
        const lg = (await r.json()).find(
          (j) => ['setup_league', 'league_explorer'].includes(j.type)
            && j.status === 'running')
        if (!lg) {
          if (alive) setState(null)
          if (wasRunning) { wasRunning = false; onDone?.() }
          return
        }
        wasRunning = true
        const lr = await fetch(`/api/jobs/${lg.id}?tail=80`)
        if (!lr.ok || !alive) return
        const log = (await lr.json()).log_tail || ''
        let m, last = null
        const re = /\[league\] step assets (\d+)\/(\d+)/g
        while ((m = re.exec(log))) last = m
        const hdr = log.match(/\[league\] (\d+) configs x (\d+) assets/)
        let rm, round = null
        const rre = /\[round (\d+)\]/g
        while ((rm = rre.exec(log))) round = rm[1]
        const elapsed = (Date.now() - Date.parse(lg.started_at)) / 60000
        const frac = last
          ? Math.min(0.99, Number(last[1]) / Number(last[2]))
          : 0.02
        setState({
          frac,
          label: lg.type === 'league_explorer'
            ? `Explorer${round ? ` — round ${round}` : ''}`
            : 'Setup League',
          text: `${last ? `assets ${last[1]}/${last[2]}` : 'warming up'}`
            + (hdr ? ` · ${hdr[1]} configs × ${hdr[2]} assets` : '')
            + ` · elapsed ${elapsed.toFixed(1)} min`
            + (frac > 0.03
              ? ` · ~${Math.max(0, (elapsed / frac) * (1 - frac)).toFixed(1)} min left this round`
              : ''),
        })
      } catch { /* keep last */ }
    }
    poll()
    const t = setInterval(poll, 3000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (!state) return null
  return (
    <div className="card">
      <h3><span className="badge running">running</span> {state.label}</h3>
      <div className="pbar-track">
        <div className="pbar-fill" style={{ width: `${Math.round(state.frac * 100)}%` }} />
      </div>
      <p className="hint" style={{ marginBottom: 0 }}>
        {Math.round(state.frac * 100)}% · {state.text} · the scorecard below
        refreshes automatically when it finishes
      </p>
    </div>
  )
}

function ScoreTable({ title, hint, rows, sort, setSort, isApproved, onAction, emptyText }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      {hint && <p className="hint" style={{ marginTop: 0 }}>{hint}</p>}
      {rows.length === 0 ? <p className="hint">{emptyText}</p> : (
        <div style={{ overflowX: 'auto' }}>
          <table className="grid">
            <thead>
              <tr>
                <Th id="rank" sort={sort} setSort={setSort}
                  info={COL_INFO.rank}>Rank</Th>
                <th className="txt" title={COL_INFO.setup}>
                  Setup<Info text={COL_INFO.setup} /></th>
                <Th id="hold_pf" sort={sort} setSort={setSort}
                  info={COL_INFO.hold_pf}>Holdout PF</Th>
                <Th id="hold_trades" sort={sort} setSort={setSort}
                  info={COL_INFO.hold_trades}>Hold trades</Th>
                <Th id="hold_pnl_usd" sort={sort} setSort={setSort}
                  info={COL_INFO.hold_pnl}>Hold P&L $</Th>
                <Th id="hold_sharpe" sort={sort} setSort={setSort}
                  info={COL_INFO.hold_sharpe}>Sharpe/tr</Th>
                <Th id="hold_assets_pf_gt1" sort={sort} setSort={setSort}
                  info={COL_INFO.breadth}>Assets PF&gt;1</Th>
                <Th id="post_pf" sort={sort} setSort={setSort}
                  info={COL_INFO.post}>Post-disc. PF</Th>
                <Th id="train_pf" sort={sort} setSort={setSort}
                  info={COL_INFO.train_pf}>Train PF</Th>
                <Th id="train_trades" sort={sort} setSort={setSort}
                  info={COL_INFO.train_trades}>Train trades</Th>
                <Th id="assets_logged" sort={sort} setSort={setSort}
                  info={COL_INFO.assets}>Assets</Th>
                <Th id="first_seen" sort={sort} setSort={setSort} txt
                  info={COL_INFO.first_seen}>First seen</Th>
                <th className="txt" title={COL_INFO.source}>
                  Source<Info text={COL_INFO.source} /></th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 150).map((r) => (
                <tr key={r.sig}
                  style={!r.eligible && !isApproved ? { opacity: 0.5 } : undefined}>
                  <td>{r.rank ?? '—'}</td>
                  <td className="txt" style={{
                    minWidth: 260, maxWidth: 560, whiteSpace: 'normal',
                  }}>
                    {isApproved && '✅ '}{r.label}</td>
                  <td><Pf v={r.hold_pf} /></td>
                  <td>{r.hold_trades}</td>
                  <td><Money v={r.hold_pnl_usd} /></td>
                  <td>{r.hold_sharpe != null ? num(r.hold_sharpe, 2)
                    : <span className="hint" title="fills in on the next evaluation round">—</span>}</td>
                  <td>{r.hold_assets_pf_gt1}/{r.hold_assets}</td>
                  <td title={r.post_trades ? `${r.post_trades} trades since discovery` : 'no data newer than discovery yet'}>
                    <Pf v={r.post_pf} /></td>
                  <td><Pf v={r.train_pf} /></td>
                  <td>{r.train_trades}</td>
                  <td>{r.assets_logged}</td>
                  <td className="txt hint">{r.first_seen?.slice(0, 10) ?? '—'}</td>
                  <td className="txt hint">{r.source}</td>
                  <td>
                    {isApproved
                      ? <button className="btn ghost" onClick={() => onAction(r)}>
                          demote</button>
                      : <button className="btn ghost" disabled={!r.eligible}
                          title={r.eligible ? '' : 'not eligible (too few holdout trades/assets)'}
                          onClick={() => onAction(r)}>
                          approve</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > 150 && (
            <p className="hint">showing 150 of {rows.length} — sort to surface
              what you need</p>
          )}
        </div>
      )}
    </div>
  )
}

// Setup League: candidate setups validated across the whole universe with a
// holdout split. The holdout columns are the only ones that matter for
// approval — train numbers include the data the setups were discovered on.
export default function League() {
  const [doc, setDoc] = useState(null)
  const [favs, setFavs] = useState([])
  const [err, setErr] = useState(null)
  const [msg, setMsg] = useState(null)
  // default order = rank: eligible leaders first, unranked rows last
  const [sort, setSort] = useState({ col: 'rank', dir: 'asc' })
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
      ? { ok: true, text: status === 'approved' ? `approved: ${row.label}` : `moved back to review: ${row.label}` }
      : { ok: false, text: 'update failed' })
    load()
  }

  async function launch() {
    setMsg(null)
    const r = await fetch('/api/jobs/setup_league', { method: 'POST' })
    const d = await r.json().catch(() => ({}))
    setMsg(r.ok
      ? { ok: true, text: 'league run started — progress bar above' }
      : { ok: false, text: d?.detail || 'launch failed (another job running?)' })
  }

  async function syncRoster() {
    setMsg(null)
    const r = await fetch('/api/autotrade/sync_approved', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ per_setup: 10 }),
    })
    const d = await r.json().catch(() => ({}))
    setMsg(r.ok
      ? { ok: true, text: `scanner roster updated: ${d.note} — paper trading starts on the next scan cycle (kill switch: /pause)` }
      : { ok: false, text: d?.detail || 'sync failed' })
  }

  const approvedSigs = useMemo(
    () => new Set(favs.filter((f) => f.status === 'approved').map((f) => f.sig)),
    [favs])

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

  const [showArchived, setShowArchived] = useState(false)
  const approvedRows = rows.filter((r) => approvedSigs.has(r.sig))
  const allReview = rows.filter((r) => !approvedSigs.has(r.sig))
  // clear failures and never-traded configs rest in the archive
  const isArchived = (r) =>
    (r.hold_pf != null && r.hold_pf < 0.9) || !r.hold_trades
  const reviewRows = showArchived ? allReview : allReview.filter((r) => !isArchived(r))
  const archivedCount = allReview.filter(isArchived).length
  const approvedMissing = favs.filter(
    (f) => f.status === 'approved' && !rows.some((r) => r.sig === f.sig))

  return (
    <div>
      <h1 className="page-title">Setups</h1>
      <p className="page-sub">
        The league: every candidate setup (your ★ favourites + auto-picked
        configs + explorer discoveries) backtested on ALL assets at your
        venue fees, then judged only on the last {doc?.holdout_days ?? 60}
        days of data — a period the setups were not selected on. Approve the
        survivors to build your monitored shortlist; star new candidates
        from any asset review on the Overview.
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
        <button className="btn" onClick={syncRoster}
          title="each approved setup's top-10 league assets go onto the scanner roster (best setup kept when symbols collide) — the scanner then paper-trades them live">
          Send approved to scanner ▶
        </button>
      </div>

      <ExplorerCard />
      <LeagueProgress onDone={load} />

      {err && <div className="card"><span className="neg">{err}</span></div>}
      {msg && (
        <div className="card">
          <span className={msg.ok ? 'pos' : 'neg'}>{msg.text}</span>
        </div>
      )}

      {!doc?.available ? (
        <div className="card">
          <p className="hint">
            no league results yet — star a few setups on the Overview asset
            reviews, then hit "Run the league now" or start the Explorer.
          </p>
        </div>
      ) : (
        <>
          <ScoreTable
            title={`Approved (${approvedRows.length})`}
            hint="your live shortlist — these are the setups the scanner will monitor"
            rows={approvedRows} sort={sort} setSort={setSort}
            isApproved onAction={(r) => setStatus(r, 'favourite')}
            emptyText="nothing approved yet — approve the best holdout performers below" />

          {approvedMissing.length > 0 && (
            <div className="card">
              <p className="hint" style={{ margin: 0 }}>
                approved but missing from the latest league run (re-run to
                score them): {approvedMissing.map((f) => f.label).join(' · ')}
              </p>
            </div>
          )}

          <ScoreTable
            title={`To review (${reviewRows.length})`}
            hint={`generated ${doc.generated_at} · holdout = trades after ${doc.cutoff?.slice(0, 10)} · eligible = enough holdout trades/assets · rank = holdout pooled PF · Post-disc. PF counts only data newer than the setup's first test`}
            rows={reviewRows} sort={sort} setSort={setSort}
            isApproved={false} onAction={(r) => setStatus(r, 'approved')}
            emptyText="nothing to review — run the league" />

          {archivedCount > 0 && (
            <p className="hint">
              <button className="btn ghost" onClick={() => setShowArchived(!showArchived)}>
                {showArchived ? 'hide' : 'show'} {archivedCount} archived
                (holdout PF &lt; 0.9)
              </button>
            </p>
          )}

          <p className="hint">
            trades/P&amp;L use ${num(doc.settings?.trade_size_stock_usd, 0)}
            /stock and ${num(doc.settings?.trade_size_crypto_usd, 0)}/crypto
            trades at {num(doc.settings?.fee_stock_pct, 3)}% /
            {num(doc.settings?.fee_crypto_pct, 2)}% per side. Approval keeps a
            setup in the shortlist the live scanner will monitor (next phase).
          </p>
        </>
      )}
    </div>
  )
}
