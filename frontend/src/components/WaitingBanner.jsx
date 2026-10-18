import React, { useEffect, useState } from 'react'
import { getStatsToday, connectJobSite } from '../api'

const NAMES = { linkedin: 'LinkedIn', naukri: 'Naukri' }

// Says loudly when queued jobs can't go out because their site is signed out, with a one-click fix
export default function WaitingBanner() {
  const [stuck, setStuck] = useState([])
  const [msg, setMsg]     = useState('')

  useEffect(() => {
    getStatsToday().then(s => {
      const r = s.readiness || {}
      const queued = r.queued_by_route || {}
      setStuck(Object.entries(r.paused || {})
        .filter(([site]) => queued[site])
        .map(([site, reason]) => ({ site, reason, count: queued[site] })))
    }).catch(() => {})
  }, [])

  async function connect(site) {
    try {
      await connectJobSite(site)
      setMsg(`A ${NAMES[site] || site} window is opening on this computer — log in there. It closes by itself ` +
             'once you are in, and the waiting jobs start going out (one every 8–20 minutes).')
    } catch (err) { setMsg(err.message) }
  }

  if (!stuck.length) return null
  return (
    <div className="alert alert-error" role="alert" style={{ display: 'grid', gap: '0.4rem' }}>
      {stuck.map(({ site, reason, count }) => (
        <div key={site} style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <span>⚠️ <strong>{count} job{count > 1 ? 's are' : ' is'} waiting for {NAMES[site] || site}</strong> — {reason}
            {' '}Nothing can be sent there until you sign in.</span>
          <button className="btn btn-primary btn-sm" onClick={() => connect(site)}>🔗 Connect {NAMES[site] || site} now</button>
        </div>
      ))}
      {msg && <div style={{ fontSize: '0.85rem' }}>{msg}</div>}
    </div>
  )
}
