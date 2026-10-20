import Pagination from '../components/Pagination'
import React, { useState, useEffect, useCallback } from 'react'
import { getApplications, updateApplicationStatus, sendApplicationEmail, cancelApplication, retryAllApplications, assistApi } from '../api'
import { safeUrl } from '../safeUrl'
import Spinner from '../components/Spinner'
import StatusBadge, { CHANNELS } from '../components/StatusBadge'
import SkillChips from '../components/SkillChips'

const STATUS_OPTIONS = ['queued', 'draft', 'applied', 'needs_manual', 'failed', 'replied']
const PAGE_SIZE = 20

// ---------------------------------------------------------------------------
// Score display
// ---------------------------------------------------------------------------
function ScoreCell({ score }) {
  const s = Number(score) || 0
  const cls = s >= 7 ? 'score-high' : s >= 5 ? 'score-mid' : 'score-low'
  return <span className={cls}>{s.toFixed ? s.toFixed(1) : s}</span>
}

// Render text with clickable links
function Linkified({ text }) {
  return String(text || '').split(/(https?:\/\/[^\s)]+)/g).map((part, i) =>
    /^https?:\/\//.test(part)
      ? <a key={i} href={safeUrl(part)} target="_blank" rel="noreferrer">{part}</a>
      : <React.Fragment key={i}>{part}</React.Fragment>)
}

function SentTo({ app }) {
  const to = app.sent_to
  if (!to) return <span style={{ color: '#94a3b8' }}>—</span>
  if (!/^https?:\/\//.test(to)) return <a href={`mailto:${to}`} onClick={e => e.stopPropagation()}>{to}</a>
  let host = to
  try { host = new URL(to).hostname } catch { /* show the raw link */ }
  return <a href={safeUrl(to)} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}>{host} ↗</a>
}

function CopyBlock({ title, text }) {
  const [copied, setCopied] = useState(false)
  return (
    <div style={{ marginTop: '0.75rem' }}>
      <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#475569', marginBottom: '0.35rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        {title}
        <button className="btn btn-secondary btn-sm" onClick={async () => {
          try { await navigator.clipboard.writeText(text); setCopied(true) } catch { /* select manually */ }
        }}>{copied ? '✅ Copied' : '📋 Copy'}</button>
      </div>
      <pre style={{
        fontSize: '0.82rem', color: '#334155', whiteSpace: 'pre-wrap', lineHeight: 1.6, background: '#fff',
        border: '1px solid #e2e8f0', borderRadius: 6, padding: '0.75rem', maxHeight: 280, overflowY: 'auto',
      }}>{text}</pre>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Expanded row — what happened, skills, cover letter, email
// ---------------------------------------------------------------------------
function ExpandedRow({ app, onSent }) {
  const [body, setBody]       = useState(app.email_body || '')
  const [sending, setSending] = useState(false)
  const [err, setErr]         = useState('')
  const isDraft = app.status === 'draft'
  const subject = (app.notes || '').match(/^Subject: (.*)$/m)?.[1] || ''

  async function handleSend() {
    setSending(true); setErr('')
    try {
      onSent(await sendApplicationEmail(app.id, { email_body: body, subject }))
    } catch (e) { setErr(e.message) }
    finally { setSending(false) }
  }

  const job = app.job || {}

  return (
    <tr>
      <td colSpan={8} style={{ padding: '0.75rem 1rem 1rem', background: '#f8fafc' }}>
        <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#475569', marginBottom: '0.35rem' }}>What happened</div>
        <p style={{ fontSize: '0.88rem', color: '#1e293b', whiteSpace: 'pre-wrap', margin: '0 0 0.6rem' }}>
          {app.status === 'queued'
            ? 'Waiting in the queue — auto-apply sends one application at a time within your apply hours and daily limits.'
            : <Linkified text={app.notes || 'No details recorded.'} />}
        </p>
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', fontSize: '0.85rem', marginBottom: '0.6rem' }}>
          {safeUrl(job.url) && <a href={safeUrl(job.url)} target="_blank" rel="noreferrer">🔗 Job posting</a>}
          {safeUrl(job.apply_url) && <a href={safeUrl(job.apply_url)} target="_blank" rel="noreferrer">🏢 Company application page</a>}
          {job.recruiter_email && <span>📧 Recruiter: <a href={`mailto:${job.recruiter_email}`}>{job.recruiter_email}</a></span>}
          {job.location && <span>📍 {job.location}</span>}
        </div>
        <SkillChips matched={job.matched_skills} missing={job.missing_skills} />
        {app.cover_letter && <CopyBlock title="✉️ Cover letter" text={app.cover_letter} />}
        {isDraft ? (
          <div style={{ marginTop: '0.75rem' }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#475569', marginBottom: '0.35rem' }}>
              ✉️ Draft — edit if needed, then send (your résumé is attached)
            </div>
            <textarea className="form-control" rows={12} value={body}
              onChange={e => setBody(e.target.value)} style={{ fontSize: '0.85rem', lineHeight: 1.6 }} />
            {err && <div className="alert alert-error" style={{ marginTop: '0.5rem' }}>⚠️ {err}</div>}
            <button className="btn btn-primary btn-sm" style={{ marginTop: '0.5rem' }}
              onClick={handleSend} disabled={sending || !body.trim()}>
              {sending ? <Spinner size="sm" /> : `Send to ${app.job?.recruiter_email || 'recruiter'}`}
            </button>
          </div>
        ) : app.email_body ? (
          <CopyBlock title={app.status === 'applied' ? `📧 Email sent to ${app.sent_to || 'recruiter'}` : '📧 Email'} text={app.email_body} />
        ) : null}
      </td>
    </tr>
  )
}

// ---------------------------------------------------------------------------
// Applications page
// ---------------------------------------------------------------------------
export default function Applications() {
  const [apps, setApps]     = useState([])
  const [total, setTotal]   = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState('')

  // Filters
  const [filterStatus, setFilterStatus]   = useState('')
  const [filterSource, setFilterSource]   = useState('')
  const [filterMinScore, setFilterMinScore] = useState('')
  const [filterMinSalary, setFilterMinSalary] = useState('')
  const [filterMaxSalary, setFilterMaxSalary] = useState('')
  const [filterMinRating, setFilterMinRating] = useState('')
  const [filterDateFrom, setFilterDateFrom] = useState('')
  const [filterDateTo, setFilterDateTo]   = useState('')
  const [searchText, setSearchText]       = useState('')
  const [page, setPage]                   = useState(1)

  // Expanded row
  const [expandedId, setExpandedId]       = useState(null)

  // Status update
  const [updatingId, setUpdatingId]       = useState(null)

  // ---------------------------------------------------------------------------
  const loadApps = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = {
        status:     filterStatus    || undefined,
        source:     filterSource    || undefined,
        min_score:  filterMinScore  || undefined,
        min_salary: filterMinSalary || undefined,
        max_salary: filterMaxSalary || undefined,
        min_rating: filterMinRating || undefined,
        date_from:  filterDateFrom  || undefined,
        date_to:    filterDateTo    || undefined,
        q:          searchText      || undefined,
        skip:       (page - 1) * PAGE_SIZE,
        limit:      PAGE_SIZE,
      }
      const data = await getApplications(params)
      setApps(data.items || data || [])
      setTotal(data.total || (data.items ? data.items.length : (data.length || 0)))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [filterStatus, filterSource, filterMinScore, filterMinSalary, filterMaxSalary,
      filterMinRating, filterDateFrom, filterDateTo, searchText, page])

  useEffect(() => { loadApps() }, [loadApps])
  useEffect(() => { setPage(1) }, [filterStatus, filterSource, filterMinScore, filterMinSalary,
                                    filterMaxSalary, filterMinRating, filterDateFrom, filterDateTo, searchText])

  // ---------------------------------------------------------------------------
  async function handleStatusChange(appId, newStatus) {
    setUpdatingId(appId)
    try {
      await updateApplicationStatus(appId, newStatus)
      setApps(prev => prev.map(a => a.id === appId ? { ...a, status: newStatus } : a))
    } catch (err) {
      alert(`Failed to update status: ${err.message}`)
    } finally {
      setUpdatingId(null)
    }
  }

  // "Fill with AI": live window where the agent fills the application (you can step in)
  const [assist, setAssist]   = useState(null)   // { appId, title }
  const [link, setLink]       = useState('')
  const [linkErr, setLinkErr] = useState('')
  const [linkBusy, setLinkBusy] = useState(false)
  // Fill everything but leave the final Submit to you (remembered on this device)
  const [review, setReview] = useState(() => { try { return localStorage.getItem('assistReview') === '1' } catch { return false } })
  function toggleReview(v) {
    setReview(v)
    try { localStorage.setItem('assistReview', v ? '1' : '0') } catch { /* private mode */ }
  }

  // The live view opens in its own tab (big browser + chat); a tab opened right on the click isn't blocked
  function openLive(tab) {
    if (tab && !tab.closed) tab.location.href = '/live'
    else window.location.href = '/live'
  }

  async function fillWithAi(app) {
    const tab = window.open('', '_blank')
    try {
      await assistApi.start(app.id, review)
      openLive(tab)
    } catch (err) { tab?.close(); alert(`Couldn't start: ${err.message}`) }
  }

  async function fillFromLink(e) {
    e.preventDefault()
    setLinkErr(''); setLinkBusy(true)
    const tab = window.open('', '_blank')
    try {
      await assistApi.fromLink({ url: link.trim(), review })
      setLink('')
      openLive(tab)
    } catch (err) { tab?.close(); setLinkErr(err.message) }
    finally { setLinkBusy(false) }
  }

  // An AI fill still running (e.g. its tab was closed): offer to open it again
  useEffect(() => {
    assistApi.status().then(s => {
      if (['starting', 'agent', 'asking', 'waiting_you'].includes(s.state)) setAssist({ title: s.title })
    }).catch(() => {})
  }, [])

  async function handleRetryAll() {
    if (!window.confirm('Re-apply to everything not sent yet (queued, needs you, failed, drafts)? ' +
                        'Each gets a fresh formal cover letter and goes out automatically, one at a time.')) return
    try {
      const res = await retryAllApplications()
      alert(`${res.requeued} application(s) are back in the queue.`)
      loadApps()
    } catch (err) { alert(`Couldn't retry: ${err.message}`) }
  }

  async function handleCancel(app) {
    if (!window.confirm(`Cancel the application for “${app.job?.title || 'this job'}”? It goes back to “Not applied yet” on the Jobs page.`)) return
    setUpdatingId(app.id)
    try {
      await cancelApplication(app.id)
      setApps(prev => prev.filter(a => a.id !== app.id))
    } catch (err) {
      alert(`Couldn't cancel: ${err.message}`)
    } finally {
      setUpdatingId(null)
    }
  }

  // ---------------------------------------------------------------------------
  function toggleExpand(id) {
    setExpandedId(prev => (prev === id ? null : id))
  }

  // ---------------------------------------------------------------------------
  function formatDate(ts) {
    if (!ts) return '—'
    // API timestamps are naive UTC
    try { return new Date(String(ts).endsWith('Z') ? ts : `${ts}Z`).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) } catch { return ts }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // ---------------------------------------------------------------------------
  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Applications</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span style={{ fontSize: '0.88rem', color: '#64748b' }}>{total} total</span>
          <button className="btn btn-primary btn-sm" onClick={handleRetryAll}
            title="Everything not sent yet goes back to the queue with a fresh formal cover letter">
            🔁 Retry everything not sent
          </button>
        </div>
      </div>


      {assist && (
        <div className="alert alert-info" role="status">
          🤖 The AI is filling “{assist.title || 'an application'}”.{' '}
          <a href="/live" target="_blank" rel="noreferrer">Open the live view ↗</a>
        </div>
      )}

      {/* ── Fill an application from a link ────────────────── */}
      <form className="fill-link" onSubmit={fillFromLink}>
        <input className="form-control" type="url" required aria-label="Application link" style={{ flex: '1 1 380px' }}
          placeholder="Paste any job or application link (Greenhouse, Lever, Workday, company careers page…)"
          value={link} onChange={e => setLink(e.target.value)} />
        <button className="btn btn-primary btn-sm" disabled={linkBusy || !link.trim()}>
          {linkBusy ? 'Opening…' : '🤖 Fill with AI'}
        </button>
        <label style={{ display: 'inline-flex', gap: '0.35rem', alignItems: 'center', fontSize: '0.82rem' }}>
          <input type="checkbox" checked={review} onChange={e => toggleReview(e.target.checked)} />
          Stop before Submit so I can review
        </label>
        {linkErr && <span style={{ color: '#b91c1c', fontSize: '0.82rem' }}>⚠️ {linkErr}</span>}
      </form>

      {/* ── Filters ──────────────────────────────────────── */}
      <div className="filters-bar">
        <div className="form-group">
          <label className="form-label">Search</label>
          <input type="text" className="form-control" placeholder="Title, company…"
            value={searchText} onChange={e => setSearchText(e.target.value)} />
        </div>
        <div className="form-group">
          <label className="form-label">Status</label>
          <select className="form-control" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
            <option value="">All</option>
            {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Source</label>
          <select className="form-control" value={filterSource} onChange={e => setFilterSource(e.target.value)}>
            <option value="">All</option>
            <option value="linkedin">LinkedIn</option>
            <option value="naukri">Naukri</option>
            <option value="indeed">Indeed</option>
            <option value="remotive">Remotive</option>
            <option value="remoteok">RemoteOK</option>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Min Score</label>
          <input type="number" className="form-control" placeholder="0" min={0} max={10}
            value={filterMinScore} onChange={e => setFilterMinScore(e.target.value)}
            style={{ width: 80 }} />
        </div>
        <div className="form-group">
          <label className="form-label">Min Salary</label>
          <input type="number" className="form-control" placeholder="0"
            value={filterMinSalary} onChange={e => setFilterMinSalary(e.target.value)}
            style={{ width: 100 }} />
        </div>
        <div className="form-group">
          <label className="form-label">Max Salary</label>
          <input type="number" className="form-control" placeholder="999"
            value={filterMaxSalary} onChange={e => setFilterMaxSalary(e.target.value)}
            style={{ width: 100 }} />
        </div>
        <div className="form-group">
          <label className="form-label">Min Rating</label>
          <input type="number" className="form-control" placeholder="0" min={0} max={5} step={0.5}
            value={filterMinRating} onChange={e => setFilterMinRating(e.target.value)}
            style={{ width: 80 }} />
        </div>
        <div className="form-group">
          <label className="form-label">From Date</label>
          <input type="date" className="form-control"
            value={filterDateFrom} onChange={e => setFilterDateFrom(e.target.value)} />
        </div>
        <div className="form-group">
          <label className="form-label">To Date</label>
          <input type="date" className="form-control"
            value={filterDateTo} onChange={e => setFilterDateTo(e.target.value)} />
        </div>
        <button className="btn btn-secondary" onClick={loadApps} style={{ alignSelf: 'flex-end' }}>
          🔄 Refresh
        </button>
      </div>

      {/* ── Table ────────────────────────────────────────── */}
      {loading
        ? <Spinner text="Loading applications…" />
        : error
          ? <div className="alert alert-error">⚠️ {error}</div>
          : apps.length === 0
            ? (
              <div className="empty-state">
                <div className="empty-state-icon">📋</div>
                <p>No applications found. Adjust filters or wait for the agent to apply.</p>
              </div>
            )
            : (
              <>
                <div className="table-wrapper">
                  <table>
                    <thead>
                      <tr>
                        <th>Job Title</th>
                        <th>Company</th>
                        <th>Channel</th>
                        <th>Sent to</th>
                        <th>Score</th>
                        <th>Status</th>
                        <th>When</th>
                        <th>Update Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {apps.map(app => {
                        const isExpanded = expandedId === app.id
                        const isUpdating = updatingId === app.id
                        const job = app.job || {}
                        const sourceCls = job.source ? `badge-source-${job.source.toLowerCase()}` : 'badge-pending'
                        return (
                          <React.Fragment key={app.id}>
                            <tr
                              className="expandable"
                              onClick={() => toggleExpand(app.id)}
                              style={{ background: isExpanded ? '#f0f7ff' : undefined }}
                            >
                              <td>
                                <span style={{ fontWeight: 600 }}>
                                  {isExpanded ? '▼ ' : '▶ '}
                                  {job.title || '—'}
                                </span>
                                {job.is_remote && <span className="badge badge-info" style={{ marginLeft: 6 }}>🌍 Remote</span>}
                              </td>
                              <td>
                                {job.company || '—'}
                                <div><span className={`badge ${sourceCls}`} title="Where the job was found">{job.source || '—'}</span></div>
                              </td>
                              <td>
                                <span className="badge badge-pending">{CHANNELS[app.method] || app.method || '—'}</span>
                              </td>
                              <td><SentTo app={app} /></td>
                              <td><ScoreCell score={job.score} /></td>
                              <td onClick={e => e.stopPropagation()}>
                                <StatusBadge status={app.status} />
                              </td>
                              <td>{formatDate(app.applied_at || app.created_at)}</td>
                              <td onClick={e => e.stopPropagation()}>
                                <select
                                  className="form-control"
                                  style={{ padding: '0.3rem 0.5rem', fontSize: '0.8rem', minWidth: 130 }}
                                  value={app.status || ''}
                                  onChange={e => handleStatusChange(app.id, e.target.value)}
                                  disabled={isUpdating}
                                >
                                  {STATUS_OPTIONS.map(s => (
                                    <option key={s} value={s}>{s}</option>
                                  ))}
                                </select>
                                {!['applied', 'replied', 'applying'].includes(app.status) && (
                                  <button className="btn btn-primary btn-sm" style={{ marginTop: '0.3rem', marginRight: '0.3rem' }}
                                    onClick={() => fillWithAi(app)}
                                    title="The AI fills this application in a live window you can watch and step into">
                                    🤖 Fill with AI
                                  </button>
                                )}
                                {!['applied', 'replied', 'applying'].includes(app.status) && (
                                  <button className="btn btn-secondary btn-sm" style={{ marginTop: '0.3rem' }}
                                    disabled={isUpdating} onClick={() => handleCancel(app)}
                                    title="Not sent yet: remove it so it never goes out">
                                    ✖ Cancel
                                  </button>
                                )}
                                {isUpdating && <Spinner size="sm" />}
                              </td>
                            </tr>
                            {isExpanded && <ExpandedRow app={app} onSent={updated => setApps(prev => prev.map(a => a.id === updated.id ? updated : a))} />}
                          </React.Fragment>
                        )
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Pagination */}
                <Pagination page={page} totalPages={totalPages} onChange={setPage} />
              </>
            )
      }
    </div>
  )
}
