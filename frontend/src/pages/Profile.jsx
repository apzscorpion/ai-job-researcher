import React, { useState, useEffect, useRef } from 'react'
import {
  getProfile, updateProfile,
  uploadResume, deleteResume, deleteAllData,
} from '../api'
import Spinner from '../components/Spinner'
import Modal from '../components/Modal'

export default function Profile() {
  const [profile, setProfile]       = useState(null)
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState('')
  const [saveMsg, setSaveMsg]       = useState('')
  const [saveError, setSaveError]   = useState('')
  const [saving, setSaving]         = useState(false)

  // Editable fields
  const [fullName, setFullName]     = useState('')
  const [phone, setPhone]           = useState('')
  const [location, setLocation]     = useState('')
  const [linkedinUrl, setLinkedinUrl] = useState('')
  const [skills, setSkills]         = useState([])
  const [applySkills, setApplySkills] = useState([])  // lower-cased skills used for search, matching and applying
  const [newSkill, setNewSkill]     = useState('')
  const [experience, setExperience] = useState([])
  const [education, setEducation]   = useState([])
  const [totalYears, setTotalYears] = useState('')

  // Resume upload
  const fileRef                     = useRef()
  const [uploading, setUploading]   = useState(false)
  const [uploadMsg, setUploadMsg]   = useState('')
  const [uploadError, setUploadError] = useState('')

  // Delete resume
  const [deletingResume, setDeletingResume] = useState(false)

  // Confirmation modal for delete all data
  const [showDeleteModal, setShowDeleteModal] = useState(false)
  const [deletingData, setDeletingData]       = useState(false)
  const [deleteError, setDeleteError]         = useState('')

  // Copy parsed résumé data into the editable fields
  function fillParsed(data) {
    const all = data.skills || data.parsed_skills || []
    setSkills(all)
    setApplySkills((data.apply_skills || all).map(s => s.toLowerCase()))
    setExperience((data.experience || data.parsed_experience || []).map(e => ({
      title: e.title || e.role || '', company: e.company || '', duration: e.duration || '',
    })))
    setEducation((data.education || data.parsed_education || []).map(e => ({
      degree: e.degree || e.qualification || '', institution: e.institution || '', year: e.year || '',
    })))
    setTotalYears(typeof data.total_years === 'number' ? String(data.total_years) : '')
  }

  function addSkills() {
    const next = [...skills]
    const added = []
    for (const s of newSkill.split(',').map(x => x.trim())) {
      if (s && !next.some(x => x.toLowerCase() === s.toLowerCase())) { next.push(s); added.push(s.toLowerCase()) }
    }
    setSkills(next)
    setApplySkills(prev => [...prev, ...added])  // new skills are used for applying by default
    setNewSkill('')
  }

  const toggleApplySkill = s => {
    const key = s.toLowerCase()
    setApplySkills(prev => (prev.includes(key) ? prev.filter(x => x !== key) : [...prev, key]))
  }

  // Edit field `key` of row `i` in a list state
  const editRow = (setter, i, key, value) =>
    setter(rows => rows.map((r, j) => (j === i ? { ...r, [key]: value } : r)))

  // ---------------------------------------------------------------------------
  async function loadProfile() {
    setLoading(true)
    setError('')
    try {
      const data = await getProfile()
      setProfile(data)
      setFullName(data.full_name || data.name || '')
      setPhone(data.phone || '')
      setLocation(data.location || '')
      setLinkedinUrl(data.linkedin_url || '')
      fillParsed(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadProfile() }, [])

  // ---------------------------------------------------------------------------
  async function handleSave(e) {
    e.preventDefault()
    setSaveMsg('')
    setSaveError('')
    setSaving(true)
    try {
      const years = parseFloat(totalYears)
      const updated = await updateProfile({
        name: fullName,
        full_name: fullName,
        phone,
        location,
        linkedin_url: linkedinUrl,
        skills,
        apply_skills: skills.filter(s => applySkills.includes(s.toLowerCase())),
        experience: experience.filter(r => r.title.trim() || r.company.trim()),
        education: education.filter(r => r.degree.trim() || r.institution.trim()),
        ...(Number.isFinite(years) && years >= 0 ? { total_years: years } : {}),
      })
      setProfile(prev => ({ ...prev, ...updated }))
      fillParsed(updated)
      setSaveMsg('Profile saved successfully!')
      setTimeout(() => setSaveMsg(''), 4000)
    } catch (err) {
      setSaveError(err.message)
    } finally {
      setSaving(false)
    }
  }

  // ---------------------------------------------------------------------------
  async function handleUpload(e) {
    const file = e.target.files[0]
    if (!file) return
    setUploadMsg('')
    setUploadError('')
    setUploading(true)
    try {
      const updated = await uploadResume(file)
      setProfile(prev => ({ ...prev, ...updated }))
      if (updated.full_name || updated.name) setFullName(updated.full_name || updated.name)
      if (updated.phone) setPhone(updated.phone)
      if (updated.location) setLocation(updated.location)
      if (updated.linkedin_url) setLinkedinUrl(updated.linkedin_url)
      fillParsed(updated)
      setUploadMsg('Resume uploaded and parsed — review the details below and fix anything that is off. A job search has started.')
      setTimeout(() => setUploadMsg(''), 5000)
    } catch (err) {
      setUploadError(err.message)
    } finally {
      setUploading(false)
      fileRef.current.value = ''
    }
  }

  // ---------------------------------------------------------------------------
  async function handleDeleteResume() {
    if (!window.confirm('Delete your uploaded resume?')) return
    setDeletingResume(true)
    try {
      await deleteResume()
      setProfile(prev => ({
        ...prev,
        resume_filename: null,
        resume_path: null,
        skills: [],
        parsed_skills: [],
        experience: [],
        parsed_experience: [],
        education: [],
        parsed_education: [],
        total_years: null,
      }))
      fillParsed({})
    } catch (err) {
      setUploadError(err.message)
    } finally {
      setDeletingResume(false)
    }
  }

  // ---------------------------------------------------------------------------
  async function handleDeleteAllData() {
    setDeleteError('')
    setDeletingData(true)
    try {
      await deleteAllData()
      localStorage.removeItem('token')
      window.location.href = '/login'
    } catch (err) {
      setDeleteError(err.message)
      setDeletingData(false)
    }
  }

  // ---------------------------------------------------------------------------
  if (loading) return <Spinner text="Loading profile…" />
  if (error)   return <div className="alert alert-error">⚠️ {error}</div>

  const parsed = profile || {}
  const resumeFilename = parsed.resume_filename || (parsed.resume_path ? parsed.resume_path.split(/[\\/]/).pop() : null)

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Profile</h1>
      </div>

      {/* ── Resume Card ──────────────────────────────────── */}
      <div className="card">
        <div className="card-title">📄 Resume</div>

        {resumeFilename ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
            <span style={{ fontWeight: 600, color: '#1e293b' }}>
              📎 {resumeFilename}
            </span>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => fileRef.current.click()}
              disabled={uploading}
            >
              {uploading ? <Spinner size="sm" /> : '🔄 Re-upload'}
            </button>
            <button
              className="btn btn-danger btn-sm"
              onClick={handleDeleteResume}
              disabled={deletingResume}
            >
              {deletingResume ? <Spinner size="sm" /> : '🗑 Delete Resume'}
            </button>
          </div>
        ) : (
          <div style={{ marginBottom: '1rem' }}>
            <p style={{ color: '#64748b', marginBottom: '0.75rem', fontSize: '0.88rem' }}>
              No resume uploaded yet.
            </p>
            <button
              className="btn btn-primary btn-sm"
              onClick={() => fileRef.current.click()}
              disabled={uploading}
            >
              {uploading ? <><Spinner size="sm" /> Uploading…</> : '⬆ Upload Resume'}
            </button>
          </div>
        )}

        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx"
          style={{ display: 'none' }}
          onChange={handleUpload}
        />

        {uploadMsg   && <div className="alert alert-success">✅ {uploadMsg}</div>}
        {uploadError && <div className="alert alert-error">⚠️ {uploadError}</div>}

        {/* Parsed data — all editable; used for job search and scoring */}
        <form onSubmit={handleSave}>
          <h4 style={{ fontSize: '0.85rem', fontWeight: 700, color: '#475569', marginBottom: '0.2rem', marginTop: '0.75rem' }}>
            Skills — {skills.filter(s => applySkills.includes(s.toLowerCase())).length} of {skills.length} used for applying
          </h4>
          <p style={{ fontSize: '0.8rem', color: '#64748b', margin: '0 0 0.4rem' }}>
            Click a skill to use it (filled) or not (dashed) for job search, matching, scoring and cover letters.
            × removes it from your profile.{' '}
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setApplySkills(skills.map(s => s.toLowerCase()))}>Use all</button>{' '}
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setApplySkills([])}>Use none</button>
          </p>
          <div className="skills-list">
            {skills.map((s, i) => (
              <span key={`${i}-${s}`} className={`skill-pill ${applySkills.includes(s.toLowerCase()) ? '' : 'skill-off'}`}>
                <button
                  type="button"
                  className="skill-pill-toggle"
                  aria-pressed={applySkills.includes(s.toLowerCase())}
                  title={applySkills.includes(s.toLowerCase()) ? 'Used for applying — click to stop' : 'Not used — click to use'}
                  onClick={() => toggleApplySkill(s)}
                >{s}</button>
                <button
                  type="button"
                  className="skill-pill-remove"
                  aria-label={`Remove ${s}`}
                  onClick={() => setSkills(skills.filter((_, j) => j !== i))}
                >×</button>
              </span>
            ))}
            {skills.length === 0 && <span style={{ fontSize: '0.85rem', color: '#64748b' }}>No skills yet.</span>}
          </div>
          <div className="edit-row" style={{ marginTop: '0.5rem' }}>
            <input
              type="text"
              className="form-control"
              aria-label="Add skills"
              value={newSkill}
              onChange={e => setNewSkill(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addSkills() } }}
              placeholder="Add skills — comma-separated, press Enter"
            />
            <button type="button" className="btn btn-secondary btn-sm" onClick={addSkills}>+ Add</button>
          </div>

          <div className="form-group" style={{ maxWidth: 220, marginTop: '0.75rem' }}>
            <label className="form-label" htmlFor="total-years">Years of experience</label>
            <input
              id="total-years"
              type="number"
              min="0"
              step="0.5"
              className="form-control"
              value={totalYears}
              onChange={e => setTotalYears(e.target.value)}
            />
          </div>

          <h4 style={{ fontSize: '0.85rem', fontWeight: 700, color: '#475569', margin: '1rem 0 0.5rem' }}>
            Experience
          </h4>
          {experience.map((exp, i) => (
            <div key={i} className="edit-row">
              <input className="form-control" aria-label="Job title" placeholder="Title"
                value={exp.title} onChange={e => editRow(setExperience, i, 'title', e.target.value)} />
              <input className="form-control" aria-label="Company" placeholder="Company"
                value={exp.company} onChange={e => editRow(setExperience, i, 'company', e.target.value)} />
              <input className="form-control" aria-label="Duration" placeholder="e.g. 2021 – Present"
                value={exp.duration} onChange={e => editRow(setExperience, i, 'duration', e.target.value)} />
              <button type="button" className="btn btn-danger btn-sm" aria-label="Remove this role"
                onClick={() => setExperience(experience.filter((_, j) => j !== i))}>🗑</button>
            </div>
          ))}
          <button type="button" className="btn btn-secondary btn-sm"
            onClick={() => setExperience([...experience, { title: '', company: '', duration: '' }])}>
            + Add role
          </button>

          <h4 style={{ fontSize: '0.85rem', fontWeight: 700, color: '#475569', margin: '1rem 0 0.5rem' }}>
            Education
          </h4>
          {education.map((edu, i) => (
            <div key={i} className="edit-row">
              <input className="form-control" aria-label="Degree" placeholder="Degree"
                value={edu.degree} onChange={e => editRow(setEducation, i, 'degree', e.target.value)} />
              <input className="form-control" aria-label="Institution" placeholder="Institution"
                value={edu.institution} onChange={e => editRow(setEducation, i, 'institution', e.target.value)} />
              <input className="form-control" aria-label="Year" placeholder="Year"
                value={edu.year} onChange={e => editRow(setEducation, i, 'year', e.target.value)} />
              <button type="button" className="btn btn-danger btn-sm" aria-label="Remove this education entry"
                onClick={() => setEducation(education.filter((_, j) => j !== i))}>🗑</button>
            </div>
          ))}
          <button type="button" className="btn btn-secondary btn-sm"
            onClick={() => setEducation([...education, { degree: '', institution: '', year: '' }])}>
            + Add education
          </button>

          {saveMsg   && <div className="alert alert-success" style={{ marginTop: '0.75rem' }}>✅ {saveMsg}</div>}
          {saveError && <div className="alert alert-error" style={{ marginTop: '0.75rem' }}>⚠️ {saveError}</div>}
          <div style={{ marginTop: '1rem' }}>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? <><Spinner size="sm" /> Saving…</> : '💾 Save Profile'}
            </button>
          </div>
        </form>
      </div>

      {/* ── Editable Info ──────────────────────────────────── */}
      <div className="card">
        <div className="card-title">👤 Personal Information</div>

        {saveMsg   && <div className="alert alert-success">✅ {saveMsg}</div>}
        {saveError && <div className="alert alert-error">⚠️ {saveError}</div>}

        <form onSubmit={handleSave}>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Full Name</label>
              <input
                type="text"
                className="form-control"
                value={fullName}
                onChange={e => setFullName(e.target.value)}
                placeholder="Jane Doe"
              />
            </div>
            <div className="form-group">
              <label className="form-label">Phone</label>
              <input
                type="tel"
                className="form-control"
                value={phone}
                onChange={e => setPhone(e.target.value)}
                placeholder="+91 98765 43210"
              />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Location</label>
              <input
                type="text"
                className="form-control"
                value={location}
                onChange={e => setLocation(e.target.value)}
                placeholder="City, Country"
              />
            </div>
            <div className="form-group">
              <label className="form-label">LinkedIn URL</label>
              <input
                type="url"
                className="form-control"
                value={linkedinUrl}
                onChange={e => setLinkedinUrl(e.target.value)}
                placeholder="https://linkedin.com/in/..."
              />
            </div>
          </div>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? <><Spinner size="sm" /> Saving…</> : '💾 Save Profile'}
          </button>
        </form>
      </div>

      {/* ── Danger Zone ────────────────────────────────────── */}
      <div className="card" style={{ borderLeft: '4px solid #ef4444' }}>
        <div className="card-title" style={{ color: '#b91c1c' }}>⚠️ Danger Zone</div>
        <p style={{ fontSize: '0.88rem', color: '#475569', marginBottom: '1rem' }}>
          Permanently delete all your data including profile, resume, jobs, and applications.
          This action cannot be undone.
        </p>
        <button className="btn btn-danger" onClick={() => setShowDeleteModal(true)}>
          🗑 Delete All My Data
        </button>
      </div>

      {/* ── Delete All Data Modal ──────────────────────────── */}
      {showDeleteModal && (
        <Modal
          title="⚠️ Delete All Data"
          onClose={() => setShowDeleteModal(false)}
          footer={
            <>
              <button className="btn btn-secondary" onClick={() => setShowDeleteModal(false)}>
                Cancel
              </button>
              <button
                className="btn btn-danger"
                onClick={handleDeleteAllData}
                disabled={deletingData}
              >
                {deletingData ? <><Spinner size="sm" /> Deleting…</> : 'Yes, Delete Everything'}
              </button>
            </>
          }
        >
          <p style={{ marginBottom: '0.75rem' }}>
            Are you absolutely sure? This will permanently delete:
          </p>
          <ul style={{ paddingLeft: '1.25rem', color: '#475569', fontSize: '0.9rem', lineHeight: 1.8 }}>
            <li>Your profile and resume</li>
            <li>All jobs found for you</li>
            <li>All applications sent</li>
            <li>Your settings and credentials</li>
          </ul>
          {deleteError && (
            <div className="alert alert-error" style={{ marginTop: '1rem' }}>
              ⚠️ {deleteError}
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}
