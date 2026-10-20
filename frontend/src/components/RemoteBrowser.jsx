import React, { useEffect, useState } from 'react'
import { remoteSignIn } from '../api'
import LiveView from './LiveView'
import Modal from './Modal'

// Sign in to your own job-site account through a live view of a browser on the app's computer
export default function RemoteBrowser({ name, onClose }) {
  const [url, setUrl] = useState('')

  useEffect(() => {
    let alive = true
    const t = setInterval(async () => {
      try {
        const s = await remoteSignIn.status()
        if (!alive) return
        setUrl(s.url || '')
        if (s.state === 'signed_in') onClose(true)
      } catch { /* keep polling */ }
    }, 2000)
    return () => { alive = false; clearInterval(t) }
  }, [onClose])

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
      <LiveView label={`${name} sign-in page`} url={url} />
      <div style={{ textAlign: 'right', marginTop: '0.5rem' }}>
        <button className="btn btn-primary btn-sm" onClick={done}>Done</button>
      </div>
    </Modal>
  )
}
