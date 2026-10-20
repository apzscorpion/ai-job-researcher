import React, { useEffect, useRef, useState } from 'react'
import { remoteSignIn } from '../api'

const KEYS = ['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']

// Live picture of the browser running on the app's computer; click and type to control it
export default function LiveView({ label, url }) {
  const [src, setSrc]     = useState('')
  const [error, setError] = useState('')
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
      } catch { /* window starting or closed */ }
    }
    const f = setInterval(frame, 800)
    frame()
    boxRef.current?.focus()
    return () => { alive = false; clearInterval(f); if (last) URL.revokeObjectURL(last) }
  }, [])

  const send = body => remoteSignIn.input(body).then(() => setError('')).catch(err => setError(err.message))

  function click(e) {
    const r = imgRef.current.getBoundingClientRect()
    // where in the picture, as fractions: the server maps them onto the page (works at any display scaling)
    send({ kind: 'click', x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height })
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

  return (
    <>
      {error && <div className="alert alert-error">⚠️ {error}</div>}
      <div ref={boxRef} tabIndex={0} onKeyDown={key} onPaste={paste} className="remote-view"
        aria-label={`${label} — click and type here`} onWheel={e => send({ kind: 'scroll', dy: e.deltaY })}>
        {src
          ? <img ref={imgRef} src={src} alt={`Live view of ${url || label}`} onClick={click} draggable={false} />
          : <div className="remote-wait">Opening the page…</div>}
      </div>
      <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="btn btn-secondary btn-sm" onClick={() => send({ kind: 'key', key: 'Enter' })}>↵ Enter</button>
        <button className="btn btn-secondary btn-sm" onClick={() => send({ kind: 'key', key: 'Tab' })}>⇥ Tab</button>
        <button className="btn btn-secondary btn-sm" onClick={() => send({ kind: 'key', key: 'Backspace' })}>⌫</button>
        <span className="form-hint" style={{ flex: 1, wordBreak: 'break-all' }}>{url}</span>
      </div>
    </>
  )
}
