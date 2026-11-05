import React from 'react'

// A save result pinned to the corner of the screen, so it's seen even when the form's own message is scrolled away
export default function Toast({ text, error }) {
  if (!text) return null
  return (
    <div className={`alert ${error ? 'alert-error' : 'alert-success'}`} role={error ? 'alert' : 'status'}
      style={{ position: 'fixed', right: 16, bottom: 16, left: 'auto', zIndex: 1000, maxWidth: 'min(420px, calc(100vw - 32px))',
               boxShadow: '0 4px 16px rgba(0,0,0,0.25)', margin: 0 }}>
      {error ? '⚠️ ' : '✅ '}{text}
    </div>
  )
}
