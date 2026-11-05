import React, { useEffect, useRef, useState } from 'react'
import { getSettings, updateSettings } from '../api'
import Toast from './Toast'

// Auto-apply options that save as soon as they change (no Save button): what gets applied to first,
// the user's district, and a summary of the other limits. Used on the Dashboard and in Settings.
export default function AutoApplyOptions({ onSettings, onSaved, autoApply }) {
  const [s, setS] = useState(null)
  const [places, setPlaces] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const timer = useRef()

  useEffect(() => {
    getSettings().then(d => { setS(d); setPlaces((d.home_places || []).join(', ')) }).catch(e => setErr(e.message))
  }, [])

  // the Dashboard's own Pause / Apply button changed it
  useEffect(() => {
    if (autoApply !== undefined) setS(prev => prev && { ...prev, auto_apply_enabled: autoApply })
  }, [autoApply])

  async function save(patch) {
    setS(prev => ({ ...prev, ...patch }))
    clearTimeout(timer.current)
    setErr('')
    try {
      await updateSettings(patch)
      onSaved?.(patch)
      setMsg('Auto-apply options saved')
      timer.current = setTimeout(() => setMsg(''), 2500)
    } catch (e) {
      setMsg('')
      setErr(`Not saved: ${e.message}`)
      getSettings().then(setS).catch(() => {})  // show what is really stored
    }
  }

  if (!s) return err ? <div className="alert alert-error">⚠️ Couldn't load auto-apply options: {err}</div> : null
  const groups = s.priority_groups || {}
  const order = s.apply_priority || []
  const rows = [...order, ...Object.keys(groups).filter(k => !order.includes(k))]
  const move = (key, d) => {
    const i = order.indexOf(key), j = i + d
    if (j < 0 || j >= order.length) return
    const next = [...order];[next[i], next[j]] = [next[j], next[i]]
    save({ apply_priority: next })
  }
  const savePlaces = () => {
    const list = places.split(',').map(p => p.trim()).filter(Boolean)
    if (list.join(',') !== (s.home_places || []).join(',')) save({ home_places: list })
  }

  return (
    <div className="card">
      <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        ⚡ Auto-apply options
        <span className="form-hint" style={{ marginLeft: 'auto' }}>Changes save automatically</span>
      </div>
      <Toast text={err || msg} error={!!err} />

      <label className="toggle-switch" style={{ marginBottom: '0.5rem' }}>
        <input type="checkbox" checked={!!s.auto_apply_enabled} onChange={e => save({ auto_apply_enabled: e.target.checked })} />
        <span className="toggle-label">Apply automatically</span>
      </label>
      <div className="form-hint" style={{ marginBottom: '0.75rem' }}>
        Up to {s.daily_cap} a day · between {s.apply_start_hour}:00 and {s.apply_end_hour}:00 ({s.timezone}) ·
        jobs scoring {s.score_threshold}+ ·{' '}
        {onSettings ? <a href="/settings" onClick={e => { e.preventDefault(); onSettings() }}>change limits</a> : 'limits are above'}
      </div>

      <div className="form-label">What to apply to first</div>
      <div className="form-hint">Tick the kinds of jobs you want and order them; ticked ones go first, top to bottom.</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', margin: '0.5rem 0' }}>
        {rows.map(key => {
          const i = order.indexOf(key)
          return (
            <div key={key} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <input type="checkbox" id={`prio-${key}`} checked={i >= 0}
                onChange={e => save({ apply_priority: e.target.checked ? [...order, key] : order.filter(k => k !== key) })} />
              <label htmlFor={`prio-${key}`} style={{ flex: 1 }}>{i >= 0 ? `${i + 1}. ` : ''}{groups[key]}</label>
              <button type="button" className="btn btn-sm" disabled={i <= 0} onClick={() => move(key, -1)}
                aria-label={`Move ${groups[key]} up`}>↑</button>
              <button type="button" className="btn btn-sm" disabled={i < 0 || i === order.length - 1}
                onClick={() => move(key, 1)} aria-label={`Move ${groups[key]} down`}>↓</button>
            </div>
          )
        })}
      </div>

      <label className="form-label" htmlFor="home-places">My district and towns in it</label>
      <input id="home-places" type="text" className="form-control" value={places}
        onChange={e => setPlaces(e.target.value)} onBlur={savePlaces}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); savePlaces() } }}
        placeholder={s.home_places_auto ? `${s.home_places_auto} (from your profile)` : 'e.g. Pathanamthitta, Thiruvalla, Adoor'} />
      <span className="form-hint">
        {places.trim() ? 'A job counts as "in my district" when its location names one of these.'
          : s.home_places_auto ? `Leave empty to use ${s.home_places_auto}, from your profile location.`
            : 'Add your location to your profile, or type your district here.'}
      </span>

      <label className="toggle-switch" style={{ marginTop: '0.5rem' }}>
        <input type="checkbox" checked={!!s.priority_only} onChange={e => save({ priority_only: e.target.checked })} />
        <span className="toggle-label">Apply only to the ticked kinds (skip everything else)</span>
      </label>
    </div>
  )
}
