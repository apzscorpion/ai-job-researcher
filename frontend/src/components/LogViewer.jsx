import React, { useState, useEffect, useRef } from 'react'
import { getLogs } from '../api'

export default function LogViewer({ isOpen, onClose }) {
  const [logs, setLogs] = useState([])
  const [filter, setFilter] = useState('ALL')
  const [search, setSearch] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const bottomRef = useRef(null)

  useEffect(() => {
    if (!isOpen) return

    let isMounted = true
    async function fetchLogs() {
      try {
        const data = await getLogs()
        if (isMounted && data && Array.isArray(data.logs)) {
          setLogs(data.logs)
        }
      } catch {
        /* ignore polling errors */
      }
    }

    fetchLogs()
    const interval = setInterval(fetchLogs, 2000)
    return () => {
      isMounted = false
      clearInterval(interval)
    }
  }, [isOpen])

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, autoScroll])

  if (!isOpen) return null

  const filteredLogs = logs.filter(log => {
    if (filter !== 'ALL' && log.level !== filter) return false
    if (search && !log.message?.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  const getLevelColor = (level) => {
    switch (level) {
      case 'ERROR': return '#ef4444'
      case 'WARNING': return '#f59e0b'
      case 'INFO': return '#38bdf8'
      default: return '#94a3b8'
    }
  }

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      backgroundColor: 'rgba(15, 23, 42, 0.75)',
      backdropFilter: 'blur(4px)',
      zIndex: 9999,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '1.5rem',
    }}>
      <div style={{
        backgroundColor: '#0f172a',
        color: '#f8fafc',
        width: '90vw',
        maxWidth: '1000px',
        height: '80vh',
        borderRadius: '12px',
        boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
        display: 'flex',
        flexDirection: 'column',
        border: '1px solid #334155',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      }}>
        {/* Header */}
        <div style={{
          padding: '0.75rem 1.25rem',
          borderBottom: '1px solid #1e293b',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: '#1e293b',
          borderTopLeftRadius: '12px',
          borderTopRightRadius: '12px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <span style={{ fontSize: '1.1rem' }}>📜</span>
            <span style={{ fontWeight: 600, fontSize: '0.95rem', color: '#e2e8f0' }}>System Execution Logs</span>
            <span style={{
              fontSize: '0.75rem',
              backgroundColor: '#334155',
              padding: '2px 8px',
              borderRadius: '9999px',
              color: '#94a3b8',
            }}>Press `~` or Esc to toggle</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button
              onClick={() => setAutoScroll(!autoScroll)}
              style={{
                fontSize: '0.75rem',
                padding: '4px 10px',
                borderRadius: '4px',
                backgroundColor: autoScroll ? '#0284c7' : '#334155',
                color: '#fff',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              {autoScroll ? '⬇ Auto-Scroll ON' : '⏸ Auto-Scroll OFF'}
            </button>
            <button
              onClick={() => setLogs([])}
              style={{
                fontSize: '0.75rem',
                padding: '4px 10px',
                borderRadius: '4px',
                backgroundColor: '#334155',
                color: '#cbd5e1',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              Clear
            </button>
            <button
              onClick={onClose}
              style={{
                backgroundColor: 'transparent',
                border: 'none',
                color: '#94a3b8',
                fontSize: '1.25rem',
                cursor: 'pointer',
                lineHeight: 1,
              }}
            >
              ✕
            </button>
          </div>
        </div>

        {/* Filter bar */}
        <div style={{
          padding: '0.5rem 1.25rem',
          display: 'flex',
          gap: '0.75rem',
          backgroundColor: '#0f172a',
          borderBottom: '1px solid #1e293b',
          alignItems: 'center',
        }}>
          <input
            type="text"
            placeholder="Search logs..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              flex: 1,
              backgroundColor: '#1e293b',
              border: '1px solid #334155',
              borderRadius: '4px',
              padding: '4px 8px',
              color: '#f8fafc',
              fontSize: '0.8rem',
            }}
          />
          <div style={{ display: 'flex', gap: '0.35rem' }}>
            {['ALL', 'INFO', 'WARNING', 'ERROR'].map(lvl => (
              <button
                key={lvl}
                onClick={() => setFilter(lvl)}
                style={{
                  padding: '3px 8px',
                  fontSize: '0.72rem',
                  borderRadius: '4px',
                  border: 'none',
                  cursor: 'pointer',
                  backgroundColor: filter === lvl ? '#38bdf8' : '#1e293b',
                  color: filter === lvl ? '#0f172a' : '#94a3b8',
                  fontWeight: filter === lvl ? 600 : 400,
                }}
              >
                {lvl}
              </button>
            ))}
          </div>
        </div>

        {/* Log rows */}
        <div style={{
          flex: 1,
          overflowY: 'auto',
          padding: '0.75rem 1.25rem',
          fontSize: '0.8rem',
          lineHeight: '1.5',
        }}>
          {filteredLogs.length === 0 ? (
            <div style={{ color: '#64748b', textAlign: 'center', marginTop: '2rem' }}>
              No log entries recorded yet.
            </div>
          ) : (
            filteredLogs.map((log, idx) => (
              <div key={idx} style={{
                display: 'flex',
                gap: '0.75rem',
                padding: '2px 0',
                borderBottom: '1px solid #1e293b15',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
              }}>
                <span style={{ color: '#64748b', flexShrink: 0 }}>[{log.time}]</span>
                <span style={{
                  color: getLevelColor(log.level),
                  fontWeight: 600,
                  flexShrink: 0,
                  width: '60px',
                }}>
                  {log.level}
                </span>
                <span style={{ color: '#f1f5f9', flex: 1 }}>{log.message}</span>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}

