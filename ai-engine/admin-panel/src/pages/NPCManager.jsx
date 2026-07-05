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
              <button
                key={i}
                type="button"
                onClick={() => setSelected(npc)}
                style={{
                  display: 'block',
                  width: '100%',
                  padding: '10px',
                  cursor: 'pointer',
                  borderRadius: '6px',
                  marginBottom: '2px',
                  border: 'none',
                  background: selected?.name === npc.name ? 'var(--accent-dim)' : 'transparent',
                  color: 'var(--text-primary)',
                  font: 'inherit',
                  fontSize: '13px',
                  textAlign: 'left',
                }}
              >
                {npc.name || 'Unnamed NPC'}
              </button>
            ))}
          </div>

          {/* NPC details */}
          <div className="card">
            {selected ? <NpcDetail npc={selected} /> : (
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

// Known fields get a readable layout; anything else falls back to raw JSON
// so unexpected/future fields from Foundry are never silently dropped.
const KNOWN_FIELDS = new Set(['name', 'uuid', 'type', 'has_player_owner', 'hp', 'max_hp'])

const NpcDetail = ({ npc }) => {
  const extra = Object.entries(npc).filter(([k]) => !KNOWN_FIELDS.has(k))
  const hpPct = npc.max_hp ? Math.max(0, Math.min(100, (npc.hp / npc.max_hp) * 100)) : null

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
        <h3 style={{ fontSize: '16px', margin: 0 }}>{npc.name || 'Unnamed NPC'}</h3>
        {npc.type && <span className="badge">{npc.type}</span>}
        {npc.has_player_owner && <span className="badge badge-connected">Player-owned</span>}
      </div>

      {hpPct !== null && (
        <div style={{ marginBottom: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
            <span>HP</span>
            <span>{npc.hp} / {npc.max_hp}</span>
          </div>
          <div style={{ height: '6px', borderRadius: '3px', background: 'var(--bg-tertiary)', overflow: 'hidden' }}>
            <div style={{
              height: '100%',
              width: `${hpPct}%`,
              background: hpPct > 50 ? 'var(--success, #4caf50)' : hpPct > 20 ? '#e0a030' : 'var(--danger)',
            }} />
          </div>
        </div>
      )}

      {extra.length > 0 && (
        <pre style={{
          fontSize: '12px',
          whiteSpace: 'pre-wrap',
          lineHeight: '1.6',
          color: 'var(--text-secondary)',
          marginBottom: '12px',
        }}>
          {JSON.stringify(Object.fromEntries(extra), null, 2)}
        </pre>
      )}

      {npc.uuid && (
        <p style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'monospace', marginTop: '12px' }}>
          {npc.uuid}
        </p>
      )}
    </div>
  )
}

export default NPCManager
