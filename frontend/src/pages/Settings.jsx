import React, { useState, useEffect, useCallback } from 'react'
import {
  getSettings, updateSettings,
  testEmail, connectBrowser, getBrowserStatus,
} from '../api'
import Spinner from '../components/Spinner'

// ---------------------------------------------------------------------------
// Helper toggle
// ---------------------------------------------------------------------------
function Toggle({ label, checked, onChange, disabled }) {
  return (
    <label className="toggle-switch">
      <input
        type="checkbox"
        checked={!!checked}
        onChange={e => onChange(e.target.checked)}
        disabled={disabled}
      />
      <span className="toggle-label">{label}</span>
    </label>
  )
}

// ---------------------------------------------------------------------------
// Section card wrapper
// ---------------------------------------------------------------------------
function Section({ title, icon, children }) {
  return (
    <div className="card" style={{ marginBottom: '1.25rem' }}>
      <div className="card-title">{icon} {title}</div>
      {children}
    </div>
  )
}

// Starting points for extra AI providers; model names change, so check the provider's docs
const PROVIDER_PRESETS = {
  gemini:     { kind: 'gemini', name: 'Gemini (extra key)', base_url: '', model: '' },
  groq:       { kind: 'openai', name: 'Groq',       base_url: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile' },
  openrouter: { kind: 'openai', name: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1',   model: 'google/gemma-4-31b-it:free' },
  openai:     { kind: 'openai', name: 'OpenAI',     base_url: 'https://api.openai.com/v1',      model: 'gpt-4o-mini' },
  mistral:    { kind: 'openai', name: 'Mistral',    base_url: 'https://api.mistral.ai/v1',      model: 'mistral-small-latest' },
  deepseek:   { kind: 'openai', name: 'DeepSeek',   base_url: 'https://api.deepseek.com/v1',    model: 'deepseek-chat' },
  custom:     { kind: 'openai', name: 'Custom (OpenAI-compatible)', base_url: '', model: '' },
}

// Questions application forms ask that a résumé rarely answers
const STANDARD_QUESTIONS = [
  ['notice_period',       'Notice period',                    'e.g. 30 days / Immediate'],
  ['expected_salary',     'Expected salary',                  'e.g. USD 60,000 per year, or ₹30 LPA'],
  ['current_salary',      'Current salary (optional)',        'Leave blank to skip questions about it'],
  ['work_authorization',  'Work authorisation',               'e.g. Indian citizen; can work remotely as a contractor or employee'],
  ['needs_sponsorship',   'Need visa sponsorship?',           'e.g. No for remote roles; Yes for on-site roles abroad'],
  ['willing_to_relocate', 'Willing to relocate?',             'e.g. Yes, to the EU or Middle East'],
  ['earliest_start',      'Earliest start date',              'e.g. 30 days after offer'],
  ['other',               'Anything else forms might ask',    'e.g. Prefer remote; available for calls 10:00–20:00 IST'],
]

// ---------------------------------------------------------------------------
// Settings page
// ---------------------------------------------------------------------------
export default function Settings() {
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState('')
  const [settings, setSettings] = useState({})

  // Per-section save states
  const [smtpSaving, setSmtpSaving] = useState(false)
  const [smtpMsg, setSmtpMsg]       = useState('')
  const [smtpErr, setSmtpErr]       = useState('')
  const [testingEmail, setTestingEmail] = useState(false)
  const [testEmailMsg, setTestEmailMsg] = useState('')
  const [testEmailErr, setTestEmailErr] = useState('')

  const [geminiSaving, setGeminiSaving] = useState(false)
  const [geminiMsg, setGeminiMsg]       = useState('')
  const [geminiErr, setGeminiErr]       = useState('')
  const [geminiKey, setGeminiKey]       = useState('')
  const [geminiSaved, setGeminiSaved]   = useState(false)

  const [providers, setProviders]   = useState([])
  const [providerPreset, setProviderPreset] = useState('groq')
  const [provSaving, setProvSaving] = useState(false)
  const [provMsg, setProvMsg]       = useState('')
  const [provErr, setProvErr]       = useState('')

  const [answers, setAnswers]       = useState({})
  const [ansSaving, setAnsSaving]   = useState(false)
  const [ansMsg, setAnsMsg]         = useState('')
  const [ansErr, setAnsErr]         = useState('')

  const [prefSaving, setPrefSaving] = useState(false)
  const [prefMsg, setPrefMsg]       = useState('')
  const [prefErr, setPrefErr]       = useState('')

  const [exclSaving, setExclSaving] = useState(false)
  const [exclMsg, setExclMsg]       = useState('')
  const [exclErr, setExclErr]       = useState('')

  // Browser status
  const [browserStatus, setBrowserStatus] = useState({})
  const [connectingPlatform, setConnectingPlatform] = useState(null)
  const [connectMsg, setConnectMsg]   = useState('')
  const [connectErr, setConnectErr]   = useState('')

  // Local form state
  const [smtp, setSmtp] = useState({
    smtp_host: '', smtp_port: 587, smtp_user: '', smtp_password: '',
    smtp_from: '', smtp_tls: true, auto_send_email: true,
  })
  const [prefs, setPrefs] = useState({
    target_locations: '', remote_ok: false,
    min_salary: 0, min_rating: 0, score_threshold: 6,
    daily_cap: 10, apply_start_hour: 9, apply_end_hour: 18,
    search_interval_hours: 4, use_gemini_scoring: true, auto_apply_enabled: false,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  })
  const [excl, setExcl] = useState({
    excluded_companies: '', excluded_keywords: '',
    ignore_degree: false, ignore_yoe_gap: false, yoe_tolerance: 1,
    partial_skills_ok: false,
  })

  // ---------------------------------------------------------------------------
  const loadSettings = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await getSettings()
      setSettings(data)
      // SMTP
      setSmtp({
        smtp_host:     data.smtp_host     || '',
        smtp_port:     data.smtp_port     ?? 587,
        smtp_user:     data.smtp_user     || '',
        smtp_password: '',   // never pre-fill password
        smtp_from:     data.smtp_from     || '',
        smtp_tls:      data.smtp_tls      ?? true,
        auto_send_email: data.auto_send_email ?? true,
      })
      // AI
      setGeminiSaved(!!data.gemini_key)
      setProviders(data.ai_providers || [])
      setAnswers(data.standard_answers || {})
      // Prefs
      setPrefs({
        target_locations:       Array.isArray(data.target_locations) ? data.target_locations.join(', ') : (data.target_locations || ''),
        remote_ok:              data.remote_ok              ?? false,
        min_salary:             data.min_salary             ?? 0,
        min_rating:             data.min_rating             ?? 0,
        score_threshold:        data.score_threshold        ?? 6,
        daily_cap:              data.daily_cap              ?? 10,
        apply_start_hour:       data.apply_start_hour       ?? 9,
        apply_end_hour:         data.apply_end_hour         ?? 18,
        search_interval_hours:  data.search_interval_hours  ?? 4,
        use_gemini_scoring:     data.use_gemini_scoring     ?? true,
        auto_apply_enabled:     data.auto_apply_enabled     ?? false,
        timezone:               data.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone,
      })
      // Exclusions
      setExcl({
        excluded_companies: Array.isArray(data.excluded_companies) ? data.excluded_companies.join(', ') : (data.excluded_companies || ''),
        excluded_keywords:  Array.isArray(data.excluded_keywords)  ? data.excluded_keywords.join(', ')  : (data.excluded_keywords  || ''),
        ignore_degree:      data.ignore_degree    ?? false,
        ignore_yoe_gap:     data.ignore_yoe_gap   ?? false,
        yoe_tolerance:      data.yoe_tolerance    ?? 1,
        partial_skills_ok:  data.partial_skills_ok ?? false,
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadBrowserStatus = useCallback(async () => {
    try {
      const data = await getBrowserStatus()
      setBrowserStatus(data || {})
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    loadSettings()
    loadBrowserStatus()
  }, [loadSettings, loadBrowserStatus])

  // ---------------------------------------------------------------------------
  // SMTP save
  async function handleSmtpSave(e) {
    e.preventDefault()
    setSmtpMsg(''); setSmtpErr('')
    setSmtpSaving(true)
    try {
      const payload = { ...smtp }
      if (!payload.smtp_password) delete payload.smtp_password // don't overwrite if blank
      await updateSettings(payload)
      setSmtpMsg('SMTP settings saved!')
      setTimeout(() => setSmtpMsg(''), 4000)
    } catch (err) { setSmtpErr(err.message) }
    finally { setSmtpSaving(false) }
  }

  // Test email
  async function handleTestEmail() {
    setTestEmailMsg(''); setTestEmailErr('')
    setTestingEmail(true)
    try {
      const res = await testEmail()
      if (!res?.ok) throw new Error(res?.error || 'Test email failed.')
      setTestEmailMsg('Test email sent — check your inbox.')
    } catch (err) { setTestEmailErr(err.message) }
    finally { setTestingEmail(false) }
  }

  // ---------------------------------------------------------------------------
  // Gemini save
  async function handleGeminiSave(e) {
    e.preventDefault()
    setGeminiMsg(''); setGeminiErr('')
    if (!geminiKey) { setGeminiErr('Please enter an API key.'); return }
    setGeminiSaving(true)
    try {
      await updateSettings({ gemini_key: geminiKey })
      setGeminiSaved(true)
      setGeminiKey('')
      setGeminiMsg('API key saved!')
      setTimeout(() => setGeminiMsg(''), 4000)
    } catch (err) { setGeminiErr(err.message) }
    finally { setGeminiSaving(false) }
  }

  // ---------------------------------------------------------------------------
  // Extra AI providers
  const editProvider = (i, key, value) =>
    setProviders(list => list.map((p, j) => (j === i ? { ...p, [key]: value } : p)))

  async function handleProvidersSave(e) {
    e.preventDefault()
    setProvMsg(''); setProvErr('')
    setProvSaving(true)
    try {
      const data = await updateSettings({ ai_providers: providers })
      setProviders(data.ai_providers || [])
      setProvMsg('AI providers saved! They are tried in this order after the main Gemini key.')
      setTimeout(() => setProvMsg(''), 5000)
    } catch (err) { setProvErr(err.message) }
    finally { setProvSaving(false) }
  }

  // Standard answers
  async function handleAnswersSave(e) {
    e.preventDefault()
    setAnsMsg(''); setAnsErr('')
    setAnsSaving(true)
    try {
      await updateSettings({ standard_answers: answers })
      setAnsMsg('Answers saved — application forms will use them.')
      setTimeout(() => setAnsMsg(''), 4000)
    } catch (err) { setAnsErr(err.message) }
    finally { setAnsSaving(false) }
  }

  // ---------------------------------------------------------------------------
  // Prefs save
  function csvToArray(str) {
    return str.split(',').map(s => s.trim()).filter(Boolean)
  }

  async function handlePrefsSave(e) {
    e.preventDefault()
    setPrefMsg(''); setPrefErr('')
    setPrefSaving(true)
    try {
      await updateSettings({
        target_locations:      csvToArray(prefs.target_locations),
        remote_ok:             prefs.remote_ok,
        min_salary:            Number(prefs.min_salary),
        min_rating:            Number(prefs.min_rating),
        score_threshold:       Number(prefs.score_threshold),
        daily_cap:             Number(prefs.daily_cap),
        apply_start_hour:      Number(prefs.apply_start_hour),
        apply_end_hour:        Number(prefs.apply_end_hour),
        search_interval_hours: Number(prefs.search_interval_hours),
        use_gemini_scoring:    prefs.use_gemini_scoring,
        auto_apply_enabled:    prefs.auto_apply_enabled,
        timezone:              prefs.timezone,
      })
      setPrefMsg('Search preferences saved!')
      setTimeout(() => setPrefMsg(''), 4000)
    } catch (err) { setPrefErr(err.message) }
    finally { setPrefSaving(false) }
  }

  // ---------------------------------------------------------------------------
  // Exclusions save
  async function handleExclSave(e) {
    e.preventDefault()
    setExclMsg(''); setExclErr('')
    setExclSaving(true)
    try {
      await updateSettings({
        excluded_companies: csvToArray(excl.excluded_companies),
        excluded_keywords:  csvToArray(excl.excluded_keywords),
        ignore_degree:      excl.ignore_degree,
        ignore_yoe_gap:     excl.ignore_yoe_gap,
        yoe_tolerance:      Number(excl.yoe_tolerance),
        partial_skills_ok:  excl.partial_skills_ok,
      })
      setExclMsg('Exclusion settings saved!')
      setTimeout(() => setExclMsg(''), 4000)
    } catch (err) { setExclErr(err.message) }
    finally { setExclSaving(false) }
  }

  // ---------------------------------------------------------------------------
  // Browser connect
  async function handleConnect(platform) {
    setConnectMsg(''); setConnectErr('')
    setConnectingPlatform(platform)
    try {
      const res = await connectBrowser(platform)
      setConnectMsg(res?.message || `Browser launched for ${platform} — please log in manually.`)
      setTimeout(() => loadBrowserStatus(), 5000)
    } catch (err) { setConnectErr(err.message) }
    finally { setConnectingPlatform(null) }
  }

  // ---------------------------------------------------------------------------
  if (loading) return <Spinner text="Loading settings…" />
  if (error)   return <div className="alert alert-error">⚠️ {error}</div>

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Settings</h1>
      </div>

      {/* ── SMTP ─────────────────────────────────────────── */}
      <Section title="Email sending (SMTP)" icon="📧">
        <p className="form-hint" style={{ marginBottom: '0.75rem' }}>
          When a job lists a recruiter email, the AI writes a tailored email and it is sent from this mailbox
          with your résumé attached. Gmail: host <code>smtp.gmail.com</code>, port 587, and an
          {' '}<a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">app password</a>
          {' '}(not your normal password).
        </p>
        {smtpMsg && <div className="alert alert-success">✅ {smtpMsg}</div>}
        {smtpErr && <div className="alert alert-error">⚠️ {smtpErr}</div>}
        <form onSubmit={handleSmtpSave}>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">SMTP Host</label>
              <input type="text" className="form-control" value={smtp.smtp_host}
                onChange={e => setSmtp(p => ({ ...p, smtp_host: e.target.value }))}
                placeholder="smtp.gmail.com" />
            </div>
            <div className="form-group">
              <label className="form-label">SMTP Port</label>
              <input type="number" className="form-control" value={smtp.smtp_port}
                onChange={e => setSmtp(p => ({ ...p, smtp_port: e.target.value }))}
                placeholder="587" />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Username / Email</label>
              <input type="email" className="form-control" value={smtp.smtp_user}
                onChange={e => setSmtp(p => ({ ...p, smtp_user: e.target.value }))}
                placeholder="you@gmail.com" />
            </div>
            <div className="form-group">
              <label className="form-label">Password</label>
              <input type="password" className="form-control" value={smtp.smtp_password}
                onChange={e => setSmtp(p => ({ ...p, smtp_password: e.target.value }))}
                placeholder="Leave blank to keep existing" />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">From Email</label>
              <input type="email" className="form-control" value={smtp.smtp_from}
                onChange={e => setSmtp(p => ({ ...p, smtp_from: e.target.value }))}
                placeholder="your.name@gmail.com" />
            </div>
            <div className="form-group" style={{ justifyContent: 'flex-end', paddingTop: '1.5rem' }}>
              <Toggle label="Use TLS" checked={smtp.smtp_tls}
                onChange={v => setSmtp(p => ({ ...p, smtp_tls: v }))} />
            </div>
          </div>
          <div style={{ marginBottom: '1rem' }}>
            <Toggle label="Send application emails automatically (off = save as drafts for you to review)"
              checked={smtp.auto_send_email} onChange={v => setSmtp(p => ({ ...p, auto_send_email: v }))} />
          </div>
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
            <button type="submit" className="btn btn-primary" disabled={smtpSaving}>
              {smtpSaving ? <><Spinner size="sm" /> Saving…</> : '💾 Save SMTP'}
            </button>
            <button type="button" className="btn btn-secondary" onClick={handleTestEmail} disabled={testingEmail}>
              {testingEmail ? <><Spinner size="sm" /> Sending…</> : '📤 Send Test Email'}
            </button>
          </div>
          {testEmailMsg && <div className="alert alert-success" style={{ marginTop: '0.75rem' }}>✅ {testEmailMsg}</div>}
          {testEmailErr && <div className="alert alert-error"   style={{ marginTop: '0.75rem' }}>⚠️ {testEmailErr}</div>}
        </form>
      </Section>

      {/* ── Gemini API Key ───────────────────────────────── */}
      <Section title="Gemini API Key" icon="🤖">
        {geminiMsg && <div className="alert alert-success">✅ {geminiMsg}</div>}
        {geminiErr && <div className="alert alert-error">⚠️ {geminiErr}</div>}
        {geminiSaved && (
          <div className="alert alert-info" style={{ marginBottom: '0.75rem' }}>
            🔑 API key is saved (masked: <code>***</code>)
          </div>
        )}
        <form onSubmit={handleGeminiSave}>
          <div className="form-group">
            <label className="form-label">
              {geminiSaved ? 'Replace API Key' : 'API Key'}
            </label>
            <input
              type="password"
              className="form-control"
              value={geminiKey}
              onChange={e => setGeminiKey(e.target.value)}
              placeholder={geminiSaved ? 'Enter new key to replace…' : 'AIza…'}
              style={{ maxWidth: 400 }}
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={geminiSaving}>
            {geminiSaving ? <><Spinner size="sm" /> Saving…</> : '💾 Save Key'}
          </button>
        </form>
      </Section>

      {/* ── Extra AI providers ───────────────────────────── */}
      <Section title="More AI providers" icon="🔑">
        <p className="form-hint" style={{ marginBottom: '0.75rem' }}>
          Used for scoring, cover letters, emails and form answers. They are tried in order after the main
          Gemini key; when one runs out of its free daily quota the next takes over, and your PC's local model is
          the last resort. Any OpenAI-compatible API works (Groq, OpenRouter, OpenAI, Mistral, DeepSeek…).
          Keys are stored encrypted and only the last 4 characters are shown.
        </p>
        {provMsg && <div className="alert alert-success">✅ {provMsg}</div>}
        {provErr && <div className="alert alert-error">⚠️ {provErr}</div>}
        <form onSubmit={handleProvidersSave}>
          {providers.map((p, i) => (
            <div key={p.id || `new-${i}`} className="edit-row">
              <span className="badge badge-pending" style={{ minWidth: 24 }}>{i + 1}</span>
              <input className="form-control" aria-label="Name" placeholder="Name"
                value={p.name} onChange={e => editProvider(i, 'name', e.target.value)} />
              {p.kind === 'openai' && (
                <>
                  <input className="form-control" aria-label="Base URL" placeholder="https://…/v1"
                    value={p.base_url} onChange={e => editProvider(i, 'base_url', e.target.value)} />
                  <input className="form-control" aria-label="Model" placeholder="model name"
                    value={p.model} onChange={e => editProvider(i, 'model', e.target.value)} />
                </>
              )}
              <input className="form-control" type="password" aria-label="API key"
                placeholder={p.id ? 'Key saved — type to replace' : 'API key'}
                value={p.key?.startsWith('…') ? '' : p.key}
                onChange={e => editProvider(i, 'key', e.target.value)} />
              <button type="button" className="btn btn-danger btn-sm" aria-label={`Remove ${p.name}`}
                onClick={() => setProviders(list => list.filter((_, j) => j !== i))}>🗑</button>
            </div>
          ))}
          <div className="edit-row">
            <select className="form-control" aria-label="Provider type" style={{ maxWidth: 260 }}
              value={providerPreset} onChange={e => setProviderPreset(e.target.value)}>
              {Object.entries(PROVIDER_PRESETS).map(([k, v]) => <option key={k} value={k}>{v.name}</option>)}
            </select>
            <button type="button" className="btn btn-secondary btn-sm"
              onClick={() => setProviders(list => [...list, { ...PROVIDER_PRESETS[providerPreset], key: '' }])}>
              + Add provider
            </button>
          </div>
          <span className="form-hint">Model names change over time — check the provider's docs if one stops working.</span>
          <div style={{ marginTop: '0.75rem' }}>
            <button type="submit" className="btn btn-primary" disabled={provSaving}>
              {provSaving ? <><Spinner size="sm" /> Saving…</> : '💾 Save AI providers'}
            </button>
          </div>
        </form>
      </Section>

      {/* ── Standard answers ─────────────────────────────── */}
      <Section title="Standard answers for application forms" icon="📝">
        <p className="form-hint" style={{ marginBottom: '0.75rem' }}>
          LinkedIn, Naukri and company forms often ask these. Auto-apply answers only from your résumé and
          these answers — when a required question isn't covered, it stops and asks you instead of guessing.
        </p>
        {ansMsg && <div className="alert alert-success">✅ {ansMsg}</div>}
        {ansErr && <div className="alert alert-error">⚠️ {ansErr}</div>}
        <form onSubmit={handleAnswersSave}>
          <div className="form-row" style={{ flexWrap: 'wrap' }}>
            {STANDARD_QUESTIONS.map(([key, label, placeholder]) => (
              <div key={key} className="form-group" style={{ minWidth: 260, flex: '1 1 45%' }}>
                <label className="form-label" htmlFor={`ans-${key}`}>{label}</label>
                <input id={`ans-${key}`} type="text" className="form-control" placeholder={placeholder}
                  value={answers[key] || ''} onChange={e => setAnswers(a => ({ ...a, [key]: e.target.value }))} />
              </div>
            ))}
          </div>
          <button type="submit" className="btn btn-primary" disabled={ansSaving}>
            {ansSaving ? <><Spinner size="sm" /> Saving…</> : '💾 Save answers'}
          </button>
        </form>
      </Section>

      {/* ── Search & Scoring Prefs ───────────────────────── */}
      <Section title="Search & Scoring Preferences" icon="🎯">
        {prefMsg && <div className="alert alert-success">✅ {prefMsg}</div>}
        {prefErr && <div className="alert alert-error">⚠️ {prefErr}</div>}
        <p className="form-hint" style={{ marginBottom: '0.75rem' }}>
          Job search is based strictly on your résumé: it searches for the roles in your work history and keeps
          only jobs that match the skills you selected on the Profile page.
        </p>
        <form onSubmit={handlePrefsSave}>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Target Locations</label>
              <input type="text" className="form-control" value={prefs.target_locations}
                onChange={e => setPrefs(p => ({ ...p, target_locations: e.target.value }))}
                placeholder="Bangalore, Remote, …" />
              <span className="form-hint">Comma-separated</span>
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Minimum Salary (LPA / K)</label>
              <input type="number" className="form-control" value={prefs.min_salary}
                onChange={e => setPrefs(p => ({ ...p, min_salary: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Minimum Company Rating</label>
              <input type="number" className="form-control" value={prefs.min_rating}
                onChange={e => setPrefs(p => ({ ...p, min_rating: e.target.value }))}
                min={0} max={5} step={0.5} />
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Score Threshold (1–10)</label>
            <div className="slider-group">
              <input type="range" min={1} max={10} step={0.5}
                value={prefs.score_threshold}
                onChange={e => setPrefs(p => ({ ...p, score_threshold: e.target.value }))} />
              <span className="slider-value">{prefs.score_threshold}</span>
            </div>
            <span className="form-hint">Jobs below this score will not be auto-applied</span>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Daily Application Cap</label>
              <input type="number" className="form-control" value={prefs.daily_cap}
                onChange={e => setPrefs(p => ({ ...p, daily_cap: e.target.value }))} min={1} />
            </div>
            <div className="form-group">
              <label className="form-label">Search Interval (hours)</label>
              <input type="number" className="form-control" value={prefs.search_interval_hours}
                onChange={e => setPrefs(p => ({ ...p, search_interval_hours: e.target.value }))} min={1} />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Apply Start Hour (0–23)</label>
              <input type="number" className="form-control" value={prefs.apply_start_hour}
                onChange={e => setPrefs(p => ({ ...p, apply_start_hour: e.target.value }))} min={0} max={23} />
            </div>
            <div className="form-group">
              <label className="form-label">Apply End Hour (0–23)</label>
              <input type="number" className="form-control" value={prefs.apply_end_hour}
                onChange={e => setPrefs(p => ({ ...p, apply_end_hour: e.target.value }))} min={0} max={23} />
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Time Zone</label>
            <input type="text" className="form-control" value={prefs.timezone}
              onChange={e => setPrefs(p => ({ ...p, timezone: e.target.value }))}
              placeholder="Asia/Kolkata" />
            <span className="form-hint">Apply hours and the daily cap use this zone (IANA name, e.g. Asia/Kolkata)</span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.25rem', marginBottom: '1rem' }}>
            <Toggle label="Search remote jobs worldwide (priority)" checked={prefs.remote_ok}           onChange={v => setPrefs(p => ({ ...p, remote_ok: v }))} />
            <Toggle label="Use cloud AI for scoring (faster, but uses your daily AI quota)" checked={prefs.use_gemini_scoring} onChange={v => setPrefs(p => ({ ...p, use_gemini_scoring: v }))} />
            <Toggle label="Auto-Apply Enabled"   checked={prefs.auto_apply_enabled}  onChange={v => setPrefs(p => ({ ...p, auto_apply_enabled: v }))} />
          </div>
          <button type="submit" className="btn btn-primary" disabled={prefSaving}>
            {prefSaving ? <><Spinner size="sm" /> Saving…</> : '💾 Save Preferences'}
          </button>
        </form>
      </Section>

      {/* ── Exclusions & Relaxations ─────────────────────── */}
      <Section title="Exclusions & Relaxations" icon="🚫">
        {exclMsg && <div className="alert alert-success">✅ {exclMsg}</div>}
        {exclErr && <div className="alert alert-error">⚠️ {exclErr}</div>}
        <form onSubmit={handleExclSave}>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Excluded Companies</label>
              <input type="text" className="form-control" value={excl.excluded_companies}
                onChange={e => setExcl(p => ({ ...p, excluded_companies: e.target.value }))}
                placeholder="Acme Corp, Bad Company, …" />
              <span className="form-hint">Comma-separated</span>
            </div>
            <div className="form-group">
              <label className="form-label">Excluded Keywords</label>
              <input type="text" className="form-control" value={excl.excluded_keywords}
                onChange={e => setExcl(p => ({ ...p, excluded_keywords: e.target.value }))}
                placeholder="Intern, Contract, …" />
              <span className="form-hint">Comma-separated</span>
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">YoE Tolerance (years)</label>
            <input type="number" className="form-control" value={excl.yoe_tolerance}
              onChange={e => setExcl(p => ({ ...p, yoe_tolerance: e.target.value }))}
              min={0} max={10} style={{ maxWidth: 120 }} />
            <span className="form-hint">Allow jobs requiring this many more years than you have</span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.25rem', marginBottom: '1rem' }}>
            <Toggle label="Ignore Degree Requirement"  checked={excl.ignore_degree}     onChange={v => setExcl(p => ({ ...p, ignore_degree: v }))} />
            <Toggle label="Ignore YoE Gap"             checked={excl.ignore_yoe_gap}    onChange={v => setExcl(p => ({ ...p, ignore_yoe_gap: v }))} />
            <Toggle label="Partial Skills OK"          checked={excl.partial_skills_ok} onChange={v => setExcl(p => ({ ...p, partial_skills_ok: v }))} />
          </div>
          <button type="submit" className="btn btn-primary" disabled={exclSaving}>
            {exclSaving ? <><Spinner size="sm" /> Saving…</> : '💾 Save Exclusions'}
          </button>
        </form>
      </Section>

      {/* ── Browser Sessions ─────────────────────────────── */}
      <Section title="Browser Sessions" icon="🌐">
        <p className="form-hint" style={{ marginBottom: '0.75rem' }}>
          Click Connect, then log in within 2 minutes in the browser window that opens. To protect your accounts,
          auto-apply sends at most 15 LinkedIn and 10 Naukri applications a day, waits 8–20 minutes between them,
          and pauses a site as soon as it shows a login page, security check or captcha.
        </p>
        {connectMsg && <div className="alert alert-info">ℹ️ {connectMsg}</div>}
        {connectErr && <div className="alert alert-error">⚠️ {connectErr}</div>}
        {['linkedin', 'naukri'].map(platform => {
          const pausedReason = browserStatus.paused?.[platform]
          const isConnected = browserStatus[platform] === true && !pausedReason
          return (
            <div key={platform} className="browser-status-row">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                <span className={`connected-dot ${isConnected ? 'active' : 'inactive'}`} />
                <span style={{ fontWeight: 600, textTransform: 'capitalize' }}>{platform}</span>
                <span style={{ fontSize: '0.8rem', color: isConnected ? '#16a34a' : pausedReason ? '#dc2626' : '#94a3b8' }}>
                  {pausedReason ? `Paused — ${pausedReason}` : isConnected ? 'Session saved' : 'Not connected'}
                </span>
              </div>
              <button
                className={`btn btn-sm ${isConnected ? 'btn-secondary' : 'btn-primary'}`}
                onClick={() => handleConnect(platform)}
                disabled={connectingPlatform !== null}
              >
                {connectingPlatform === platform
                  ? <><Spinner size="sm" /> Launching…</>
                  : isConnected
                    ? '🔄 Reconnect'
                    : `🔗 Connect ${platform.charAt(0).toUpperCase() + platform.slice(1)}`
                }
              </button>
            </div>
          )
        })}
      </Section>
    </div>
  )
}
