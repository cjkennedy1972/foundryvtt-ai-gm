import React, { useState } from 'react'
import { useStore } from '../store.js'

const API_BASE = '/api'

const CampaignBuilder = () => {
  const {
    newCampaign,
    setNewCampaign,
    resetNewCampaign,
    npcs,
    fetchNpcs,
    fetchEvents
  } = useStore()

  const [building, setBuilding] = useState(false)
  const [buildError, setBuildError] = useState(null)
  const [buildResult, setBuildResult] = useState(null)

  const availableFiles = [
    'Aethelwyrd/Worldbuilding.md',
    'Aethelwyrd/Aethelwyrd Campaign State.md',
    'Aethelwyrd/Act I - The Shattered Sky.md',
    'Aethelwyrd/NPCs - Act I.md',
    'Aethelwyrd/Character Hooks.md',
    'Aethelwyrd/Aethelwyrd.md',
    'Aethelwyrd/Aethelwyrd Chronicles.md',
    'DM_Reference.md',
    'DnD_SRD_v5.2.1_Full_Text.txt',
    'Dungeons_and_Dragons.md',
    'Foundry API Relay.md',
    'The Age of Unmaking/The Age of Unmaking.md',
  ]

  const handleToggleFile = (file) => {
    const current = newCampaign.vaultFiles.split(',').map(f => f.trim()).filter(Boolean)
    let updated
    if (current.includes(file)) {
      updated = current.filter(f => f !== file).join(', ')
    } else {
      updated = [...current, file].join(', ')
    }
    setNewCampaign('vaultFiles', updated)
  }

  const handleBuild = async () => {
    if (!newCampaign.name.trim()) {
      setBuildError('Please enter a campaign name.')
      return
    }
    const files = newCampaign.vaultFiles.split(',').map(f => f.trim()).filter(Boolean)
    if (files.length === 0) {
      setBuildError('Please select at least one source file.')
      return
    }
    setBuilding(true)
    setBuildError(null)
    try {
      const res = await fetch(`${API_BASE}/campaign/load`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newCampaign.name,
          vault_files: files,
          description: newCampaign.description
        })
      })
      const data = await res.json()
      if (data.status === 'ok') {
        setBuildResult(data)
        resetNewCampaign()
      } else {
        setBuildError(data.message || 'Build failed')
      }
    } catch (e) {
      setBuildError(`Network error: ${e.message}`)
    } finally {
      setBuilding(false)
    }
  }

  const handleImportFromNPCs = () => {
    const npcNames = npcs.map(n => n.name || 'Unknown')
    setNewCampaign('vaultFiles', npcNames.join(', '))
  }

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>Campaign Builder</h2>
          <p>Build a new campaign by selecting source files from the Obsidian vault</p>
        </div>
      </div>

      {/* Current NPC inventory */}
      <div className="card" style={{ marginBottom: '16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
          <h3 style={{ fontSize: '14px' }}>NPC Inventory (from Foundry)</h3>
          <button className="btn btn-sm" onClick={fetchNpcs}>Refresh NPCs</button>
        </div>
        {npcs.length > 0 ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {npcs.map((npc, i) => (
              <span key={i} className="badge" style={{ background: 'var(--bg-tertiary)', cursor: 'pointer' }}
                onClick={() => {
                  const current = newCampaign.vaultFiles.split(',').map(f => f.trim()).filter(Boolean)
                  const file = `Aethelwyrd/${npc.name || 'Unknown'}.md`
                  setNewCampaign('vaultFiles', [...current, file].join(', '))
                }}
                title={`Click to add ${npc.name || 'this NPC'} to campaign`}>
                {(npc.name || 'Unknown')} +
              </span>
            ))}
          </div>
        ) : (
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
            Click "Refresh NPCs" to load NPC data from FoundryVTT
          </p>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
        {/* Campaign info */}
        <div className="card">
          <h3 style={{ fontSize: '14px', marginBottom: '16px' }}>Campaign Info</h3>
          <div className="form-group">
            <label>Campaign Name</label>
            <input
              className="input"
              placeholder="My New Campaign"
              value={newCampaign.name}
              onChange={(e) => setNewCampaign('name', e.target.value)}
            />
          </div>
          <div className="form-group">
            <label>Description</label>
            <textarea
              className="textarea"
              placeholder="Describe the campaign..."
              value={newCampaign.description}
              onChange={(e) => setNewCampaign('description', e.target.value)}
            />
          </div>
        </div>

        {/* File selector */}
        <div className="card">
          <h3 style={{ fontSize: '14px', marginBottom: '16px' }}>
            Source Files ({newCampaign.vaultFiles.split(',').filter(f=>f.trim()).length} selected)
          </h3>
          <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
            {availableFiles.map(file => {
              const selected = newCampaign.vaultFiles.includes(file)
              return (
                <div
                  key={file}
                  style={{
                    padding: '8px',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    background: selected ? 'var(--accent-dim)' : 'transparent',
                    border: selected ? '1px solid var(--accent)' : '1px solid transparent',
                    marginBottom: '4px',
                    fontSize: '12px'
                  }}
                  onClick={() => handleToggleFile(file)}
                >
                  {file.split('/').pop()}
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <div style={{ marginTop: '16px', display: 'flex', gap: '8px' }}>
        <button className="btn btn-primary" onClick={handleBuild} disabled={building}>
          {building ? '🏗️ Building...' : '🏗️ Build Campaign'}
        </button>
        <button className="btn" onClick={() => resetNewCampaign()}>
          Clear
        </button>
      </div>

      {buildError && (
        <div style={{ marginTop: '16px', padding: '12px', background: '#3a1f1f', borderRadius: '6px', border: '1px solid #6a3030' }}>
          <p style={{ color: '#ff9999', fontSize: '13px', margin: 0 }}>❌ {buildError}</p>
        </div>
      )}

      {buildResult && (
        <div style={{ marginTop: '16px', padding: '12px', background: '#1f3a1f', borderRadius: '6px', border: '1px solid #306a30' }}>
          <p style={{ color: '#99ff99', fontSize: '13px', margin: '0 0 6px' }}>✅ {buildResult.message || 'Campaign loaded successfully'} ({buildResult.loaded_files} files linked)</p>
          {buildResult.folder && (
            <div style={{ fontSize: '11px', color: '#88cc88' }}>
              📁 {buildResult.folder}
            </div>
          )}
        </div>
      )}

    </div>
  )
}

export default CampaignBuilder
