import { useState, useEffect } from 'react'
import { useStore } from '../store'

export default function CampaignStart() {
  const {
    campaignSession,
    listCampaigns,
    getCampaign,
    startCampaign,
    endSession,
    deleteCampaign,
    fetchStatus,
    fetchEvents,
  } = useStore()

  const [loadingList, setLoadingList] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(null)

  // Load campaign list on mount
  useEffect(() => {
    listCampaigns()
  }, [])

  const handleStart = async (name, continueFromLast = false) => {
    const result = await startCampaign(name, continueFromLast)
    if (result.status === 'started') {
      await fetchStatus()
      await fetchEvents()
    }
  }

  const handleEnd = async () => {
    const result = await endSession()
    if (result.status === 'ended') {
      await fetchStatus()
    }
  }

  const handleDelete = async (name) => {
    await deleteCampaign(name)
    setDeleteConfirm(null)
  }

  // Render active session state
  if (campaignSession.activeSession) {
    return (
      <div style={{ maxHeight: 'calc(100vh - 80px)', overflowY: 'auto' }}>
        <div style={{ maxWidth: 700, margin: '0 auto', paddingTop: 16 }}>
          <div className="section-header">
            <div>
              <h2>🎮 Active Session</h2>
              <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4 }}>
                Currently playing — {campaignSession.activeSession.campaign_name}
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn" onClick={() => handleStart(campaignSession.activeSession.campaign_name, true)}>
                🔄 Continue Session
              </button>
              <button className="btn btn-danger" onClick={handleEnd}>
                ⏹ End Session
              </button>
            </div>
          </div>

          {/* Session details */}
          <div className="card" style={{ marginBottom: 16 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
              <div>
                <div className="label">Session ID</div>
                <div style={{ fontFamily: 'monospace', fontSize: 14, marginTop: 4 }}>
                  {campaignSession.activeSession.session_id}
                </div>
              </div>
              <div>
                <div className="label">Campaign</div>
                <div style={{ fontSize: 14, marginTop: 4 }}>{campaignSession.activeSession.campaign_name}</div>
              </div>
              <div>
                <div className="label">Status</div>
                <span className="badge badge-running" style={{ marginTop: 4 }}>Active</span>
              </div>
            </div>
          </div>

          {/* Quick actions */}
          <div className="card">
            <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Quick Actions</h4>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="btn" onClick={() => {
                const store = useStore.getState()
                store.setActivePage('dashboard')
              }}>
                📊 Dashboard
              </button>
              <button className="btn" onClick={() => {
                const store = useStore.getState()
                store.setActivePage('npcs')
              }}>
                👥 NPCs
              </button>
              <button className="btn" onClick={() => {
                const store = useStore.getState()
                store.setActivePage('overrides')
              }}>
                📝 Overrides
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // Render campaign list
  return (
    <div style={{ maxHeight: 'calc(100vh - 80px)', overflowY: 'auto' }}>
      <div style={{ maxWidth: 900, margin: '0 auto', paddingTop: 16 }}>
        <div className="section-header">
          <div>
            <h2>📚 Campaign Library</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4 }}>
              Select a campaign to start or continue playing.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={() => {
              listCampaigns()
            }}>
              🔄 Refresh
            </button>
          </div>
        </div>

        {campaignSession.loading && !campaignSession.campaigns.length && (
          <div className="loading">Loading campaigns...</div>
        )}

        {campaignSession.error && (
          <div className="card" style={{ border: '1px solid var(--danger)', marginBottom: 16 }}>
            <p style={{ color: 'var(--danger)', fontSize: 13 }}>{campaignSession.error}</p>
          </div>
        )}

        {!campaignSession.loading && campaignSession.campaigns.length === 0 && (
          <div className="empty-state">
            <div style={{ fontSize: 48, marginBottom: 12 }}>📁</div>
            <p>No campaigns found.</p>
            <p style={{ fontSize: 12, marginTop: 4, color: 'var(--text-muted)' }}>
              Use the Campaign Builder to create a new campaign first.
            </p>
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {campaignSession.campaigns.map((campaign, i) => (
            <CampaignCard
              key={i}
              campaign={campaign}
              onStart={(name) => handleStart(name)}
              onContinue={(name) => handleStart(name, true)}
              onDelete={(name) => setDeleteConfirm(name)}
              onCancelDelete={() => setDeleteConfirm(null)}
              onConfirmDelete={(name) => handleDelete(name)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

// Campaign card component
function CampaignCard({ campaign, onStart, onContinue, onDelete, onCancelDelete, onConfirmDelete }) {
  const { getCampaign, setCampaignSession } = useStore()
  const [expanded, setExpanded] = useState(false)
  const [details, setDetails] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleViewDetails = async () => {
    if (details) {
      setExpanded(!expanded)
      return
    }
    setLoading(true)
    const result = await getCampaign(campaign.name || campaign.campaign_name)
    setDetails(result)
    setLoading(false)
    setExpanded(true)
  }

  const name = campaign.name || campaign.campaign_name || 'Unnamed'

  return (
    <div className="card" style={{ cursor: 'default' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>{name}</h3>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>
            {campaign.description || campaign.summary || 'No description available'}
          </p>
          <div style={{ display: 'flex', gap: 8, fontSize: 12, color: 'var(--text-muted)' }}>
            {campaign.theme && <span>🎭 {campaign.theme}</span>}
            {campaign.total_scenes !== undefined && <span>🗺️ {campaign.total_scenes} scenes</span>}
            {campaign.total_npcs !== undefined && <span>👥 {campaign.total_npcs} NPCs</span>}
            {campaign.total_quests !== undefined && <span>⚔️ {campaign.total_quests} quests</span>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn" onClick={() => onContinue(name)}>
            ▶ Continue
          </button>
          <button className="btn btn-primary" onClick={() => onStart(name)}>
            🚀 Start
          </button>
          <button className="btn btn-sm" onClick={() => onDelete(name)} style={{ marginLeft: 4 }}>
            🗑️
          </button>
        </div>
      </div>

      {/* Expand details */}
      {expanded && details && (
        <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            {details.npc_count !== undefined && (
              <div>
                <div className="label">NPCs</div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{details.npc_count}</div>
              </div>
            )}
            {details.location_count !== undefined && (
              <div>
                <div className="label">Locations</div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{details.location_count}</div>
              </div>
            )}
            {details.quest_count !== undefined && (
              <div>
                <div className="label">Quests</div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{details.quest_count}</div>
              </div>
            )}
            {details.journal_entries !== undefined && (
              <div>
                <div className="label">Journal Entries</div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{details.journal_entries}</div>
              </div>
            )}
          </div>
          {details.status && (
            <div style={{ marginTop: 12 }}>
              <div className="label">Status</div>
              <span className="badge badge-connected" style={{ marginTop: 4 }}>
                {details.status}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Delete confirmation */}
      {deleteConfirm === name && (
        <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)', display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            Delete "{name}"? This cannot be undone.
          </span>
          <button className="btn btn-danger btn-sm" onClick={() => onConfirmDelete(name)}>Delete</button>
          <button className="btn btn-sm" onClick={onCancelDelete}>Cancel</button>
        </div>
      )}

      {loading && <div className="loading" style={{ padding: 0 }}>Loading...</div>}
    </div>
  )
}
