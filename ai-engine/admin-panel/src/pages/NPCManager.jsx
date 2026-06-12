import React, { useEffect, useState } from 'react'
import { useStore } from '../store.js'

const NPCManager = () => {
  const { npcs, fetchNpcs, engineStatus } = useStore()
  const [selected, setSelected] = useState(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    fetchNpcs()
  }, [])

  const filtered = npcs.filter(n =>
    (n.name || '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>NPC Manager</h2>
          <p>View and manage NPCs from FoundryVTT</p>
        </div>
        <button className="btn btn-sm" onClick={fetchNpcs}>
          ↻ Refresh from Foundry
        </button>
      </div>

      {!engineStatus?.connected && (
        <div className="card" style={{ marginBottom: '16px', borderLeft: '3px solid var(--danger)' }}>
          <p style={{ fontSize: '13px' }}>
            ⚠️ Not connected to FoundryVTT. NPCs can only be loaded when the relay is connected.
          </p>
        </div>
      )}

      <div style={{ marginBottom: '16px' }}>
        <input
          className="input"
          placeholder="Search NPCs..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: '300px' }}
        />
      </div>

      {filtered.length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: '250px 1fr', gap: '16px' }}>
          {/* NPC list */}
          <div className="card" style={{ overflowY: 'auto', maxHeight: '500px' }}>
            {filtered.map((npc, i) => (
              <div
                key={i}
                onClick={() => setSelected(npc)}
                style={{
                  padding: '10px',
                  cursor: 'pointer',
                  borderRadius: '6px',
                  marginBottom: '2px',
                  background: selected?.name === npc.name ? 'var(--accent-dim)' : 'transparent',
                  fontSize: '13px'
                }}
              >
                {npc.name || 'Unnamed NPC'}
              </div>
            ))}
          </div>

          {/* NPC details */}
          <div className="card">
            {selected ? (
              <div>
                <h3 style={{ fontSize: '16px', marginBottom: '12px' }}>
                  {selected.name || 'Unnamed NPC'}
                </h3>
                <pre style={{
                  fontSize: '12px',
                  whiteSpace: 'pre-wrap',
                  lineHeight: '1.6',
                  color: 'var(--text-secondary)'
                }}>
                  {JSON.stringify(selected, null, 2)}
                </pre>
              </div>
            ) : (
              <div className="empty-state">
                <p>Select an NPC to view details</p>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="empty-state">
          <p>No NPCs found. Make sure NPCs exist in your FoundryVTT scene.</p>
        </div>
      )}
    </div>
  )
}

export default NPCManager
