import { useEffect, useState } from 'react'
import { Pf } from '../components/bits.jsx'
import { num } from '../data.js'

// Which assets trade automatically (paper mode), each on its validated
// best strategy. Enabling requires a positive out-of-sample verdict.
export default function Autotrade() {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState(null)
  const [symbol, setSymbol] = useState('')
  const [size, setSize] = useState(1000)
  const [msg, setMsg] = useState(null)

  async function load() {
    try {
      const r = await fetch('/api/autotrade')
      if (!r.ok) throw new Error(r.status === 404
        ? 'endpoint not available yet — arrives with the server cutover'
        : `API error ${r.status}`)
      setRows(await r.json())
      setErr(null)
    } catch (e) {
      setErr(String(e.message || e))
    }
  }

  useEffect(() => { load() }, [])

  async function save(sym, enabled, sizeUsd, force = false) {
    setMsg(null)
    const r = await fetch('/api/autotrade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sym, enabled, size_usd: sizeUsd, force }),
    })
    if (!r.ok) {
      const detail = (await r.json())?.detail || `error ${r.status}`
      setMsg({ ok: false, text: detail })
    } else {
      setMsg({ ok: true, text: `${sym} ${enabled ? 'enabled' : 'disabled'}` })
    }
    load()
  }

  return (
    <div>
      <h1 className="page-title">Autotrade (paper)</h1>
      <p className="page-sub">
        Enabled assets are scanned live and traded in paper mode on their
        walk-forward-validated strategy. Nightly reprocessing is scoped to
        this roster. Kill switch: /pause on Telegram.
      </p>

      {err && <div className="card"><span className="hint">{err}</span></div>}
      {msg && (
        <div className="card">
          <span className={msg.ok ? 'pos' : 'neg'}>{msg.text}</span>
        </div>
      )}

      <div className="card">
        <h3>Add asset</h3>
        <div className="filters">
          <label>symbol
            <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder="e.g. LLY" style={{ minWidth: 110 }} />
          </label>
          <label>size (USD per trade)
            <input type="number" min="10" value={size}
              onChange={(e) => setSize(Number(e.target.value) || 1000)} />
          </label>
          <button className="btn" disabled={!symbol}
            onClick={() => save(symbol, true, size)}>Enable</button>
        </div>
        <p className="hint">
          Enabling requires a positive out-of-sample profit factor for the
          symbol — the button will refuse assets that failed walk-forward.
        </p>
      </div>

      <div className="card">
        <h3>Roster</h3>
        {!rows || rows.length === 0 ? (
          <p className="hint">no assets enabled yet</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th className="txt">Symbol</th><th className="txt">State</th>
                <th>OOS PF</th><th>Size/trade</th><th className="txt">Added</th><th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol}>
                  <td className="txt"><b>{r.symbol}</b></td>
                  <td className="txt">{r.enabled
                    ? <span className="badge done">ON</span>
                    : <span className="badge failed">off</span>}</td>
                  <td><Pf v={r.oos_pf} /></td>
                  <td>${num(r.size_usd, 0)}</td>
                  <td className="txt hint">{r.added_at?.slice(0, 10)}</td>
                  <td>
                    <button className="btn ghost"
                      onClick={() => save(r.symbol, !r.enabled, r.size_usd)}>
                      {r.enabled ? 'disable' : 'enable'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
