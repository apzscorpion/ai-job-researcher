import React, { useEffect, useState } from 'react'
import { getLocalModel, trainLocalModel } from '../api'
import Spinner from './Spinner'

// The user's own job scorer, trained on this computer from the jobs the AI already scored
export default function LocalModelCard({ cloudScoring }) {
  const [data, setData]   = useState(null)
  const [busy, setBusy]   = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { getLocalModel().then(setData).catch(err => setError(err.message)) }, [])

  async function retrain() {
    setBusy(true); setError('')
    try { setData(await trainLocalModel()) } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  const m = data?.model
  return (
    <div className="card" style={{ background: '#f8fafc', padding: '0.75rem 1rem', marginBottom: '1rem' }}>
      <div style={{ fontWeight: 700, fontSize: '0.88rem', marginBottom: '0.25rem' }}>🧠 Your local scoring model</div>
      <p className="form-hint" style={{ marginBottom: '0.5rem' }}>
        {cloudScoring
          ? 'Cloud scoring is on: the AI scores every job (uses your quota) and the local model keeps learning from it.'
          : 'Cloud scoring is off: new jobs are scored instantly on this computer by your trained model — no AI quota used.'}
        {' '}It learns from jobs the AI scored and retrains itself after every 10 new ones; until it's trained,
        the AI scores as before.
      </p>
      {m ? (
        <p style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}>
          Trained on <strong>{m.examples}</strong> of your jobs · typical error <strong>±{m.mae}</strong> points
          (just guessing the average: ±{m.baseline_mae})
          {m.agree_at_7 != null && <> · agrees with the AI on apply-or-skip for <strong>{m.agree_at_7}%</strong> of jobs</>}
          {' '}· last trained {m.trained_at}
          {!m.usable && <span style={{ color: '#b91c1c' }}> — not accurate enough yet, so the AI keeps scoring</span>}
        </p>
      ) : data && (
        <p style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}>Not trained yet — needs {data.min_examples} AI-scored jobs.</p>
      )}
      {error && <div className="alert alert-error">⚠️ {error}</div>}
      <button type="button" className="btn btn-secondary btn-sm" onClick={retrain} disabled={busy}>
        {busy ? <><Spinner size="sm" /> Training…</> : '🔁 Retrain now'}
      </button>
    </div>
  )
}
