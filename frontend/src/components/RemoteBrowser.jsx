import React, { useEffect, useRef, useState } from 'react'
import { remoteSignIn } from '../api'
import Modal from './Modal'

const KEYS = ['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']

// Live view of a sign-in page running on the host: click on it and type to log in to your own account
export default function RemoteBrowser({ name, onClose }) {
  const [src, setSrc]       = useState('')
  const [state, setState]   = useState('starting')
  const [url, setUrl]       = useState('')
  const [error, setError]   = useState('')
  const imgRef = useRef(null)
  const boxRef = useRef(null)

  useEffect(() => {
    let alive = true
    let last = ''
    async function frame() {
      try {
        const blob = await remoteSignIn.frame()
        if (!alive) return
        const next = URL.createObjectURL(blob)
        setSrc(next)
        if (last) URL.revokeObjectURL(last)
        last = next
      } catch { /* window still starting or closed */ }
    }
    async function status() {
      try {
        const s = await remoteSignIn.status()
        if (!alive) return
        setState(s.state); setUrl(s.url || '')
        if (s.state === 'signed_in') onClose(true)
      } catch (err) { setError(err.message) }
    }
    const f = setInterval(frame, 800)
    const s = setInterval(status, 2000)
    frame(); status()
    boxRef.current?.focus()
    return () => { alive = false; clearInterval(f); clearInterval(s); if (last) URL.revokeObjectURL(last) }
  }, [onClose])

  const send = body => remoteSignIn.input(body).catch(err => setError(err.message))

  function click(e) {
    const img = imgRef.current
    const r = img.getBoundingClientRect()
    send({ kind: 'click', x: (e.clientX - r.left) * img.naturalWidth / r.width,
           y: (e.clientY - r.top) * img.naturalHeight / r.height })
    boxRef.current?.focus()
  }

  function key(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return
    if (e.key.length === 1) { e.preventDefault(); send({ kind: 'type', text: e.key }) }
    else if (KEYS.includes(e.key)) { e.preventDefault(); send({ kind: 'key', key: e.key }) }
  }

  function paste(e) {
    e.preventDefault()
    send({ kind: 'type', text: e.clipboardData.getData('text').slice(0, 500) })
  }

  async function done() {
    await remoteSignIn.close().catch(() => {})
    onClose(false)
  }

  return (
    <Modal title={`Sign in to ${name}`} onClose={done}>
      <p className="form-hint" style={{ marginBottom: '0.5rem' }}>
        This is a live view of a browser on the app's computer, signed in only for <strong>your</strong> account.
        Click into the page, then type — passwords too (they go straight to {name}; the app never stores them).
        It closes by itself once you're signed in{name === 'LinkedIn' || name === 'Naukri' ? '' : ' — or click Done'}.
      </p>
      {error && <div className="alert alert-error">⚠️ {error}</div>}
      <div ref={boxRef} tabIndex={0} onKeyDown={key} onPaste={paste} className="remote-view"
        aria-label={`${name} sign-in page — click and type here`}
        onWheel={e => send({ kind: 'scroll', dy: e.deltaY })}>
        {src
          ? <img ref={imgRef} src={src} alt={`Live view of ${url || name}`} onClick={click} draggable={false} />
          : <div className="remote-wait">{state === 'closed' || state === 'error' ? 'The window closed.' : 'Opening the sign-in page…'}</div>}
      </div>
      <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="btn btn-secondary btn-sm" onClick={() => send({ kind: 'key', key: 'Enter' })}>↵ Enter</button>
        <button className="btn btn-secondary btn-sm" onClick={() => send({ kind: 'key', key: 'Tab' })}>⇥ Tab</button>
        <button className="btn btn-secondary btn-sm" onClick={() => send({ kind: 'key', key: 'Backspace' })}>⌫</button>
        <span className="form-hint" style={{ flex: 1 }}>{url}</span>
        <button className="btn btn-primary btn-sm" onClick={done}>Done</button>
      </div>
    </Modal>
  )
}
