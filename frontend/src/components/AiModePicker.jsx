import React, { useState } from 'react'
import { updateSettings } from '../api'
import LocalModelCard from './LocalModelCard'

const MODES = [
  {
    id: 'full_ai', title: 'Full AI (keys)', tag: 'Fastest · most accurate · most quota',
    text: 'Your cloud AI keys do everything: score every job (with résumé tips), write cover letters and ' +
          'emails, answer application forms and run the site agent. Your local model keeps learning from ' +
          'the AI\'s scores in the background.',
  },
  {
    id: 'semi_ai', title: 'Semi AI', tag: 'Recommended · saves most quota',
    text: 'Your trained local model scores jobs instantly, and the local AI on this computer double-checks ' +
          'the borderline ones in the background. Cloud AI keys are used only to write emails and cover ' +
          'letters, answer forms and run the site agent.',
  },
  {
    id: 'local_plus_ai', title: 'Local models + AI key', tag: 'Least quota · slower answers',
    text: 'Like Semi AI, and form answers and résumé reports also try the local AI first (about 1–3 minutes ' +
          'each on this PC), falling back to your keys only if it fails. Keys are used mainly for cover ' +
          'letters, emails and the site agent, which need speed.',
  },
  {
    id: 'local_only', title: 'Only local models', tag: 'Free · private · slow',
    text: 'Nothing is ever sent to a cloud AI — everything runs on this computer. Scoring is instant once ' +
          'your local model is trained, but writing a cover letter or each step of filling a form takes ' +
          'several minutes here, so applying is much slower and some sites may time out.',
  },
]

// One switch for how much work goes to cloud AI keys vs. models running on this computer
export default function AiModePicker({ mode, onChange }) {
  const [saving, setSaving] = useState('')
  const [msg, setMsg]       = useState('')
  const [err, setErr]       = useState('')

  async function pick(id) {
    if (id === mode) return
    setSaving(id); setMsg(''); setErr('')
    try {
      await updateSettings({ ai_mode: id })
      onChange(id)
      setMsg(`Switched to “${MODES.find(m => m.id === id).title}”.`)
    } catch (e) { setErr(e.message) }
    finally { setSaving('') }
  }

  return (
    <div>
      {msg && <div className="alert alert-success">✅ {msg}</div>}
      {err && <div className="alert alert-error">⚠️ {err}</div>}
      <div className="ai-modes" role="radiogroup" aria-label="AI mode">
        {MODES.map(m => (
          <label key={m.id} className={`ai-mode${mode === m.id ? ' ai-mode-on' : ''}`}>
            <input type="radio" name="ai-mode" checked={mode === m.id} onChange={() => pick(m.id)}
              disabled={!!saving} />
            <span className="ai-mode-title">{m.title}{saving === m.id ? ' — saving…' : ''}</span>
            <span className="ai-mode-tag">{m.tag}</span>
            <span className="ai-mode-text">{m.text}</span>
          </label>
        ))}
      </div>
      <LocalModelCard cloudScoring={mode === 'full_ai'} />
    </div>
  )
}
