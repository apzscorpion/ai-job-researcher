import React from 'react'

const STATUS_MAP = {
  queued:       { cls: 'badge-info',         label: 'Queued' },
  applying:     { cls: 'badge-info',         label: 'Applying…' },
  skipped:      { cls: 'badge-pending',      label: 'Skipped (duplicate)' },
  draft:        { cls: 'badge-warning',      label: 'Draft — review' },
  applied:      { cls: 'badge-applied',      label: 'Applied' },
  needs_manual: { cls: 'badge-needs_manual', label: 'Needs you' },
  failed:       { cls: 'badge-failed',       label: 'Failed' },
  replied:      { cls: 'badge-replied',      label: 'Replied' },
  pending:      { cls: 'badge-pending',      label: 'Pending' },
  success:      { cls: 'badge-success',      label: 'Success' },
  error:        { cls: 'badge-error',        label: 'Error' },
  warning:      { cls: 'badge-warning',      label: 'Warning' },
  info:         { cls: 'badge-info',         label: 'Info' },
}

// How an application was sent (Application.method)
export const CHANNELS = {
  email:    '📧 Email',
  linkedin: '🔗 LinkedIn',
  naukri:   '🟦 Naukri',
  site:     '🏢 Company site',
  manual:   '✋ Manual',
  pending:  '—',
}

export default function StatusBadge({ status }) {
  const key = (status || '').toLowerCase()
  const { cls, label } = STATUS_MAP[key] || { cls: 'badge-pending', label: status || 'Unknown' }
  return (
    <span className={`badge ${cls}`}>{label}</span>
  )
}
