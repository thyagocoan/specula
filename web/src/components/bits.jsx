import { num } from '../data.js'

export function Ret({ v }) {
  if (v == null) return <span>—</span>
  const cls = v > 0 ? 'pos' : v < 0 ? 'neg' : ''
  const arrow = v > 0 ? '▲' : v < 0 ? '▼' : ''
  return <span className={cls}>{arrow} {num(v, 1)}%</span>
}

export function Pf({ v }) {
  if (v == null) return <span>—</span>
  return <span className={v >= 1 ? 'pos' : 'neg'}>{num(v)}</span>
}

export function useSort(defaultCol, defaultDir = 'desc') {
  return { col: defaultCol, dir: defaultDir }
}

export function sortRows(rows, col, dir) {
  const m = dir === 'desc' ? -1 : 1
  return [...rows].sort((a, b) => {
    const av = a[col]; const bv = b[col]
    if (av == null && bv == null) return 0
    if (av == null) return 1 // nulls sort last in BOTH directions
    if (bv == null) return -1
    const c = typeof av === 'string' ? av.localeCompare(bv) : av - bv
    return m * c
  })
}

export function Th({ id, sort, setSort, children, txt }) {
  const active = sort.col === id
  return (
    <th className={txt ? 'txt' : ''}
      onClick={() => setSort({ col: id, dir: active && sort.dir === 'desc' ? 'asc' : 'desc' })}>
      {children}{active ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  )
}
