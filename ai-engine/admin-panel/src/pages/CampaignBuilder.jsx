import React from 'react'
import { useStore } from '../store.js'

const LEVEL_RANGE_PRESETS = [
  { label: 'Short Arc (1–5)',     value: '1-5' },
  { label: 'Medium (1–10)',       value: '1-10' },
  { label: 'Long (3–12)',         value: '3-12' },
  { label: 'Epic (3–15)',         value: '3-15' },
  { label: 'Full Campaign (1–20)',value: '1-20' },
]

const scalingHint = (range) => {
  const parts = range.replace('–', '-').split('-').map(Number).filter(Boolean)
  if (parts.length < 2) return null
  const span = parts[1] - parts[0]
  if (span <= 5)  return '~3–5 scenes · 2–3 acts · 2–4 encounters (one tier, Arc 1 covers it all)'
  if (span <= 10) return '~5–8 scenes · 4–6 acts · 4–6 encounters (two tiers — use Extend Arc when party reaches mid-point)'
  if (span <= 15) return '~8–12 scenes · 6–9 acts · 6–9 encounters (three tiers — Arc 1 sets up; Extend Arc 2–3 times as party levels)'
  return '~10–15 scenes · 9–12 acts · 8–12 encounters (full epic — extend every 4–5 levels)'
}

const CampaignBuilder = () => {
  const {
    campaignWizard,
    setWizardField,
    resetWizard,
    buildCampaign,
  } = useStore()

  const hint = scalingHint(campaignWizard.levelRange || '1-5')

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>Campaign Builder</h2>
          <p>Describe your campaign — the AI will generate everything needed from the details you provide</p>
        </div>
      </div>

      <div className="card">
        <h3 style={{ fontSize: '14px', marginBottom: '16px' }}>Campaign Details</h3>
        <div className="form-group">
          <label>Campaign Name</label>
          <input
            className="input"
            placeholder="My New Campaign"
            value={campaignWizard.name}
            onChange={(e) => setWizardField('name', e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Description</label>
          <textarea
            className="textarea"
            placeholder="Describe the world, tone, and setting of your campaign. Include key themes, conflicts, and the general mood you want."
            rows={4}
            value={campaignWizard.description}
            onChange={(e) => setWizardField('description', e.target.value)}
          />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div className="form-group">
            <label>Theme</label>
            <input
              className="input"
              placeholder="e.g. dark fantasy, steampunk, high magic"
              value={campaignWizard.theme}
              onChange={(e) => setWizardField('theme', e.target.value)}
            />
          </div>
          <div className="form-group">
            <label>Scale</label>
            <input
              className="input"
              placeholder="e.g. one-shot, short arc, full campaign"
              value={campaignWizard.scale}
              onChange={(e) => setWizardField('scale', e.target.value)}
            />
          </div>
        </div>

        {/* Level Range — the key new field */}
        <div className="form-group">
          <label>
            Level Range
            <span style={{ fontSize: '11px', color: 'var(--text-secondary)', marginLeft: '8px' }}>
              Controls how many scenes, acts, and encounters are generated
            </span>
          </label>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            {LEVEL_RANGE_PRESETS.map(({ label, value }) => (
              <button
                key={value}
                className={`btn btn-sm${(campaignWizard.levelRange || '1-5') === value ? ' btn-primary' : ''}`}
                style={{ fontSize: '12px', padding: '4px 10px' }}
                onClick={() => setWizardField('levelRange', value)}
              >
                {label}
              </button>
            ))}
            <input
              className="input"
              style={{ width: '90px', fontSize: '13px' }}
              placeholder="e.g. 3-15"
              value={campaignWizard.levelRange || '1-5'}
              onChange={(e) => setWizardField('levelRange', e.target.value)}
            />
          </div>
          {hint && (
            <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '6px' }}>
              ⚖️ {hint} — the AI generates all of Arc 1 upfront;
              use <strong>Extend Arc</strong> in Saved Campaigns to add more content as the party levels up.
            </p>
          )}
        </div>

        <div className="form-group">
          <label>Seed Ideas <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>(optional: NPCs, locations, or concepts to shape the campaign)</span></label>
          <textarea
            className="textarea"
            placeholder="Key NPCs to include, specific locations, plot hooks, or any other ideas that should shape the campaign"
            rows={3}
            value={campaignWizard.seedIdeas}
            onChange={(e) => setWizardField('seedIdeas', e.target.value)}
          />
        </div>
      </div>

      <div style={{ marginTop: '16px', display: 'flex', gap: '8px' }}>
        <button className="btn btn-primary" onClick={() => {
          if (!campaignWizard.name.trim()) {
            setWizardField('buildError', 'Please enter a campaign name.')
            return
          }
          setWizardField('buildError', null)
          buildCampaign()
        }} disabled={campaignWizard.buildInProgress}>
          {campaignWizard.buildInProgress ? '🏗️ Building...' : '🏗️ Build Campaign'}
        </button>
        <button className="btn" onClick={() => resetWizard()}>
          Clear
        </button>
      </div>

      {campaignWizard.buildError && (
        <div style={{ marginTop: '16px', padding: '12px', background: '#3a1f1f', borderRadius: '6px', border: '1px solid #6a3030' }}>
          <p style={{ color: '#ff9999', fontSize: '13px', margin: 0 }}>❌ {campaignWizard.buildError}</p>
        </div>
      )}

      {campaignWizard.buildResult && (
        <div style={{ marginTop: '16px', padding: '12px', background: '#1f3a1f', borderRadius: '6px', border: '1px solid #306a30' }}>
          <p style={{ color: '#99ff99', fontSize: '13px', margin: '0 0 6px' }}>✅ Campaign build initiated ({campaignWizard.buildResult.prompt_id || 'processing'})</p>
          {campaignWizard.buildResult.status === 'ok' && (
            <div style={{ fontSize: '11px', color: '#88cc88' }}>
              Campaign generated with {campaignWizard.buildResult.steps?.length || 0} steps
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default CampaignBuilder
