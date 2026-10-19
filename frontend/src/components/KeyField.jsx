import React, { useState } from 'react'
import { revealKey, testKey } from '../api'
import Spinner from './Spinner'

// API key input with show/hide (fetches the saved key only when asked) and a live test of the key
export default function KeyField({ savedId, value, onChange, placeholder, test, style }) {
  const [shown, setShown]     = useState(false)
  const [busy, setBusy]       = useState('')
  const [result, setResult]   = useState(null)
  const [error, setError]     = useState('')

  async function toggle() {
    setError('')
    if (!shown && !value && savedId) {
      setBusy('reveal')
      try { onChange((await revealKey(savedId)).key) }
      catch (err) { setError(err.message); setBusy(''); return }
      setBusy('')
    }
    setShown(s => !s)
  }

  async function runTest() {
    setBusy('test'); setError(''); setResult(null)
    try { setResult(await testKey({ ...test, id: savedId || null, key: value || '' })) }
    catch (err) { setError(err.message) }
    finally { setBusy('') }
  }

  return (
    <div style={{ minWidth: 0, ...style }}>
      <div style={{ display: 'flex', gap: '0.35rem' }}>
        <input className="form-control" type={shown ? 'text' : 'password'} aria-label="API key"
          autoComplete="off" spellCheck={false} placeholder={placeholder}
          value={value} onChange={e => onChange(e.target.value)} />
        <button type="button" className="btn btn-secondary btn-sm" onClick={toggle} disabled={busy === 'reveal'}
          aria-label={shown ? 'Hide key' : 'Show key'} aria-pressed={shown} title={shown ? 'Hide key' : 'Show key'}>
          {busy === 'reveal' ? <Spinner size="sm" /> : shown ? '🙈' : '👁'}
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={runTest} disabled={busy === 'test'}
          title="Send one tiny request with this key and show the provider's answer">
          {busy === 'test' ? <><Spinner size="sm" /> Testing…</> : '🧪 Test'}
        </button>
      </div>
      {error && <div className="key-test key-test-bad">⚠️ {error}</div>}
      {result?.account && (
        <div className="key-test" style={{ color: '#334155' }}>
          ℹ️ {result.account.label ? <>Key name <strong>{result.account.label}</strong> · </> : null}
          {result.account.free_tier ? 'free tier' : 'paid account'} · used ${Number(result.account.usage_usd || 0).toFixed(4)}
          {result.account.limit_usd != null && ` of $${result.account.limit_usd} limit`}
        </div>
      )}
      {result && (
        <ul className="key-test" aria-label="Test results">
          {result.results.map(r => (
            <li key={r.model} className={r.ok ? 'key-test-ok' : 'key-test-bad'}>
              {r.ok
                ? <>✅ <strong>{r.model}</strong> answered “{r.reply}” in {r.ms} ms{r.tokens ? ` · ${r.tokens} tokens` : ''}</>
                : <>❌ <strong>{r.model}</strong> — {r.error}</>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
