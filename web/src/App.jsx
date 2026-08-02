import { useEffect, useState } from 'react'
import { loadRuns } from './data.js'
import Overview from './pages/Overview.jsx'
import Assets from './pages/Assets.jsx'
import Setups from './pages/Setups.jsx'
import Runs from './pages/Runs.jsx'
import WalkForward from './pages/WalkForward.jsx'
import Execute from './pages/Execute.jsx'

const PAGES = [
  ['overview', 'Overview'],
  ['assets', 'Assets'],
  ['setups', 'Setups'],
  ['runs', 'Runs'],
  ['walkforward', 'Walk-forward'],
  ['execute', 'Execute'],
]

export default function App() {
  const [page, setPage] = useState('overview')
  const [data, setData] = useState(null)
  const [apiUp, setApiUp] = useState(false)
  const [error, setError] = useState(null)

  async function load() {
    try {
      const r = await fetch('/api/runs')
      if (r.ok) {
        setData(await r.json())
        setApiUp(true)
        return
      }
      throw new Error()
    } catch {
      try {
        setData(await loadRuns())
        setApiUp(false)
      } catch (e) {
        setError(String(e.message || e))
      }
    }
  }

  useEffect(() => {
    load()
  }, [])

  // refresh registry data when returning from the Execute page
  useEffect(() => {
    if (page !== 'execute') load()
  }, [page])

  if (error) return <div className="err">{error}</div>
  if (!data) return <div className="err" style={{ color: 'var(--muted)' }}>loading registry…</div>

  const runs = data.runs

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          Specula
          <small>backtest results explorer</small>
        </div>
        {PAGES.map(([id, label]) => (
          <button key={id} className={`nav-item ${page === id ? 'active' : ''}`}
            onClick={() => setPage(id)}>
            <span>{label}</span>
            {id === 'runs' && <span className="count">{runs.length.toLocaleString()}</span>}
          </button>
        ))}
        <div className="sidebar-foot">
          {apiUp ? 'live API' : 'static snapshot'} · {data.count?.toLocaleString()} runs
        </div>
      </aside>
      <main className="main">
        {page === 'overview' && <Overview runs={runs} generatedAt={data.generated_at} />}
        {page === 'assets' && <Assets runs={runs} />}
        {page === 'setups' && <Setups runs={runs} />}
        {page === 'runs' && <Runs runs={runs} />}
        {page === 'walkforward' && <WalkForward />}
        {page === 'execute' && <Execute apiUp={apiUp} />}
      </main>
    </div>
  )
}
