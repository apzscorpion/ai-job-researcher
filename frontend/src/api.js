// ---------------------------------------------------------------------------
// api.js — Centralised fetch wrapper + all API endpoint functions
// ---------------------------------------------------------------------------

// Same-origin /api in development (Vite proxy); the backend's https URL when hosted (VITE_API_BASE)
const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '') + '/api'

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------
export async function apiFetch(path, options = {}) {
  const token = localStorage.getItem('token')

  const headers = {
    ...(options.headers || {}),
  }

  // Don't set Content-Type for FormData — browser sets it with boundary
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  // ngrok's free tier shows a warning page instead of the API unless this header is sent
  headers['ngrok-skip-browser-warning'] = '1'

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })

  // Expired or invalid token: drop it and go back to login
  if (response.status === 401) {
    localStorage.removeItem('token')
    window.location.href = '/login'
    throw new Error('Session expired — please log in again.')
  }

  if (!response.ok) {
    let errMsg = `HTTP ${response.status}`
    try {
      const errData = await response.json()
      errMsg = errData.detail || errData.message || errData.error || errMsg
    } catch {
      // ignore json parse error; keep the HTTP status string
    }
    throw new Error(errMsg)
  }

  // Return null for 204 No Content
  if (response.status === 204) return null
  if (options.raw) return response.blob()  // images and files

  return response.json()
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
export async function register({ email, password, full_name }) {
  return apiFetch('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password, full_name }),
  })
}

export async function login({ email, password }) {
  // OAuth2 form body expected by FastAPI
  const form = new URLSearchParams()
  form.append('username', email)
  form.append('password', password)

  const response = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
  })

  if (!response.ok) {
    let errMsg = `HTTP ${response.status}`
    try {
      const errData = await response.json()
      errMsg = errData.detail || errData.message || errMsg
    } catch { /* ignore */ }
    throw new Error(errMsg)
  }

  return response.json() // { access_token, token_type }
}

export async function getMe() {
  return apiFetch('/auth/me')
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------
export async function getProfile() {
  return apiFetch('/profile')
}

export async function updateProfile(data) {
  return apiFetch('/profile', {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function uploadResume(file) {
  const formData = new FormData()
  formData.append('file', file)
  return apiFetch('/profile/resume', {
    method: 'POST',
    body: formData,
  })
}

export async function deleteResume() {
  return apiFetch('/profile/resume', { method: 'DELETE' })
}

export async function deleteAllData() {
  return apiFetch('/profile/all', { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
export async function getSettings() {
  return apiFetch('/settings')
}

export async function updateSettings(data) {
  return apiFetch('/settings', {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function testEmail() {
  return apiFetch('/settings/test-email', { method: 'POST' })
}

export async function getLocalModel() {
  return apiFetch('/settings/local-model')
}

export async function trainLocalModel() {
  return apiFetch('/settings/local-model/train', { method: 'POST' })
}

export async function revealKey(keyId) {
  return apiFetch(`/settings/keys/${encodeURIComponent(keyId)}/reveal`, { method: 'POST' })
}

export async function testKey(body) {
  return apiFetch('/settings/keys/test', { method: 'POST', body: JSON.stringify(body) })
}

export async function connectJobSite(site, remote = false) {
  return apiFetch(`/settings/job-sites/${site}/connect${remote ? '?remote=true' : ''}`, { method: 'POST' })
}

// "Fill with AI": the agent fills an application in a live window you can step into
export const assistApi = {
  start:       (appId, review) => apiFetch(`/applications/${appId}/assist?review=${review ? 'true' : 'false'}`, { method: 'POST' }),
  fromLink:    body  => apiFetch('/applications/from-link', { method: 'POST', body: JSON.stringify(body) }),
  status:      ()    => apiFetch('/applications/assist/status'),
  resume:      ()    => apiFetch('/applications/assist/continue', { method: 'POST' }),
  close:       ()    => apiFetch('/applications/assist/close', { method: 'POST' }),
  pause:       ()    => apiFetch('/applications/assist/pause', { method: 'POST' }),
  unpause:     ()    => apiFetch('/applications/assist/unpause', { method: 'POST' }),
  markApplied: appId => apiFetch(`/applications/${appId}/mark-applied`, { method: 'POST' }),
  reply:       text  => apiFetch('/applications/assist/reply', { method: 'POST', body: JSON.stringify({ text }) }),
  upload:      file  => {
    const form = new FormData()
    form.append('file', file)
    return apiFetch('/applications/assist/upload', { method: 'POST', body: form })
  },
}

// Sign-in window that runs on the host and is shown inside the website
export const remoteSignIn = {
  status: () => apiFetch('/settings/job-sites/remote/status'),
  frame:  () => apiFetch('/settings/job-sites/remote/frame', { raw: true }),
  input:  body => apiFetch('/settings/job-sites/remote/input', { method: 'POST', body: JSON.stringify(body) }),
  close:  () => apiFetch('/settings/job-sites/remote/close', { method: 'POST', keepalive: true }),  // keepalive: still sent as the tab closes
}

// Reads every saved browser profile: slow-ish (a few seconds)
export async function getJobSites() {
  return apiFetch('/settings/job-sites')
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------
export async function getJobs(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v !== '' && v !== undefined && v !== null))
  ).toString()
  return apiFetch(`/jobs${qs ? `?${qs}` : ''}`)
}

export async function getJob(jobId) {
  return apiFetch(`/jobs/${jobId}`)
}

export async function getRegions() {
  return apiFetch('/jobs/regions')
}

export async function applyToJobs(jobIds) {
  return apiFetch('/jobs/apply', { method: 'POST', body: JSON.stringify({ job_ids: jobIds }) })
}

export async function applyNow(jobId, via) {
  return apiFetch(`/jobs/${jobId}/apply-now`, { method: 'POST', body: JSON.stringify({ via }) })
}

export async function retryAllApplications() {
  return apiFetch('/applications/retry-all', { method: 'POST' })
}

export async function cancelApplication(appId) {
  return apiFetch(`/applications/${appId}`, { method: 'DELETE' })
}

export async function triggerSearch() {
  return apiFetch('/jobs/search', { method: 'POST' })
}

export async function getResumeReport(jobId) {
  return apiFetch(`/jobs/${jobId}/resume-report`, { method: 'POST' })
}

export async function getCoverLetter(jobId) {
  return apiFetch(`/jobs/${jobId}/cover-letter`, { method: 'POST' })
}

export async function deleteJob(jobId) {
  return apiFetch(`/jobs/${jobId}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// Applications
// ---------------------------------------------------------------------------
export async function getApplications(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v !== '' && v !== undefined && v !== null))
  ).toString()
  return apiFetch(`/applications${qs ? `?${qs}` : ''}`)
}

export async function getApplication(appId) {
  return apiFetch(`/applications/${appId}`)
}

export async function updateApplicationStatus(appId, status) {
  return apiFetch(`/applications/${appId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export async function sendApplicationEmail(appId, { email_body, subject } = {}) {
  return apiFetch(`/applications/${appId}/send`, {
    method: 'POST',
    body: JSON.stringify({ email_body, subject }),
  })
}

export async function getStatsToday() {
  return apiFetch('/applications/stats/today')
}

// ---------------------------------------------------------------------------
// Logs & Diagnostics
// ---------------------------------------------------------------------------
export async function getLogs() {
  return apiFetch('/logs')
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------
export async function getNotifications() {
  return apiFetch('/notifications')
}

export async function markNotificationsSeen() {
  return apiFetch('/notifications/seen', { method: 'POST' })
}

export async function getNeedsYouReport() {
  return apiFetch('/applications/report')
}

export async function retryReason(key) {
  return apiFetch(`/applications/report/${encodeURIComponent(key)}/retry`, { method: 'POST' })
}
