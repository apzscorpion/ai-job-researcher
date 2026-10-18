import WaitingBanner from '../components/WaitingBanner'
import React, { useState, useEffect, useCallback } from 'react'
import { getJobs, getJob, triggerSearch, getResumeReport, getCoverLetter, getRegions, applyToJobs, applyNow } from '../api'
import SkillChips from '../components/SkillChips'
import StatusBadge from '../components/StatusBadge'
import { safeUrl } from '../safeUrl'
import Spinner from '../components/Spinner'
import Modal from '../components/Modal'

// ---------------------------------------------------------------------------
// Score badge
// ---------------------------------------------------------------------------
function ScoreBadge({ score, status }) {
  if (status === 'new') return <span className="badge badge-pending" title="Scored on the next search">Not scored yet</span>
  if (status === 'skipped') return <span className="badge badge-pending">Filtered out</span>
  const s = Number(score) || 0
  const cls = s >= 7 ? 'score-high' : s >= 5 ? 'score-mid' : 'score-low'
  return <span className={`job-card-score ${cls}`}>{s.toFixed ? s.toFixed(1) : s}</span>
}

// ---------------------------------------------------------------------------
// Source badge
// ---------------------------------------------------------------------------
const SOURCE_NAMES = {
  linkedin: 'LinkedIn', naukri: 'Naukri', indeed: 'Indeed', remotive: 'Remotive',
  remoteok: 'RemoteOK', infopark: 'Infopark', technopark: 'Technopark', glassdoor: 'Glassdoor', google: 'Google Jobs',
}

function SourceBadge({ source }) {
  const cls = source ? `badge-source-${source.toLowerCase()}` : 'badge-pending'
  const name = SOURCE_NAMES[source?.toLowerCase()] || source || '—'
  return <span className={`badge ${cls}`} title={`Found on ${name}`}>Found on {name}</span>
}

// How the scheduler will apply (route_for on the backend)
function applyVia(job) {
  switch (job.apply_via) {
    case 'email':    return `📧 Email to ${job.recruiter_email} — résumé + cover letter attached`
    case 'linkedin': return '🔗 LinkedIn Easy Apply (or the company site it links to)'
    case 'naukri':   return '🟦 Naukri apply (or the company site it links to)'
    default:         return '🏢 Company site — we fill the form; captchas or logins come to you'
  }
}

// Jobs with no application yet (or a failed one) can be picked for applying
const pickable = job => !job.application_status || job.application_status === 'failed'

const VIA_BUTTON = {
  email:    job => ({ label: '📧 Apply by email', title: `Sends your résumé + an AI cover letter from your Gmail to ${job.recruiter_email}` }),
  linkedin: () => ({ label: '🔗 Apply on LinkedIn', title: 'LinkedIn Easy Apply (keeps the 8-minute gap and 15/day limit)' }),
  naukri:   () => ({ label: '🟦 Apply on Naukri', title: 'Naukri apply (keeps the gap and 10/day limit)' }),
  site:     () => ({ label: '🏢 Apply on company site', title: 'Fills the company application form; captchas or logins come to you' }),
}

// One button per way this job can be applied to; applies now instead of waiting in the queue
function ApplyButtons({ job, onDone }) {
  const [busy, setBusy] = useState('')
  const [msg, setMsg]   = useState('')
  const [err, setErr]   = useState('')
  const status = job.application_status
  if (['applied', 'replied', 'applying'].includes(status) || !job.apply_options?.length) return null

  async function go(via) {
    setBusy(via); setMsg(''); setErr('')
    try {
      const res = await applyNow(job.id, via)
      setMsg(res.message)
      setTimeout(onDone, res.started ? 4000 : 0)
    } catch (e) { setErr(e.message) }
    finally { setBusy('') }
  }

  return (
    <div onClick={e => e.stopPropagation()} style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', alignItems: 'center' }}>
      {job.apply_options.map(via => {
        const { label, title } = VIA_BUTTON[via](job)
        return (
          <button key={via} className="btn btn-primary btn-sm" title={title} disabled={!!busy} onClick={() => go(via)}>
            {busy === via ? <><Spinner size="sm" /> Starting…</> : label}
          </button>
        )
      })}
      {msg && <span style={{ fontSize: '0.78rem', color: '#166534' }}>✅ {msg}</span>}
      {err && <span style={{ fontSize: '0.78rem', color: '#b91c1c' }}>⚠️ {err}</span>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AI document for a job: résumé update report or cover letter (copyable)
// ---------------------------------------------------------------------------
function AiDoc({ jobId, fetcher, label, hint }) {
  const [report, setReport]   = useState('')
  const [info, setInfo]       = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')
  const [copied, setCopied]   = useState(false)

  async function generate() {
    setLoading(true); setError(''); setCopied(false)
    try {
      const res = await fetcher(jobId)
      setReport(res.report)
      setInfo(`Written by ${res.model} · job description from ${res.jd_source}`)
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(report)
      setCopied(true)
    } catch { setError('Copy failed — select the text and copy it manually.') }
  }

  return (
    <div className="card" style={{ padding: '0.75rem 1rem', marginBottom: '0.75rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-primary btn-sm" onClick={generate} disabled={loading}>
          {loading ? <><Spinner size="sm" /> Writing…</> : report ? `↻ Regenerate ${label.toLowerCase()}` : label}
        </button>
        {report && (
          <button className="btn btn-success btn-sm" onClick={copy}>
            {copied ? '✅ Copied' : '📋 Copy'}
          </button>
        )}
        <span style={{ fontSize: '0.8rem', color: '#64748b' }}>
          {loading
            ? 'Working… if every cloud AI key is out of quota, the local model takes a few minutes.'
            : info || hint}
        </span>
      </div>
      {error && <div className="alert alert-error" style={{ marginTop: '0.5rem' }}>⚠️ {error}</div>}
      {report && (
        <textarea
          readOnly
          value={report}
          aria-label={label}
          onFocus={e => e.target.select()}
          style={{
            width: '100%', minHeight: 280, marginTop: '0.5rem', fontFamily: 'monospace',
            fontSize: '0.8rem', padding: '0.6rem', borderRadius: 6, border: '1px solid #e2e8f0',
          }}
        />
      )}
    </div>
  )
}

function JobDetailModal({ job, onClose, onApplied }) {
  const score = Number(job.score) || 0
  const scoreCls = score >= 7 ? 'score-high' : score >= 5 ? 'score-mid' : 'score-low'
  return (
    <Modal title={`${job.title} — ${job.company}`} onClose={onClose}>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
        <SourceBadge source={job.source} />
        {job.is_remote && <span className="badge badge-info">🌍 Remote</span>}
        <span className={`badge ${scoreCls}`}>Score: {score.toFixed ? score.toFixed(1) : score}</span>
        {job.location && (
          <span className="badge badge-pending">📍 {job.location}</span>
        )}
        {job.salary_range && (
          <span className="badge badge-pending">💰 {job.salary_range}</span>
        )}
        {job.company_rating && (
          <span className="badge badge-pending">⭐ {job.company_rating}</span>
        )}
      </div>

      <p style={{ marginBottom: '0.5rem', fontSize: '0.88rem', color: '#334155' }}>
        <strong>How we apply:</strong> {applyVia(job)}
        {job.application_status && <> · <StatusBadge status={job.application_status} /></>}
      </p>
      <div style={{ marginBottom: '0.75rem' }}>
        <ApplyButtons job={job} onDone={onApplied} />
      </div>

      {job.recruiter_email && (
        <p style={{ marginBottom: '0.75rem', fontSize: '0.88rem', color: '#475569' }}>
          📧 Recruiter: <a href={`mailto:${job.recruiter_email}`}>{job.recruiter_email}</a>
        </p>
      )}

      {job.score_reason && (
        <div className="card" style={{ background: '#f8fafc', padding: '0.75rem 1rem', marginBottom: '0.75rem' }}>
          <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#475569', marginBottom: '0.25rem' }}>
            Score Reason
          </div>
          <p style={{ fontSize: '0.87rem', color: '#334155' }}>{job.score_reason}</p>
        </div>
      )}

      {job.resume_tips?.length > 0 && (
        <div className="card" style={{ background: '#fffbeb', padding: '0.75rem 1rem', marginBottom: '0.75rem' }}>
          <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#92400e', marginBottom: '0.25rem' }}>
            📝 Résumé suggestions for this job
          </div>
          <ul style={{ fontSize: '0.87rem', color: '#334155', margin: '0 0 0 1.1rem' }}>
            {job.resume_tips.map((t, i) => <li key={i}>{t}</li>)}
          </ul>
        </div>
      )}

      <div className="card" style={{ padding: '0.75rem 1rem', marginBottom: '0.75rem' }}>
        <SkillChips matched={job.matched_skills} missing={job.missing_skills} />
      </div>

      <AiDoc key={`cl-${job.id}`} jobId={job.id} fetcher={getCoverLetter} label="✉️ Cover letter"
        hint="Writes a cover letter for this job from your résumé and selected skills." />
      <AiDoc key={`rr-${job.id}`} jobId={job.id} fetcher={getResumeReport} label="📄 Résumé update report"
        hint="Compares the full job description with your résumé — paste the report into any AI to update your résumé." />

      {job.description && (
        <div>
          <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#475569', marginBottom: '0.35rem' }}>
            Job Description
          </div>
          <div
            style={{
              fontSize: '0.87rem', color: '#334155',
              maxHeight: 320, overflowY: 'auto',
              whiteSpace: 'pre-wrap', lineHeight: 1.65,
              background: '#f8fafc', padding: '0.75rem',
              borderRadius: 6,
            }}
          >
            {job.description}
          </div>
        </div>
      )}

      {job.url && (
        <div style={{ marginTop: '0.75rem' }}>
          <a href={safeUrl(job.url)} target="_blank" rel="noreferrer" className="btn btn-primary btn-sm">
            🔗 Open Job Listing
          </a>
        </div>
      )}
    </Modal>
  )
}

// ---------------------------------------------------------------------------
// Jobs page
// ---------------------------------------------------------------------------
export default function Jobs() {
  const [jobs, setJobs]             = useState([])
  const [total, setTotal]           = useState(0)
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState('')

  const [search, setSearch]         = useState('')
  const [filterSource, setFilterSource]   = useState('')
  const [filterStatus, setFilterStatus]   = useState('')
  const [filterMinScore, setFilterMinScore] = useState('')
  const [filterRemote, setFilterRemote]     = useState('')
  const [filterRegion, setFilterRegion]     = useState('')
  const [filterApplied, setFilterApplied]   = useState('false')
  const [regions, setRegions]               = useState({ states: [], parks: [] })
  const [picked, setPicked]                 = useState(() => new Set())
  const [applying, setApplying]             = useState(false)
  const [applyMsg, setApplyMsg]             = useState('')
  const [page, setPage]             = useState(1)
  const PAGE_SIZE = 20

  const [selectedJob, setSelectedJob]       = useState(null)
  const [loadingDetail, setLoadingDetail]   = useState(false)

  const [searchLoading, setSearchLoading] = useState(false)
  const [searchMsg, setSearchMsg]         = useState('')
  const [searchError, setSearchError]     = useState('')

  // ---------------------------------------------------------------------------
  const loadJobs = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = {
        q:          search      || undefined,
        source:     filterSource|| undefined,
        status:     filterStatus|| undefined,
        min_score:  filterMinScore || undefined,
        remote:     filterRemote   || undefined,
        region:     filterRegion   || undefined,
        has_application: filterApplied || undefined,
        skip:       (page - 1) * PAGE_SIZE,
        limit:      PAGE_SIZE,
      }
      const data = await getJobs(params)
      setJobs(data.items || data || [])
      setTotal(data.total || (data.items ? data.items.length : (data.length || 0)))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [search, filterSource, filterStatus, filterMinScore, filterRemote, filterRegion, filterApplied, page])

  useEffect(() => { getRegions().then(setRegions).catch(() => {}) }, [])

  // Initial load + 60s refresh
  useEffect(() => {
    loadJobs()
    const interval = setInterval(loadJobs, 60_000)
    return () => clearInterval(interval)
  }, [loadJobs])

  // Reset to page 1 when filters change
  useEffect(() => { setPage(1) }, [search, filterSource, filterStatus, filterMinScore, filterRemote, filterRegion, filterApplied])

  function togglePick(id) {
    setPicked(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function handleApplyPicked() {
    setApplying(true); setApplyMsg(''); setError('')
    try {
      const res = await applyToJobs([...picked])
      setApplyMsg(res.message)
      setPicked(new Set())
      loadJobs()
    } catch (err) { setError(err.message) }
    finally { setApplying(false) }
  }

  // ---------------------------------------------------------------------------
  async function handleJobClick(job) {
    setLoadingDetail(true)
    try {
      const detail = await getJob(job.id)
      setSelectedJob(detail)
    } catch {
      setSelectedJob(job) // fall back to card data
    } finally {
      setLoadingDetail(false)
    }
  }

  // ---------------------------------------------------------------------------
  async function handleSearchNow() {
    setSearchMsg(''); setSearchError('')
    setSearchLoading(true)
    try {
      const res = await triggerSearch()
      setSearchMsg(res?.message || 'Search triggered! New jobs will appear shortly.')
      setTimeout(() => { setSearchMsg(''); loadJobs() }, 5000)
    } catch (err) { setSearchError(err.message) }
    finally { setSearchLoading(false) }
  }

  // ---------------------------------------------------------------------------
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Jobs</h1>
        <button className="btn btn-primary" onClick={handleSearchNow} disabled={searchLoading}>
          {searchLoading ? <><Spinner size="sm" /> Searching…</> : '🔍 Search Now'}
        </button>
      </div>

      <WaitingBanner />

      {searchMsg   && <div className="alert alert-success">✅ {searchMsg}</div>}
      {searchError && <div className="alert alert-error">⚠️ {searchError}</div>}
      {applyMsg    && <div className="alert alert-success">✅ {applyMsg}</div>}

      {/* ── Filters ──────────────────────────────────────── */}
      <div className="filters-bar">
        <div className="form-group">
          <label className="form-label">Search</label>
          <input
            type="text"
            className="form-control"
            placeholder="Title, company, keyword…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="form-group">
          <label className="form-label">Source</label>
          <select className="form-control" value={filterSource} onChange={e => setFilterSource(e.target.value)}>
            <option value="">All Sources</option>
            <option value="linkedin">LinkedIn</option>
            <option value="naukri">Naukri</option>
            <option value="indeed">Indeed</option>
            <option value="remotive">Remotive</option>
            <option value="remoteok">RemoteOK</option>
            <option value="infopark">Infopark</option>
            <option value="technopark">Technopark</option>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Status</label>
          <select className="form-control" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
            <option value="">All Statuses</option>
            <option value="new">Not scored yet</option>
            <option value="scored">Scored</option>
            <option value="skipped">Filtered out</option>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Application</label>
          <select className="form-control" value={filterApplied} onChange={e => setFilterApplied(e.target.value)}>
            <option value="false">Not applied yet</option>
            <option value="true">Already applied / queued</option>
            <option value="">All jobs</option>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Work Type</label>
          <select className="form-control" value={filterRemote} onChange={e => setFilterRemote(e.target.value)}>
            <option value="">Any</option>
            <option value="true">🌍 Remote only</option>
            <option value="false">On-site / hybrid</option>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Location</label>
          <select className="form-control" value={filterRegion} onChange={e => setFilterRegion(e.target.value)}>
            <option value="">Anywhere</option>
            <option value="international">🌍 International (outside India)</option>
            <option value="india">🇮🇳 India</option>
            <optgroup label="Indian state">
              {regions.states.map(s => <option key={s} value={`state:${s}`}>{s}</option>)}
            </optgroup>
            <optgroup label="Tech park">
              {regions.parks.map(p => <option key={p} value={`park:${p}`}>{p}</option>)}
            </optgroup>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Min Score</label>
          <select className="form-control" value={filterMinScore} onChange={e => setFilterMinScore(e.target.value)}>
            <option value="">Any</option>
            <option value="5">5+</option>
            <option value="7">7+</option>
            <option value="8">8+</option>
          </select>
        </div>
        <button className="btn btn-secondary" onClick={loadJobs} style={{ alignSelf: 'flex-end' }}>
          🔄 Refresh
        </button>
      </div>

      {/* ── Content ──────────────────────────────────────── */}
      {loading
        ? <Spinner text="Loading jobs…" />
        : error
          ? <div className="alert alert-error">⚠️ {error}</div>
          : jobs.length === 0
            ? (
              <div className="empty-state">
                <div className="empty-state-icon">🔍</div>
                <p>No jobs found. Try adjusting filters or trigger a new search.</p>
              </div>
            )
            : (
              <>
                <div className="apply-bar">
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={jobs.some(pickable) && jobs.filter(pickable).every(j => picked.has(j.id))}
                      onChange={e => setPicked(prev => {
                        const next = new Set(prev)
                        jobs.filter(pickable).forEach(j => e.target.checked ? next.add(j.id) : next.delete(j.id))
                        return next
                      })}
                    />
                    Select all on this page
                  </label>
                  <button className="btn btn-primary btn-sm" disabled={!picked.size || applying} onClick={handleApplyPicked}>
                    {applying ? <><Spinner size="sm" /> Queuing…</> : `🚀 Apply to ${picked.size} selected`}
                  </button>
                  <span style={{ fontSize: '0.8rem', color: '#64748b' }}>
                    Showing {jobs.length} of {total} jobs. Selected jobs are sent one at a time with gaps
                    (LinkedIn 8–20 min apart, max 15/day; Naukri max 10/day) within your apply hours.
                  </span>
                </div>
                <div className="jobs-grid">
                  {jobs.map(job => {
                    const score = Number(job.score) || 0
                    return (
                      <div
                        key={job.id}
                        className="job-card"
                        onClick={() => handleJobClick(job)}
                        title="Click for details"
                      >
                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start' }}>
                          <input
                            type="checkbox"
                            aria-label={`Select ${job.title} at ${job.company}`}
                            title={pickable(job) ? 'Select to apply' : 'Already has an application'}
                            disabled={!pickable(job)}
                            checked={picked.has(job.id)}
                            onClick={e => e.stopPropagation()}
                            onChange={() => togglePick(job.id)}
                            style={{ marginTop: '0.2rem' }}
                          />
                          <div className="job-card-title">{job.title}</div>
                        </div>
                        <div className="job-card-company">{job.company}</div>
                        {job.location && <div className="job-card-location">📍 {job.location}</div>}
                        <div className="job-card-meta">
                          <SourceBadge source={job.source} />
                          {job.is_remote && <span className="badge badge-info">🌍 Remote</span>}
                          <ScoreBadge score={score} status={job.status} />
                          {job.salary_range && (
                            <span style={{ fontSize: '0.78rem', color: '#475569' }}>💰 {job.salary_range}</span>
                          )}
                          {job.company_rating && (
                            <span style={{ fontSize: '0.78rem', color: '#475569' }}>⭐ {job.company_rating}</span>
                          )}
                        </div>
                        <SkillChips compact matched={job.matched_skills?.slice(0, 6)} missing={job.missing_skills?.slice(0, 4)} />
                        <div style={{ fontSize: '0.78rem', color: '#475569' }}>
                          {job.application_status && <><StatusBadge status={job.application_status} />{' '}</>}
                          {applyVia(job)}
                        </div>
                        <ApplyButtons job={job} onDone={loadJobs} />
                      </div>
                    )
                  })}
                </div>

                {/* Pagination */}
                <div className="pagination">
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                    disabled={page === 1}
                  >
                    ← Prev
                  </button>
                  <span className="pagination-info">Page {page} of {totalPages}</span>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                  >
                    Next →
                  </button>
                </div>
              </>
            )
      }

      {/* ── Detail loading overlay ────────────────────────── */}
      {loadingDetail && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 999,
        }}>
          <Spinner size="lg" text="Loading details…" />
        </div>
      )}

      {/* ── Job Detail Modal ─────────────────────────────── */}
      {selectedJob && (
        <JobDetailModal job={selectedJob} onClose={() => setSelectedJob(null)} onApplied={() => { setSelectedJob(null); loadJobs() }} />
      )}
    </div>
  )
}
