import { useEffect, useState } from 'react'
import { Pf } from '../components/bits.jsx'
import { num } from '../data.js'

function SettingsCard() {
  const [s, setS] = useState(null)
  const [saved, setSaved] = useState(null)

  useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch('/api/settings')
        if (r.ok) setS(await r.json())
      } catch { /* offline */ }
    })()
  }, [])

  async function save() {
    setSaved(null)
    const r = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s),
    })
    const d = await r.json()
    setSaved(r.ok ? { ok: true, text: 'saved — applies to new backtests, curves and paper trades' }
      : { ok: false, text: d?.detail || 'save failed' })
  }

  if (!s) return null
  const field = (key, label, step) => (
    <label>{label}
      <input type="number" step={step} value={s[key]}
        onChange={(e) => setS({ ...s, [key]: Number(e.target.value) })} />
    </label>
  )
  return (
    <div className="card">
      <h3>Backtest &amp; trading settings</h3>
      <div className="filters">
        {field('fee_crypto_pct', 'crypto fee %/side', 0.01)}
        {field('fee_stock_pct', 'stock fee %/side', 0.005)}
        {field('capital_usd', 'capital (USD)', 1000)}
        {field('trade_size_usd', 'size per trade (USD, 0 = all-in)', 100)}
        <button className="btn" onClick={save}>Save</button>
      </div>
      <p className="hint">
        Backtests run two fee scenarios: your fee and a 2.5× stressed fee (the
        two PF columns across the portal). Nightly reprocessing, sweeps, curve
        and trigger views all pick these up; already-logged registry rows keep
        the fee they were run with.
      </p>
      {saved && <p className={saved.ok ? 'pos' : 'neg'}>{saved.text}</p>}
    </div>
  )
}

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

      <SettingsCard />

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
