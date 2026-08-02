import { useEffect, useState } from 'react'

const dur = (a, b) => {
  if (!a) return '—'
  const ms = (b ? Date.parse(b) : Date.now()) - Date.parse(a)
  const s = Math.round(ms / 1000)
  return s < 90 ? `${s}s` : `${Math.round(s / 60)}m`
}

// rough per-step minute estimates for the overnight lab progress bar
const LAB_STEPS = [
  ['equity bronze/silver', 25], ['quality report', 10], ['MA megasweep', 45],
  ['lab coarse', 60], ['scoring', 3], ['lab refine', 45],
  ['walk-forward (candidates)', 40], ['equity curves', 15], ['web export', 3],
]

function ProgressCard({ job }) {
  const [log, setLog] = useState('')

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const r = await fetch(`/api/jobs/${job.id}?tail=500`)
        if (alive && r.ok) setLog((await r.json()).log_tail || '')
      } catch { /* keep last */ }
    }
    poll()
    const t = setInterval(poll, 10000)
    return () => { alive = false; clearInterval(t) }
  }, [job.id])

  const done = []
  let doneMinutes = 0
  let current = null
  for (const line of log.split('\n')) {
    if (line.startsWith('=====')) {
      current = line.replace(/=+/g, '').trim().replace(/ \(cap.*$/, '')
    }
    const m = line.match(/^\[(ok|FAIL|timeout)\] (.+?) \((\d+(?:\.\d+)?) min/)
    if (m) { done.push(m[2].trim()); doneMinutes += parseFloat(m[3]) }
  }
  if (done.includes(current)) current = null

  const est = Object.fromEntries(LAB_STEPS)
  const totalEst = LAB_STEPS.reduce((s, [, e]) => s + e, 0)
  const elapsedMin = (Date.now() - Date.parse(job.started_at)) / 60000
  const isLab = job.type === 'overnight_lab'
  let frac = null
  if (isLab) {
    const completedEst = done.reduce((s, d) => s + (est[d] ?? 5), 0)
    const curEst = current ? (est[current] ?? 10) : 0
    const curElapsed = Math.min(Math.max(0, elapsedMin - doneMinutes), 0.95 * curEst)
    frac = Math.min(0.99, (completedEst + curElapsed) / totalEst)
  }

  return (
    <div className="card">
      <h3><span className="badge running">running</span> {job.label}</h3>
      {frac != null ? (
        <>
          <div className="pbar-track">
            <div className="pbar-fill" style={{ width: `${(frac * 100).toFixed(0)}%` }} />
          </div>
          <p className="hint" style={{ marginBottom: 0 }}>
            {(frac * 100).toFixed(0)}% (rough estimate) · step {done.length + (current ? 1 : 0)}/{LAB_STEPS.length}:
            {' '}<b>{current || 'finishing'}</b> · elapsed {(elapsedMin / 60).toFixed(1)}h ·
            ~{(totalEst * (1 - frac) / 60).toFixed(1)}h remaining
          </p>
        </>
      ) : (
        <p className="hint" style={{ marginBottom: 0 }}>
          running for {Math.round(elapsedMin)} min (no step estimates for this job type)
        </p>
      )}
    </div>
  )
}

export default function Execute({ apiUp }) {
  const [jobs, setJobs] = useState([])
  const [types, setTypes] = useState([
    { id: 'sweep_mtf', label: 'MTF sweep (BTCUSDT)', desc: '912 configs over 19 timeframe pairs; appends to the registry.' },
    { id: 'sweep_equities', label: 'MTF sweep (10 equities)', desc: '9,120 configs — session-aligned bars, EOD flat, spread-based costs.' },
    { id: 'walkforward', label: 'Walk-forward validation (BTCUSDT)', desc: 'Rolling 120d train / 30d test over the full grid; the out-of-sample verdict.' },
    { id: 'rsi_filter', label: 'RSI filter analysis (FFFD)', desc: 'Multi-TF RSI at each entry: bucket diagnosis + hypothesis filters vs baseline.' },
    { id: 'export_web', label: 'Re-export web data', desc: 'Refresh runs.json and sync report HTMLs into the app.' },
    { id: 'daily_update', label: 'Daily data update', desc: 'Pull latest bars for every symbol in the lake, rebuild, re-run walk-forward, refresh portal. Also runs nightly via Task Scheduler.' },
    { id: 'overnight_lab', label: 'Overnight strategy lab', desc: 'Full discovery pipeline: MA megasweep → ORB/VWAP/RSI coarse → scoring → 1m refine → walk-forward on candidates (~6h).' },
  ])
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)
  const anyRunning = jobs.some((j) => j.status === 'running')

  async function refresh() {
    try {
      const r = await fetch('/api/jobs')
      if (!r.ok) throw new Error()
      setJobs(await r.json())
      setError(null)
    } catch {
      setError('API offline — start it from the repo root: uv run uvicorn specula.server:app --port 8756')
    }
  }

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 2500)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (!selected) return
    const load = async () => {
      const r = await fetch(`/api/jobs/${selected.id}?tail=200`)
      if (r.ok) setSelected(await r.json())
    }
    load()
    const t = setInterval(load, 2500)
    return () => clearInterval(t)
  }, [selected?.id, selected?.status === 'running'])

  async function start(type) {
    const r = await fetch(`/api/jobs/${type}`, { method: 'POST' })
    if (r.status === 409) alert('Another job is already running — one at a time.')
    refresh()
  }

  return (
    <div>
      <h1 className="page-title">Execute</h1>
      <p className="page-sub">Launch and monitor sweeps and validation from here — results land in the registry automatically.</p>
      {error && <div className="card"><span className="neg">{error}</span></div>}

      {jobs.filter((j) => j.status === 'running').map((j) => (
        <ProgressCard key={j.id} job={j} />
      ))}

      <div className="tiles">
        {types.map((t) => (
          <div className="tile" key={t.id}>
            <div className="k">{t.label}</div>
            <div className="d" style={{ marginBottom: 10 }}>{t.desc}</div>
            <button className="btn" disabled={!!error || anyRunning} onClick={() => start(t.id)}>
              {anyRunning ? 'busy…' : 'Run'}
            </button>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Job history (this API session)</h3>
        {jobs.length === 0 ? (
          <p className="hint">nothing launched yet</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th className="txt">Job</th><th className="txt">Type</th>
                <th className="txt">Status</th><th>Started (UTC)</th><th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} className="selectable" onClick={() => setSelected(j)}>
                  <td className="txt" style={{ fontFamily: 'Consolas, monospace', fontSize: 12 }}>{j.id}</td>
                  <td className="txt">{j.label}</td>
                  <td className="txt">
                    <span className={`badge ${j.status === 'running' ? 'running' : j.status === 'done' ? 'done' : 'failed'}`}>
                      {j.status}
                    </span>
                  </td>
                  <td>{j.started_at?.replace('T', ' ').replace('+00:00', '')}</td>
                  <td>{dur(j.started_at, j.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selected && (
        <div className="card">
          <h3>
            Log — {selected.label} ({selected.id})
            <button className="btn ghost" style={{ float: 'right' }}
              onClick={() => setSelected(null)}>close</button>
          </h3>
          <div className="log">{selected.log_tail || 'no output yet…'}</div>
        </div>
      )}
    </div>
  )
}
