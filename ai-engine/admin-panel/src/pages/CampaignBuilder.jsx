import React from 'react'
import { useStore } from '../store.js'

const CampaignBuilder = () => {
  const {
    newCampaign,
    setNewCampaign,
    resetNewCampaign,
    npcs,
    fetchNpcs,
    fetchEvents
  } = useStore()

  const availableFiles = [
    'Dungeons_and_Dragons/Worldbuilding.md',
    'Dungeons_and_Dragons/Aethelwyrd Campaign State.md',
    'Dungeons_and_Dragons/Act I - The Shattered Sky.md',
    'Dungeons_and_Dragons/NPCs - Act I.md',
    'Dungeons_and_Dragons/Character Hooks.md',
    'Dungeons_and_Dragons/DnD SRD_v5.2.1_Full_Text.txt',
    'Dungeons_and_Dragons/DM_Reference.md',
    'Dungeons_and_Dragons/Dungeons_and_Dragons.md',
    'Dungeons_and_Dragons/DnD SRD v5.2.1 Quick Reference Guide.md',
    'Dungeons_and_Dragons/Session 01 - The Shattered Dawn.md',
    'Dungeons_and_Dragons/Session 02 - Aethelwyrd Nomad Village.md',
    'Dungeons_and_Dragons/Session 03 - Forest Journey.md',
    'Dungeons_and_Dragons/Session 04 - Confrontation.md',
    'Dungeons_and_Dragons/Aethelwyrd Religion.md',
    'Dungeons_and_Dragons/Aethelwyrd History.md',
    'Dungeons_and_Dragons/Selmor.md',
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

  const handleBuild = () => {
    alert(`Campaign "${newCampaign.name}" would be built with ${newCampaign.vaultFiles.split(',').filter(f=>f.trim()).length} files.`)
    resetNewCampaign()
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
                  const file = `Dungeons_and_Dragons/${npc.name || 'Unknown'}.md`
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
        <button className="btn btn-primary" onClick={handleBuild}>
          🏗️ Build Campaign
        </button>
        <button className="btn" onClick={() => resetNewCampaign()}>
          Clear
        </button>
      </div>
    </div>
  )
}

export default CampaignBuilder
