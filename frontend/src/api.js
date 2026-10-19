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

export async function revealKey(keyId) {
  return apiFetch(`/settings/keys/${encodeURIComponent(keyId)}/reveal`, { method: 'POST' })
}

export async function testKey(body) {
  return apiFetch('/settings/keys/test', { method: 'POST', body: JSON.stringify(body) })
}

export async function connectJobSite(site) {
  return apiFetch(`/settings/job-sites/${site}/connect`, { method: 'POST' })
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
