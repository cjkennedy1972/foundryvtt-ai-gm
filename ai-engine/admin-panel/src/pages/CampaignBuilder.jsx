import React from 'react'
import { useStore } from '../store.js'
import { API_BASE } from '../store.js'

const CampaignBuilder = () => {
  const {
    campaignWizard,
    setWizardField,
    resetWizard,
    buildCampaign,
  } = useStore()

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
