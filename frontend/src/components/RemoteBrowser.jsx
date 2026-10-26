import React, { useEffect, useState } from 'react'
import { remoteSignIn } from '../api'
import LiveView from './LiveView'

// Sign in to your own job-site account through a live view of a browser on the app's computer.
// Its own tab (/connect?name=…), opened by Settings → Job sites → Connect; closes itself when done.
export default function RemoteBrowser() {
  const name = new URLSearchParams(window.location.search).get('name') || 'the site'
  const [url, setUrl] = useState('')
  const [signedIn, setSignedIn] = useState(false)

  useEffect(() => {
    let alive = true
    const t = setInterval(async () => {
      try {
        const s = await remoteSignIn.status()
        if (!alive) return
        setUrl(s.url || '')
        if (s.state === 'signed_in') { setSignedIn(true); clearInterval(t); setTimeout(() => window.close(), 1500) }
      } catch { /* keep polling */ }
    }, 2000)
    // closing the tab ends the session too, or the profile stays "in use" until it times out
    const end = () => remoteSignIn.close().catch(() => {})
    window.addEventListener('pagehide', end)
    return () => { alive = false; clearInterval(t); window.removeEventListener('pagehide', end) }
  }, [])

  async function done() {
    await remoteSignIn.close().catch(() => {})
    window.close()
  }

  return (
    <div className="live-page">
      <header className="live-head">
        <div className="live-title">
          <strong>Sign in to {name}</strong>
          <span>Live view of a browser on the app's computer, signed in only for your account — passwords go straight to {name}; the app never stores them.</span>
        </div>
        {signedIn && <span className="live-state live-ok">✅ Signed in — closing…</span>}
        <button className="btn btn-primary btn-sm" onClick={done}>Done</button>
      </header>
      <div className="live-browser">
        <LiveView label={`${name} sign-in page`} url={url} />
      </div>
    </div>
  )
}
