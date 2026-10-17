import React from 'react'

export default function Spinner({ size = '', text = '' }) {
  const cls = ['spinner', size === 'sm' ? 'spinner-sm' : size === 'lg' ? 'spinner-lg' : ''].join(' ').trim()
  if (text) {
    return (
      <div className="loading-container">
        <span className={cls} />
        <span>{text}</span>
      </div>
    )
  }
  return <span className={cls} />
}
