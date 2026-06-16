import { useState, useEffect } from 'react'
import { useStore } from '../store'

export default function CampaignStart() {
  const {
    campaignSession,
    fetchActiveSession,
    listCampaigns,
    getCampaign,
    deployCampaign,
    startCampaign,
    endSession,
    deleteCampaign,
    fetchStatus,
    fetchEvents,
  } = useStore()

  const [deleteConfirm, setDeleteConfirm] = useState(null)

  useEffect(() => {
    fetchActiveSession()
    listCampaigns()
  }, [])

  const handleStart = async (name, continueFromLast = false) => {
    const deployResult = await deployCampaign(name)
    if (deployResult?.error) {
      console.warn('Deployment warning:', deployResult.error)
    }
    const result = await startCampaign(name, continueFromLast)
    if (result?.status === 'started') {
      await fetchStatus()
      await fetchEvents()
    }
  }

  const handleEnd = async () => {
    const result = await endSession()
    if (!result?.error) {
      await fetchStatus()
      fetchActiveSession()
    }
  }

  const handleDelete = async (name) => {
    await deleteCampaign(name)
    setDeleteConfirm(null)
  }

  const { activeSession, loading, error, campaigns } = campaignSession

  return (
    <div style={{ maxHeight: 'calc(100vh - 80px)', overflowY: 'auto' }}>
      <div style={{ maxWidth: 900, margin: '0 auto', paddingTop: 16 }}>
        {/* Active Session Banner — always visible when session is running */}
        {activeSession && (
          <div className="card" style={{ border: '2px solid var(--primary)', marginBottom: 20, background: 'var(--bg-secondary)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4, marginTop: 0 }}>🎮 Active Session</h3>
                <p style={{ color: 'var(--text-secondary)', fontSize: 13, margin: 0 }}>
                  {activeSession.campaign_name ? `Campaign: ${activeSession.campaign_name}` : 'Session in progress'}
                </p>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  className="btn btn-sm"
                  disabled={loading}
                  onClick={() => handleStart(activeSession.campaign_name, true)}
                >
                  🔄 Continue
                </button>
                <button
                  className="btn btn-sm btn-danger"
                  disabled={loading}
                  onClick={handleEnd}
                >
                  {loading ? '⏳' : '⏹ End'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Campaign Library Section */}
        <div className="section-header">
          <div>
            <h2>📚 Campaign Library</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4 }}>
              {activeSession
                ? 'Start a different campaign (will end the current session)'
                : 'Select a campaign to deploy and begin play.'}
            </p>
          </div>
          <button className="btn" onClick={() => { fetchActiveSession(); listCampaigns() }}>
            🔄 Refresh
          </button>
        </div>

        {error && (
          <div className="card" style={{ border: '1px solid var(--danger)', marginBottom: 16 }}>
            <p style={{ color: 'var(--danger)', fontSize: 13 }}>{error}</p>
          </div>
        )}

        {loading && campaigns.length === 0 && (
          <div className="loading">Loading campaigns…</div>
        )}

        {!loading && campaigns.length === 0 && (
          <div className="empty-state">
            <div style={{ fontSize: 48, marginBottom: 12 }}>📁</div>
            <p>No campaigns found.</p>
            <p style={{ fontSize: 12, marginTop: 4, color: 'var(--text-muted)' }}>
              Use the Campaign Builder to create a new campaign first.
            </p>
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {campaigns.map((campaign, i) => (
            <CampaignCard
              key={i}
              campaign={campaign}
              loading={loading}
              deleteConfirm={deleteConfirm}
              onStart={(name) => handleStart(name, false)}
              onContinue={(name) => handleStart(name, true)}
              onDeleteRequest={(name) => setDeleteConfirm(name)}
              onDeleteCancel={() => setDeleteConfirm(null)}
              onDeleteConfirm={(name) => handleDelete(name)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

// Campaign card component
function CampaignCard({
  campaign,
  loading,
  deleteConfirm,
  onStart,
  onContinue,
  onDeleteRequest,
  onDeleteCancel,
  onDeleteConfirm,
}) {
  const { getCampaign, regenerateAssets } = useStore()
  const [expanded, setExpanded] = useState(false)
  const [details, setDetails] = useState(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [regen, setRegen] = useState(null)

  const name = campaign.name || campaign.campaign_name || 'Unnamed'
  const isDeletePending = deleteConfirm === name

  const handleViewDetails = async () => {
    if (details) {
      setExpanded((v) => !v)
      return
    }
    setDetailsLoading(true)
    const result = await getCampaign(name)
    setDetails(result)
    setDetailsLoading(false)
    setExpanded(true)
  }

  const handleRegen = async () => {
    setRegen('running')
    const result = await regenerateAssets(name)
    setRegen(result)
  }

  return (
    <div className="card" style={{ cursor: 'default' }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1, marginRight: 12 }}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>{name}</h3>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>
            {campaign.description || campaign.summary || 'No description available'}
          </p>
          <div style={{ display: 'flex', gap: 10, fontSize: 12, color: 'var(--text-muted)', flexWrap: 'wrap' }}>
            {campaign.theme && <span>🎭 {campaign.theme}</span>}
            {campaign.total_scenes !== undefined && <span>🗺️ {campaign.total_scenes} scenes</span>}
            {campaign.total_npcs !== undefined && <span>👥 {campaign.total_npcs} NPCs</span>}
            {campaign.total_quests !== undefined && <span>⚔️ {campaign.total_quests} quests</span>}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <button
            className="btn btn-sm"
            onClick={handleViewDetails}
            disabled={detailsLoading}
          >
            {detailsLoading ? '…' : expanded ? '▲ Less' : '▼ Details'}
          </button>
          <button
            className="btn btn-sm"
            disabled={regen === 'running'}
            onClick={handleRegen}
            title="Regenerate maps & portraits with the latest image workflow and attach them to Foundry scenes"
          >
            {regen === 'running' ? '⏳ Maps…' : '🎨 Regenerate'}
          </button>
          <button
            className="btn"
            disabled={loading}
            onClick={() => onContinue(name)}
          >
            ▶ Continue
          </button>
          <button
            className="btn btn-primary"
            disabled={loading}
            onClick={() => onStart(name)}
          >
            {loading ? '⏳' : '🚀 Start'}
          </button>
          <button
            className="btn btn-sm"
            disabled={loading}
            onClick={() => onDeleteRequest(name)}
            title="Delete campaign"
          >
            🗑️
          </button>
        </div>
      </div>

      {/* Asset regeneration result */}
      {regen && regen !== 'running' && (
        <div
          style={{
            marginTop: 12,
            padding: '8px 12px',
            borderRadius: 6,
            fontSize: 12,
            background: regen.status === 'completed' ? 'var(--bg-tertiary)' : 'rgba(220,53,69,0.1)',
            border: '1px solid ' + (regen.status === 'completed' ? 'var(--success)' : 'var(--danger)'),
            color: 'var(--text-secondary)',
          }}
        >
          {regen.status === 'completed' ? (
            <span>
              ✅ Regenerated {regen.maps_generated} map(s), {regen.portraits_generated} portrait(s)
              {regen.scenes_attached > 0 && `, attached ${regen.scenes_attached} to Foundry scenes`}.
            </span>
          ) : (
            <span style={{ color: 'var(--danger)' }}>⚠️ {regen.error || 'Regeneration failed'}</span>
          )}
          {Array.isArray(regen.errors) && regen.errors.length > 0 && (
            <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              {regen.errors.slice(0, 4).map((e, i) => (
                <li key={i} style={{ color: 'var(--text-muted)' }}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Expanded details */}
      {expanded && details && (
        <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
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
              <div className="label">Vault Status</div>
              <span className="badge badge-connected" style={{ marginTop: 4 }}>{details.status}</span>
            </div>
          )}
        </div>
      )}

      {/* Delete confirmation */}
      {isDeletePending && (
        <div
          style={{
            marginTop: 16,
            paddingTop: 16,
            borderTop: '1px solid var(--border)',
            display: 'flex',
            gap: 8,
            alignItems: 'center',
          }}
        >
          <span style={{ fontSize: 13, color: 'var(--text-secondary)', flex: 1 }}>
            Delete "{name}"? This cannot be undone.
          </span>
          <button
            className="btn btn-danger btn-sm"
            onClick={() => onDeleteConfirm(name)}
          >
            Delete
          </button>
          <button className="btn btn-sm" onClick={onDeleteCancel}>
            Cancel
          </button>
        </div>
      )}
    </div>
  )
}
