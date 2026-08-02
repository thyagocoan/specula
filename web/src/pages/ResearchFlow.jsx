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
    desc: 'Multi-timeframe RSI (daily → 15m) snapshotted at each entry, look-ahead safe. Accept a filter only if the effect is monotone across buckets and economically sensible — never a cherry-picked magic bucket. Lesson so far: the hypothesis inverted — FFFD trades entered at higher-TF RSI extremes are the BEST ones; the 4h outside-40/60 filter doubled out-of-sample per-trade quality (PF 2.6 → 4.1) at half the trade count.',
  },
  {
    n: 5,
    title: 'Walk-forward validation',
    tags: [],
    desc: 'Rolling 120d train / 30d test; the winner is picked on training data only and judged on unseen data. This is the only number to trust — in-sample results are always inflated. Verdict so far: BTC FFFD survives at futures fees (OOS PF 1.42); of 10 stocks, only GOOGL, LLY, AVGO and TSLA survived (6 in-sample stars failed). See the Walk-forward page.',
  },
  {
    n: 6,
    title: 'Ready for paper trading',
    tags: [],
    desc: 'Only walk-forward survivors qualify, and only under the economics they survived at (futures/maker-level costs for crypto, ~1bp spreads for equities). Nothing here yet has earned real capital — the funnel exists to make sure whatever does, earned it honestly.',
  },
]

export default function ResearchFlow({ runs }) {
  return (
    <div>
      <h1 className="page-title">Research flow</h1>
      <p className="page-sub">How a setup earns trust here — each stage narrows the field before the next spends compute or credibility on it.</p>
      <div className="card">
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
    </div>
  )
}
