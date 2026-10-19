import React, { useCallback, useEffect, useState } from 'react'
import { getStatsToday, connectJobSite } from '../api'

const NAMES = { linkedin: 'LinkedIn', naukri: 'Naukri' }

function Chip({ tone, title, children }) {
  return <span className={`status-chip status-${tone}`} title={title}>{children}</span>
}

const hours = m => (m >= 60 ? `${Math.round(m / 60)} h` : `${m} min`)

// Always-visible bar: platform limits / restrictions and which AI providers are out of quota
export default function StatusHeader() {
  const [r, setR]     = useState(null)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => getStatsToday().then(s => setR(s.readiness || {})).catch(() => {}), [])
  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [load])

  async function connect(site) {
    try {
      await connectJobSite(site)
      setMsg(`A ${NAMES[site]} window is opening on this computer — log in there; it closes by itself.`)
    } catch (err) { setMsg(err.message) }
  }

  if (!r) return null
  const paused = r.paused || {}
  const caps = r.platform_daily_cap || {}
  const used = r.platform_today || {}
  const queued = r.queued_by_route || {}
  const ai = r.ai_quota || []
  const aiOut = ai.filter(p => p.out)

  return (
    <div className="status-header" role="status" aria-live="polite">
      {Object.keys(caps).map(p => {
        const reason = paused[p]
        const full = (used[p] || 0) >= caps[p]
        if (reason) {
          const restricted = /restricted/i.test(reason)
          return (
            <Chip key={p} tone="bad" title={reason}>
              {restricted ? '⛔' : '⏸'} {NAMES[p]}: {restricted ? 'account restricted — stopped' : reason.replace(/^\w+ /, '')}
              {queued[p] ? ` (${queued[p]} waiting)` : ''}
              {/logged out|security check|restricted/i.test(reason) &&
                <button className="status-chip-btn" onClick={() => connect(p)}>Connect</button>}
            </Chip>
          )
        }
        return (
          <Chip key={p} tone={full ? 'warn' : 'ok'}
            title={full ? `Daily limit reached — ${NAMES[p]} resumes tomorrow` : 'Applications sent today / daily limit'}>
            {full ? '🛑' : '✅'} {NAMES[p]} {used[p] || 0}/{caps[p]} today{full ? ' — limit reached, resumes tomorrow' : ''}
          </Chip>
        )
      })}
      {ai.length === 0
        ? <Chip tone="bad" title="Add a key in Settings">🤖 No cloud AI key — only the slow local model</Chip>
        : aiOut.length === ai.length
          ? <Chip tone="bad" title="Every cloud provider is out of quota; the local model is used (slow)">
              🤖 All AI out of quota ({aiOut.map(p => `${p.name} ~${hours(Math.min(...p.blocked.map(b => b.minutes)))}`).join(', ')}) — using the slow local model
            </Chip>
          : ai.map((p, i) => (
              <Chip key={`${p.name}-${i}`} tone={p.out ? 'bad' : p.blocked.length ? 'warn' : 'ok'}
                title={[p.label && `Key: ${p.label}`, ...p.blocked.map(b => `${b.model}: ${b.why}, back in ~${hours(b.minutes)}`)]
                  .filter(Boolean).join('\n') || 'Available'}>
                🤖 {p.name}: {p.out
                  ? `out of quota (back in ~${hours(Math.min(...p.blocked.map(b => b.minutes)))})`
                  : p.blocked.length ? `${p.models - p.blocked.length}/${p.models} models available` : 'OK'}
              </Chip>
            ))}
      {msg && <span className="status-msg">{msg}</span>}
    </div>
  )
}
