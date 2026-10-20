import React, { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { assistApi } from '../api'
import LiveView from '../components/LiveView'

const STATE = {
  starting:    ['⏳', 'Opening the application page…', 'info'],
  agent:       ['🤖', 'The AI is filling the form', 'info'],
  asking:      ['❓', 'The AI has a question for you — answer in the chat', 'warn'],
  waiting_you: ['✋', 'Your turn — act in the window, then reply in the chat', 'warn'],
  applied:     ['✅', 'Applied — the site confirmed it', 'ok'],
  closed:      ['■', 'Stopped', 'muted'],
  error:       ['⚠️', 'Browser error — see the chat', 'bad'],
  none:        ['•', 'No AI fill is running', 'muted'],
}
const LIVE = ['starting', 'agent', 'asking', 'waiting_you']

// Full-page "Fill with AI": the big live browser on the left, the AI's progress and questions as a chat
// on the right — answer in text or attach a file, and the AI carries on by itself.
export default function LiveApply() {
  const [st, setSt]       = useState({ state: 'starting', chat: [] })
  const [text, setText]   = useState('')
  const [busy, setBusy]   = useState(false)
  const [err, setErr]     = useState('')
  const [closingIn, setClosingIn] = useState(null)   // seconds until this tab closes itself
  const wasLive = useRef(false)
  const navigate = useNavigate()
  const listRef = useRef(null)
  const fileRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const s = await assistApi.status()
        if (alive) setSt(s)
      } catch (e) { if (alive) setErr(e.message) }
    }
    poll()
    const t = setInterval(poll, 1200)
    return () => { alive = false; clearInterval(t) }
  }, [])

  // Session over (applied / stopped): close this tab by itself — errors stay open so you can read them
  useEffect(() => {
    if (LIVE.includes(st.state)) { wasLive.current = true; return }
    if (wasLive.current && ['applied', 'closed'].includes(st.state) && closingIn === null) {
      setClosingIn(st.state === 'applied' ? 5 : 3)
    }
  }, [st.state, closingIn])

  useEffect(() => {
    if (closingIn === null) return
    if (closingIn <= 0) {
      window.close()  // works for the tab the Applications page opened
      const t = setTimeout(() => navigate('/applications'), 300)  // if the browser kept it open
      return () => clearTimeout(t)
    }
    const t = setTimeout(() => setClosingIn(s => (s === null ? null : s - 1)), 1000)
    return () => clearTimeout(t)
  }, [closingIn, navigate])

  const count = st.chat?.length || 0
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' }) }, [count])
  useEffect(() => { if (st.state === 'asking') inputRef.current?.focus() }, [st.state])
  useEffect(() => { document.title = `${st.state === 'asking' ? '❓ ' : ''}Fill with AI — ${st.title || 'AI Job Researcher'}` },
    [st.state, st.title])

  async function act(fn) {
    setBusy(true); setErr('')
    try { await fn() } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  async function send(e) {
    e.preventDefault()
    const t = text.trim()
    if (!t) return
    await act(async () => { await assistApi.reply(t); setText('') })
  }

  async function attach(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (file) await act(() => assistApi.upload(file))
  }

  const [icon, label, tone] = STATE[st.state] || STATE.starting
  const live = LIVE.includes(st.state)
  return (
    <div className="live-page">
      <header className="live-head">
        <img src="/logo.svg" alt="" width="28" height="28" />
        <div className="live-title">
          <strong>Fill with AI</strong>
          <span>{st.title}</span>
        </div>
        <span className={`live-state live-${tone}`} role="status">{icon} {label}</span>
        <div className="live-actions">
          {live && st.app_id && (
            <button className="btn btn-success btn-sm" disabled={busy} title="You submitted it yourself"
              onClick={() => act(() => assistApi.markApplied(st.app_id))}>✔ Mark as applied</button>
          )}
          {live && <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => act(assistApi.close)}>■ Stop</button>}
          <Link to="/applications" className="btn btn-secondary btn-sm">← Applications</Link>
        </div>
      </header>

      <main className="live-main">
        {closingIn !== null && (
          <div className="live-closing" role="status">
            {st.state === 'applied' ? '✅ Applied — ' : 'Session finished — '}closing this tab in {Math.max(closingIn, 0)} s…
            <button className="btn btn-secondary btn-sm" onClick={() => setClosingIn(null)}>Keep open</button>
          </div>
        )}
        <section className="live-browser" aria-label="Live browser">
          {live ? <LiveView label={st.title || 'Application'} url={st.url} />
                : <div className="live-empty">{st.state === 'applied' ? '✅ Applied. You can close this tab.'
                                                  : 'The browser is closed.'}</div>}
        </section>

        <aside className="live-chat" aria-label="Chat with the AI">
          <ol className="chat-list" ref={listRef}>
            {(st.chat || []).map((m, i) => (
              <li key={i} className={`bubble bubble-${m.from} bubble-${m.kind || 'info'}`}>
                <span className="bubble-text">{m.text}</span>
                <span className="bubble-time">{m.time}</span>
              </li>
            ))}
            {st.state === 'agent' && <li className="bubble bubble-ai bubble-typing">AI is working…</li>}
          </ol>
          {err && <div className="alert alert-error" style={{ margin: '0 0.75rem' }}>⚠️ {err}</div>}
          {live && (
            <form className="chat-compose" onSubmit={send}>
              {st.question && <div className="chat-question">❓ {st.question}</div>}
              <textarea ref={inputRef} rows={2} className="form-control" aria-label="Your reply"
                placeholder={st.state === 'asking' ? 'Type your answer…' : 'Tell the AI something, or reply “done” after acting in the window'}
                value={text} onChange={e => setText(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) send(e) }} />
              <div className="chat-buttons">
                <input ref={fileRef} type="file" hidden onChange={attach}
                  accept=".pdf,.doc,.docx,.txt,.rtf,.odt,.png,.jpg,.jpeg" />
                <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
                  onClick={() => fileRef.current?.click()} title="Attach a file for the AI to upload">📎 Attach</button>
                {st.state === 'waiting_you' && (
                  <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
                    onClick={() => act(assistApi.resume)}>🤖 Continue with AI</button>
                )}
                <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !text.trim()}>Send</button>
              </div>
            </form>
          )}
        </aside>
      </main>
    </div>
  )
}
