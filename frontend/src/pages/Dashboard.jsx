import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getStatsToday, getApplications, getJobs, triggerSearch, getSettings, updateSettings,
  getNotifications, markNotificationsSeen,
} from '../api'
import Spinner from '../components/Spinner'
import StatusBadge, { CHANNELS } from '../components/StatusBadge'

function formatTime(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ts
  }
}

function formatDate(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleDateString()
  } catch {
    return ts
  }
}

// "3 Email · 2 LinkedIn" from {email: 3, linkedin: 2}
function byChannel(counts) {
  return Object.entries(counts || {}).map(([m, n]) => `${n} ${(CHANNELS[m] || m).replace(/^\S+\s/, '')}`).join(' · ')
}

function Check({ ok, warn, children }) {
  return (
    <li style={{ display: 'flex', gap: '0.5rem', fontSize: '0.88rem', padding: '0.2rem 0' }}>
      <span aria-hidden="true">{ok ? '✅' : warn ? '⚠️' : '⛔'}</span><span>{children}</span>
    </li>
  )
}

// What auto-apply can do right now, and why not when it can't
function AutoApplyStatus({ r, onSettings }) {
  if (!r) return null
  const paused = r.paused || {}
  const [start, end] = r.apply_hours || []
  return (
    <div className="card">
      <div className="card-title">
        ⚡ Auto-apply status
        <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={onSettings}>Settings</button>
      </div>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        <Check ok={r.auto_apply}>Auto-apply is <strong>{r.auto_apply ? 'on' : 'off'}</strong> — applies {start}:00–{end}:00 ({r.timezone}), one job at a time.</Check>
        <Check ok={r.email_ready && r.auto_send_email} warn={r.email_ready}>
          Recruiter emails: {r.email_ready
            ? (r.auto_send_email ? 'written by AI and sent from your mailbox with your résumé' : 'written by AI and saved as drafts for you to send')
            : 'written by AI as drafts — set up SMTP in Settings to send them automatically'}
        </Check>
        {['linkedin', 'naukri'].map(p => (
          <Check key={p} ok={!paused[p]}>
            {p === 'linkedin' ? 'LinkedIn Easy Apply' : 'Naukri'}: {paused[p]
              ? <strong>paused — {paused[p]}</strong>
              : `up to ${r.platform_daily_cap?.[p]} a day, 8–20 min apart (pauses itself on login walls or captchas)`}
          </Check>
        ))}
        <Check ok>Company sites (Greenhouse, Lever, Ashby, Workable and plain forms): visited, filled, résumé + AI cover letter uploaded, submitted only when every required question is answered.</Check>
        <Check ok={r.ai_providers > 0} warn>
          AI: {r.ai_providers ? `${r.ai_providers} cloud key(s), local model as backup` : 'local model only (slow) — add keys in Settings'}
        </Check>
      </ul>
      <details style={{ marginTop: '0.6rem', fontSize: '0.86rem', color: '#475569' }}>
        <summary style={{ cursor: 'pointer', fontWeight: 600 }}>How auto-apply works</summary>
        <ol style={{ margin: '0.5rem 0 0 1.2rem', lineHeight: 1.7 }}>
          <li>Search uses only your résumé: the roles in your work history, filtered by the skills you selected on Profile.</li>
          <li>Each job is scored against your résumé; jobs at or above your score threshold are queued (remote first).</li>
          <li>Every 5 minutes one queued job is sent: by email if the posting lists a recruiter address, else LinkedIn / Naukri, else the company's own site.</li>
          <li>Anything it can't finish safely (unknown questions, captchas, sites that need an account) lands in <em>Waiting for you</em> with the reason and a link.</li>
          <li>The Applications page shows where each one went, the cover letter or email used, and the skills match.</li>
        </ol>
      </details>
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()

  const [stats, setStats]               = useState(null)
  const [statsLoading, setStatsLoading] = useState(true)
  const [statsError, setStatsError]     = useState('')

  const [activityLog, setActivityLog]         = useState([])
  const [activityLoading, setActivityLoading] = useState(true)

  const [pendingJobs, setPendingJobs]   = useState([])
  const [jobsTotal, setJobsTotal]       = useState(0)

  const [settings, setSettings]         = useState(null)
  const [autoApply, setAutoApply]       = useState(false)
  const [autoApplySaving, setAutoApplySaving] = useState(false)

  const [notifications, setNotifications] = useState([])

  const [searchLoading, setSearchLoading] = useState(false)
  const [searchMsg, setSearchMsg]         = useState('')
  const [searchError, setSearchError]     = useState('')

  // ---------------------------------------------------------------------------
  // Load today's stats
  // ---------------------------------------------------------------------------
  const loadStats = useCallback(async () => {
    setStatsLoading(true)
    setStatsError('')
    try {
      const data = await getStatsToday()
      setStats(data)
    } catch (err) {
      setStatsError(err.message)
    } finally {
      setStatsLoading(false)
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Load activity log (recent applications)
  // ---------------------------------------------------------------------------
  const loadActivity = useCallback(async () => {
    setActivityLoading(true)
    try {
      const data = await getApplications({ limit: 20, sort: 'desc' })
      setActivityLog(data.items || data || [])
    } catch {
      // silently fail for polling
    } finally {
      setActivityLoading(false)
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Load pending jobs (scored, not yet applied)
  // ---------------------------------------------------------------------------
  const loadPendingJobs = useCallback(async () => {
    try {
      const data = await getJobs({ status: 'scored', limit: 5 })
      setPendingJobs(data.items || data || [])
      const all = await getJobs({ limit: 1 })
      setJobsTotal(all.total || 0)
    } catch { /* ignore */ }
  }, [])

  // ---------------------------------------------------------------------------
  // Load settings for auto_apply_enabled
  // ---------------------------------------------------------------------------
  const loadSettings = useCallback(async () => {
    try {
      const data = await getSettings()
      setSettings(data)
      setAutoApply(data.auto_apply_enabled ?? false)
    } catch { /* ignore */ }
  }, [])

  const loadNotifications = useCallback(async () => {
    try {
      const data = await getNotifications()
      setNotifications(data.items || [])
    } catch { /* ignore */ }
  }, [])

  async function handleDismissNotifications() {
    try {
      await markNotificationsSeen()
      setNotifications([])
    } catch { /* ignore */ }
  }

  // ---------------------------------------------------------------------------
  // Initial load + polling every 30s
  // ---------------------------------------------------------------------------
  useEffect(() => {
    loadStats()
    loadActivity()
    loadPendingJobs()
    loadSettings()
    loadNotifications()

    const interval = setInterval(() => {
      loadStats()
      loadActivity()
      loadNotifications()
    }, 30_000)

    return () => clearInterval(interval)
  }, [loadStats, loadActivity, loadPendingJobs, loadSettings, loadNotifications])

  // ---------------------------------------------------------------------------
  // Trigger search
  // ---------------------------------------------------------------------------
  async function handleSearch() {
    setSearchLoading(true)
    setSearchMsg('')
    setSearchError('')
    try {
      const res = await triggerSearch()
      setSearchMsg(res?.message || 'Search started — remote jobs are searched first. New matches appear in a few minutes.')
      setTimeout(() => {
        loadStats()
        loadPendingJobs()
      }, 2000)
    } catch (err) {
      setSearchError(err.message || 'Failed to trigger search.')
    } finally {
      setSearchLoading(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Toggle auto-apply
  // ---------------------------------------------------------------------------
  async function handleAutoApplyToggle() {
    const newVal = !autoApply
    setAutoApplySaving(true)
    setSearchMsg('')
    setSearchError('')
    try {
      const res = await updateSettings({ auto_apply_enabled: newVal })
      setAutoApply(newVal)
      setSearchMsg(newVal
        ? `Auto-apply on — ${res?.queued ?? 0} matching job(s) queued now; new matches are queued as they're scored. Applications go out one at a time within your apply hours and daily cap.`
        : 'Auto-apply paused — queued jobs stay queued until you turn it back on.')
      loadStats()
    } catch (err) { setSearchError(err.message) }
    finally {
      setAutoApplySaving(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Dashboard</h1>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <button
            className="btn btn-primary"
            onClick={handleSearch}
            disabled={searchLoading}
          >
            {searchLoading ? <><Spinner size="sm" /> Searching…</> : '🔍 Search Now'}
          </button>
          <button
            className={`btn ${autoApply ? 'btn-warning' : 'btn-success'}`}
            onClick={handleAutoApplyToggle}
            disabled={autoApplySaving}
          >
            {autoApplySaving
              ? <Spinner size="sm" />
              : autoApply
                ? '⏸ Pause Auto-Apply'
                : '⚡ Apply Automatically'
            }
          </button>
        </div>
      </div>

      {searchMsg  && <div className="alert alert-success">✅ {searchMsg}</div>}
      {searchError && <div className="alert alert-error">⚠️ {searchError}</div>}

      {/* ── Stats ─────────────────────────────────────────── */}
      {statsLoading
        ? <Spinner text="Loading stats…" />
        : statsError
          ? <div className="alert alert-error">⚠️ {statsError}</div>
          : stats && (
            <>
              <div className="card-grid">
                <div className="stat-card">
                  <span className="stat-label">Sent Today</span>
                  <span className="stat-value">{stats.sent_today ?? 0}</span>
                  <span className="stat-sub">{byChannel(stats.sent_today_by_channel) || `attempts ${stats.applied_today ?? 0} of ${stats.daily_cap ?? '—'} daily cap`}</span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Sent All Time</span>
                  <span className="stat-value">{stats.sent_total ?? 0}</span>
                  <span className="stat-sub">{byChannel(stats.sent_total_by_channel) || 'none yet'}</span>
                </div>
                <div className="stat-card" style={{ cursor: 'pointer' }} onClick={() => navigate('/applications')}>
                  <span className="stat-label">Waiting for You</span>
                  <span className="stat-value">{(stats.needs_you_count ?? 0) + (stats.draft_count ?? 0)}</span>
                  <span className="stat-sub">{stats.needs_you_count ?? 0} to finish yourself · {stats.draft_count ?? 0} email drafts</span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Queued</span>
                  <span className="stat-value">{stats.queued_count ?? 0}</span>
                  <span className="stat-sub">{jobsTotal} jobs found · {stats.replied_count ?? 0} replies · {stats.failed_count ?? 0} failed</span>
                </div>
              </div>
              <AutoApplyStatus r={stats.readiness} onSettings={() => navigate('/settings')} />
            </>
          )
      }

      {/* ── Notifications ────────────────────────────────── */}
      {notifications.length > 0 && (
        <div className="card">
          <div className="card-title">
            🔔 Needs your attention ({notifications.length})
            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }}
              onClick={handleDismissNotifications}>
              Dismiss all
            </button>
          </div>
          <ul className="activity-log">
            {notifications.map(n => (
              <li key={n.id} className="activity-item" style={{ alignItems: 'flex-start' }}>
                <span className="activity-time">{formatDate(n.ts)}</span>
                <span className="activity-text">
                  {n.event === 'resume_update_suggested' ? (
                    <>
                      📝 <strong>Update your résumé</strong> for <strong>{n.title}</strong> @ {n.company}
                      {n.score != null && <> (score {Number(n.score).toFixed(1)})</>}
                      <ul style={{ margin: '0.3rem 0 0 1.1rem' }}>
                        {(n.tips || []).map((t, i) => <li key={i}>{t}</li>)}
                      </ul>
                    </>
                  ) : (
                    <>
                      {n.event === 'email_draft_ready' ? '✉️' : n.event === 'account_attention' ? '⚠️' : '👉'} <strong>{n.title}</strong> @ {n.company} — {n.message}
                      {n.event === 'email_draft_ready' && (
                        <> <a href="/applications" onClick={e => { e.preventDefault(); navigate('/applications') }}>Open Applications</a></>
                      )}
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Top matches ──────────────────────────────────── */}
      <div className="card">
        <div className="card-title">
          ⭐ Top Matches
          <button
            className="btn btn-secondary btn-sm"
            style={{ marginLeft: 'auto' }}
            onClick={() => navigate('/jobs')}
          >
            View All
          </button>
        </div>
        {pendingJobs.length === 0
          ? <p style={{ color: '#94a3b8', fontSize: '0.88rem' }}>No scored jobs yet. Upload your résumé on Profile, then click Search Now.</p>
          : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.87rem' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.6rem', color: '#475569', fontWeight: 700 }}>Title</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.6rem', color: '#475569', fontWeight: 700 }}>Company</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.6rem', color: '#475569', fontWeight: 700 }}>Score</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.6rem', color: '#475569', fontWeight: 700 }}>Source</th>
                </tr>
              </thead>
              <tbody>
                {pendingJobs.map(job => {
                  const score = job.score ?? 0
                  const scoreCls = score >= 7 ? 'score-high' : score >= 5 ? 'score-mid' : 'score-low'
                  return (
                    <tr key={job.id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                      <td style={{ padding: '0.5rem 0.6rem', fontWeight: 600 }}>
                        {job.title}{job.is_remote && <span className="badge badge-info" style={{ marginLeft: 6 }}>🌍 Remote</span>}
                      </td>
                      <td style={{ padding: '0.5rem 0.6rem', color: '#475569' }}>{job.company}</td>
                      <td style={{ padding: '0.5rem 0.6rem' }}>
                        <span className={scoreCls}>{score.toFixed ? score.toFixed(1) : score}</span>
                      </td>
                      <td style={{ padding: '0.5rem 0.6rem' }}>
                        <span className={`badge badge-source-${job.source}`}>{job.source}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )
        }
      </div>

      {/* ── Activity Log ──────────────────────────────────── */}
      <div className="card">
        <div className="card-title">📝 Recent Activity</div>
        {activityLoading
          ? <Spinner size="sm" text="Loading…" />
          : activityLog.length === 0
            ? <p style={{ color: '#94a3b8', fontSize: '0.88rem' }}>No activity yet.</p>
            : (
              <ul className="activity-log">
                {activityLog.map((app, idx) => (
                  <li key={app.id || idx} className="activity-item">
                    <span className="activity-time">
                      {formatTime(app.applied_at || app.created_at)}
                    </span>
                    <span className="activity-text">
                      <strong>{app.job?.title || 'Unknown Job'}</strong>
                      {app.job?.company ? ` @ ${app.job.company}` : ''}
                      {' '}· {CHANNELS[app.method] || app.method} <StatusBadge status={app.status} />
                      {app.sent_to && <span style={{ color: '#64748b' }}> → {app.sent_to}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )
        }
      </div>
    </div>
  )
}
