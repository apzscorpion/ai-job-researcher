import React, { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { getMe } from '../api'

const NAV_LINKS = [
  { to: '/dashboard',    label: 'Dashboard',    icon: '🏠' },
  { to: '/jobs',         label: 'Jobs',         icon: '🔍' },
  { to: '/applications', label: 'Applications', icon: '📋' },
  { to: '/profile',      label: 'Profile',      icon: '👤' },
  { to: '/settings',     label: 'Settings',     icon: '⚙️' },
]

export default function Sidebar({ onOpenLogs }) {
  const navigate = useNavigate()
  const [userEmail, setUserEmail] = useState('')

  useEffect(() => {
    getMe()
      .then(data => setUserEmail(data.email || ''))
      .catch(() => {})
  }, [])

  function handleLogout() {
    localStorage.removeItem('token')
    navigate('/login')
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-brand-icon">🤖</span>
        AI Job Researcher
      </div>

      <ul className="sidebar-nav">
        {NAV_LINKS.map(({ to, label, icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="nav-icon">{icon}</span>
              {label}
            </NavLink>
          </li>
        ))}
      </ul>

      <div className="sidebar-footer">
        {userEmail && (
          <div className="sidebar-user-email" title={userEmail}>
            {userEmail}
          </div>
        )}
        <button
          onClick={onOpenLogs}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '0.4rem',
            width: '100%',
            padding: '0.45rem 0.75rem',
            marginBottom: '0.5rem',
            borderRadius: '6px',
            backgroundColor: '#1e293b',
            color: '#38bdf8',
            border: '1px solid #334155',
            fontSize: '0.8rem',
            cursor: 'pointer',
            fontWeight: 500,
          }}
          title="Toggle system execution logs (Shortcut: ~)"
        >
          <span>📜</span> System Logs <span style={{ opacity: 0.6, fontSize: '0.7rem' }}>~</span>
        </button>
        <button className="btn-logout" onClick={handleLogout}>
          🚪 Logout
        </button>
      </div>
    </aside>
  )
}
