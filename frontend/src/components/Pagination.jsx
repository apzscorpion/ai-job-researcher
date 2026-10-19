import React, { useEffect, useState } from 'react'

// First / Prev / nearby page numbers / Next / Last, plus a box to jump to any page
export default function Pagination({ page, totalPages, onChange }) {
  const [entry, setEntry] = useState(String(page))
  useEffect(() => setEntry(String(page)), [page])

  const go = n => onChange(Math.min(totalPages, Math.max(1, n)))
  const from = Math.max(1, Math.min(page - 2, totalPages - 4))
  const nearby = Array.from({ length: Math.min(5, totalPages) }, (_, i) => from + i)

  function jump(e) {
    e.preventDefault()
    const n = parseInt(entry, 10)
    if (Number.isFinite(n)) go(n)
    else setEntry(String(page))
  }

  return (
    <nav className="pagination" aria-label="Pages">
      <button className="btn btn-secondary btn-sm" onClick={() => go(1)} disabled={page === 1} aria-label="First page">«</button>
      <button className="btn btn-secondary btn-sm" onClick={() => go(page - 1)} disabled={page === 1}>← Prev</button>
      {nearby.map(n => (
        <button key={n} className={`btn btn-sm ${n === page ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => go(n)} aria-current={n === page ? 'page' : undefined}>{n}</button>
      ))}
      <button className="btn btn-secondary btn-sm" onClick={() => go(page + 1)} disabled={page >= totalPages}>Next →</button>
      <button className="btn btn-secondary btn-sm" onClick={() => go(totalPages)} disabled={page >= totalPages} aria-label="Last page">»</button>
      <form onSubmit={jump} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
        <label className="pagination-info" htmlFor="page-jump">Page</label>
        <input id="page-jump" type="number" min={1} max={totalPages} value={entry}
          onChange={e => setEntry(e.target.value)} onBlur={jump}
          className="form-control" style={{ width: 70, padding: '0.25rem 0.4rem' }} />
        <span className="pagination-info">of {totalPages}</span>
        <button type="submit" className="btn btn-secondary btn-sm">Go</button>
      </form>
    </nav>
  )
}
