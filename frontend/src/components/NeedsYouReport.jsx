import React, { useCallback, useEffect, useState } from 'react'
import { getNeedsYouReport, retryReason } from '../api'

// "Why jobs need you": stopped applications grouped by cause, what happens next, and a Retry for the
// causes the AI can now handle itself.
export default function NeedsYouReport({ onRetried }) {
  const [groups, setGroups] = useState([])
  const [busy, setBusy] = useState('')

  const load = useCallback(() => { getNeedsYouReport().then(setGroups).catch(() => setGroups([])) }, [])
  useEffect(() => { load() }, [load])

  async function retry(g) {
    setBusy(g.key)
    try {
      const res = await retryReason(g.key)
      alert(`${res.requeued} application(s) are back in the queue.`)
      load(); onRetried?.()
    } catch (err) { alert(`Couldn't retry: ${err.message}`) } finally { setBusy('') }
  }

  if (!groups.length) return null
  const total = groups.reduce((n, g) => n + g.count, 0)
  const aiCan = groups.filter(g => g.ai_can).reduce((n, g) => n + g.count, 0)
  return (
    <details className="card needs-report">
      <summary>
        📋 <strong>Why {total} application{total === 1 ? '' : 's'} stopped</strong> — {aiCan} the AI can retry, {total - aiCan} need you
      </summary>
      <ul>
        {groups.map(g => (
          <li key={g.key} className={g.ai_can ? 'ai-can' : 'needs-you'}>
            <div className="needs-head">
              <strong>{g.title}</strong> <span className="badge">{g.count}</span>
              {g.ai_can && (
                <button className="btn btn-primary btn-sm" disabled={busy === g.key} onClick={() => retry(g)}>
                  {busy === g.key ? 'Queuing…' : '🔁 Retry these'}
                </button>
              )}
            </div>
            <div>{g.why} <em>{g.next}</em></div>
            <small>{g.jobs.slice(0, 4).map(j => `${j.title} at ${j.company}`).join(' · ')}
              {g.jobs.length > 4 ? ` · +${g.jobs.length - 4} more` : ''}</small>
          </li>
        ))}
      </ul>
    </details>
  )
}
