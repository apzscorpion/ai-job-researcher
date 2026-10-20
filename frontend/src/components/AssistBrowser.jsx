import React, { useEffect, useState } from 'react'
import { assistApi } from '../api'
import LiveView from './LiveView'
import Modal from './Modal'

const STATE = {
  starting:    ['⏳', 'Opening the application page…'],
  agent:       ['🤖', 'The AI is reading the page and filling the form — watch it work.'],
  waiting_you: ['✋', "Your turn — finish what the AI couldn't (captcha, login, an answer), then press Continue with AI."],
  applied:     ['✅', 'Applied! The site confirmed the application.'],
  closed:      ['■', 'The window is closed.'],
  error:       ['⚠️', 'Something went wrong with the browser — see the log below.'],
}

// The AI fills an application while you watch live; step in any time, then let it continue
export default function AssistBrowser({ title, appId, onClose }) {
  const [st, setSt]     = useState({ state: 'starting', log: [] })
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState('')

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const s = await assistApi.status()
        if (alive) setSt(s)
      } catch { /* keep polling */ }
    }
    poll()
    const t = setInterval(poll, 1500)
    return () => { alive = false; clearInterval(t) }
  }, [])

  async function act(fn) {
    setBusy(true); setErr('')
    try { await fn() } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  async function close() {
    await assistApi.close().catch(() => {})
    onClose(st.state === 'applied')
  }

  const [icon, text] = STATE[st.state] || STATE.starting
  const live = ['starting', 'agent', 'waiting_you'].includes(st.state)
  return (
    <Modal title={`Fill with AI — ${title}`} onClose={close}>
      <div className={`assist-state assist-${st.state}`} role="status">{icon} {text}</div>
      {err && <div className="alert alert-error">⚠️ {err}</div>}
      {live && <LiveView label={title} url={st.url} />}
      <ol className="assist-log" aria-label="What the AI did">
        {(st.log || []).slice(-8).map((l, i) => <li key={i}><span>{l.time}</span>{l.text}</li>)}
      </ol>
      <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: '0.5rem' }}>
        {st.state === 'waiting_you' && (
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act(assistApi.resume)}>
            🤖 Continue with AI
          </button>
        )}
        {st.state !== 'applied' && (
          <button className="btn btn-success btn-sm" disabled={busy} title="You submitted it yourself"
            onClick={() => act(async () => { await assistApi.markApplied(appId); onClose(true) })}>
            ✔ Mark as applied
          </button>
        )}
        <button className="btn btn-secondary btn-sm" onClick={close}>{st.state === 'applied' ? 'Done' : 'Close'}</button>
      </div>
    </Modal>
  )
}
