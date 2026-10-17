import React from 'react'

// Green = the job asks for it and it is one of your selected skills; red = the job asks for it, you don't list it
export default function SkillChips({ matched = [], missing = [], compact = false }) {
  if (!matched.length && !missing.length) return null
  const total = matched.length + missing.length
  return (
    <div style={{ marginTop: compact ? '0.4rem' : 0 }}>
      {!compact && (
        <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#475569', marginBottom: '0.3rem' }}>
          Skills match: {matched.length}/{total}
        </div>
      )}
      <div className="skills-list" style={{ marginTop: 0 }}>
        {matched.map(s => <span key={`m-${s}`} className="skill-pill skill-match" title="You have this skill">✓ {s}</span>)}
        {missing.map(s => <span key={`x-${s}`} className="skill-pill skill-miss" title="Job asks for it; not in your selected skills">✗ {s}</span>)}
      </div>
    </div>
  )
}
