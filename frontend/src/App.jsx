import React, { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'

import Sidebar from './components/Sidebar'
import StatusHeader from './components/StatusHeader'
import LiveApply from './pages/LiveApply'
import RemoteBrowser from './components/RemoteBrowser'
import LogViewer from './components/LogViewer'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import Profile from './pages/Profile'
import Settings from './pages/Settings'
import Jobs from './pages/Jobs'
import Applications from './pages/Applications'

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------
function getToken() {
  return localStorage.getItem('token')
}

// ---------------------------------------------------------------------------
// PrivateRoute — redirects to /login when unauthenticated
// ---------------------------------------------------------------------------
function PrivateRoute({ children }) {
  const token = getToken()
  const location = useLocation()
  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return children
}

// ---------------------------------------------------------------------------
// AppShell — sidebar + main content for protected pages
// ---------------------------------------------------------------------------
function AppShell({ children, onOpenLogs }) {
  return (
    <div className="app-layout">
      <Sidebar onOpenLogs={onOpenLogs} />
      <main className="main-content">
        <StatusHeader />
        {children}
      </main>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Root redirect
// ---------------------------------------------------------------------------
function RootRedirect() {
  const token = getToken()
  return token ? <Navigate to="/dashboard" replace /> : <Navigate to="/login" replace />
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const [logsOpen, setLogsOpen] = useState(false)

  // Keyboard shortcut listener: press ` or ~ or Ctrl+L to toggle logs
  useEffect(() => {
    function handleKeyDown(e) {
      // Don't trigger if user is typing in a form input or textarea
      const tag = e.target?.tagName?.toLowerCase()
      const isInput = tag === 'input' || tag === 'textarea' || e.target?.isContentEditable

      if ((e.key === '`' || e.key === '~' || (e.ctrlKey && e.key.toLowerCase() === 'l')) && !isInput) {
        e.preventDefault()
        setLogsOpen(prev => !prev)
      } else if (e.key === 'Escape' && logsOpen) {
        setLogsOpen(false)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [logsOpen])

  const openLogs = () => setLogsOpen(true)

  return (
    <BrowserRouter>
      <Routes>
        {/* Public */}
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />

        {/* Full-page live view for "Fill with AI" (opens in its own tab) */}
        <Route path="/live" element={<PrivateRoute><LiveApply /></PrivateRoute>} />
        {/* Job-site sign-in live view (Settings → Job sites → Connect opens it in its own tab) */}
        <Route path="/connect" element={<PrivateRoute><RemoteBrowser /></PrivateRoute>} />

        {/* Root */}
        <Route path="/" element={<RootRedirect />} />

        {/* Protected */}
        <Route
          path="/dashboard"
          element={
            <PrivateRoute>
              <AppShell onOpenLogs={openLogs}><Dashboard /></AppShell>
            </PrivateRoute>
          }
        />
        <Route
          path="/profile"
          element={
            <PrivateRoute>
              <AppShell onOpenLogs={openLogs}><Profile /></AppShell>
            </PrivateRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <PrivateRoute>
              <AppShell onOpenLogs={openLogs}><Settings /></AppShell>
            </PrivateRoute>
          }
        />
        <Route
          path="/jobs"
          element={
            <PrivateRoute>
              <AppShell onOpenLogs={openLogs}><Jobs /></AppShell>
            </PrivateRoute>
          }
        />
        <Route
          path="/applications"
          element={
            <PrivateRoute>
              <AppShell onOpenLogs={openLogs}><Applications /></AppShell>
            </PrivateRoute>
          }
        />

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

      <LogViewer isOpen={logsOpen} onClose={() => setLogsOpen(false)} />
    </BrowserRouter>
  )
}
