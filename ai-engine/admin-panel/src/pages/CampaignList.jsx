import React, { useState, useEffect } from 'react'
import { useStore, API_BASE } from '../store.js'
import { safeFetch } from '../fetch.js'

const CampaignList = () => {
  const { campaignWizard, buildCampaign } = useStore()
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [loadingCampaign, setLoadingCampaign] = useState(false)
  const [error, setError] = useState('')
  const [loadedData, setLoadedData] = useState(null)

  const loadCampaigns = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await safeFetch(`${API_BASE}/campaigns/list`)
      if (!res.ok) {
        setError(res.error || 'Failed to load campaigns')
      }
      setCampaigns(res.data?.campaigns || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadCampaigns() }, [])

  const loadCampaign = async (name) => {
    setSelected(name)
    setError('')
    setLoadingCampaign(true)
    setLoadedData(null)
    try {
      const res = await safeFetch(`${API_BASE}/campaigns/${encodeURIComponent(name)}`)
      if (!res.ok) {
        setError(res.error || 'Failed to load campaign')
        setSelected(null)
        return
      }
      setLoadedData(res.data)
    } catch (e) {
      setError(e.message)
      setSelected(null)
    } finally {
      setLoadingCampaign(false)
    }
  }

  const deleteCampaign = async (name) => {
    if (!confirm(`Delete campaign "${name}"? This cannot be undone.`)) return
    try {
      const res = await safeFetch(`${API_BASE}/campaigns/${encodeURIComponent(name)}`, { method: 'DELETE' })
      if (!res.ok) {
        setError(res.error || 'Failed to delete campaign')
      } else {
        loadCampaigns()
        setSelected(null)
      }
    } catch (e) {
      setError(e.message)
    }
  }

  const hasCampaigns = campaigns && campaigns.length > 0

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>Saved Campaigns</h2>
          <p>Browse, load, or delete campaigns stored in the Obsidian vault</p>
        </div>
        <button className="btn" onClick={loadCampaigns} disabled={loading}>
          {loading ? 'Loading...' : '🔄 Refresh'}
        </button>
      </div>

      {error && (
        <div style={{ marginBottom: '16px', padding: '12px', background: '#3a1f1f', borderRadius: '6px', border: '1px solid #6a3030' }}>
          <p style={{ color: '#ff9999', fontSize: '13px', margin: 0 }}>❌ {error}</p>
        </div>
      )}

      {!hasCampaigns && !loading && !error && (
        <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-secondary)' }}>
          <p style={{ fontSize: '16px', marginBottom: '8px' }}>No campaigns found</p>
          <p style={{ fontSize: '13px' }}>Build a new campaign to get started.</p>
        </div>
      )}

      {hasCampaigns && (
        <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: '16px' }}>
          {/* Campaign list */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {campaigns.map(c => (
              <div key={c.name} style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '10px 12px',
                background: selected === c.name ? 'var(--bg-active)' : 'transparent',
                borderRadius: '6px',
                cursor: 'pointer',
                border: selected === c.name ? '1px solid var(--bg-active)' : '1px solid transparent',
              }}
                onClick={() => loadCampaign(c.name)}
              >
                <span style={{ fontSize: '14px', fontWeight: selected === c.name ? '600' : '400' }}>
                  {c.name}
                </span>
                <button
                  className="btn btn-sm"
                  style={{ background: 'transparent', border: 'none', padding: '4px 8px', fontSize: '12px', color: '#ff6666' }}
                  onClick={(e) => { e.stopPropagation(); deleteCampaign(c.name) }}
                  title="Delete campaign"
                >
                  🗑
                </button>
              </div>
            ))}
          </div>

          {/* Campaign detail */}
          <div className="card">
            {loadingCampaign ? (
              <p style={{ color: 'var(--text-secondary)' }}>Loading campaign...</p>
            ) : selected ? (
              <div>
                <h3 style={{ fontSize: '18px', marginBottom: '8px' }}>
                  {selected}
                </h3>
                {(() => {
                  if (!loadedData) return null
                  const d = loadedData.data || loadedData
                  return (
                    <div>
                      {d.description && (
                        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>{d.description}</p>
                      )}
                      <div style={{ display: 'flex', gap: '12px', marginBottom: '16px', fontSize: '12px' }}>
                        {d.theme && <span className="badge">{d.theme}</span>}
                        {d.scale && <span className="badge">{d.scale}</span>}
                      </div>
                      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                        ✅ Ready to load into FoundryVTT when you begin your session.
                      </p>
                    </div>
                  )
                })()}
              </div>
            ) : (
              <p style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '24px' }}>
                Select a campaign from the list to view its details
              </p>
            )}
          </div>
        </div>
      )}

      <div style={{ marginTop: '24px', paddingTop: '16px', borderTop: '1px solid var(--bg-tertiary)' }}>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
          💡 Tip: To load a campaign created outside the AI GM, describe it in the <a href="#campaign" style={{ color: '#66aaff' }}>Campaign Builder</a> — the AI will generate everything from your description.
        </p>
      </div>
    </div>
  )
}

export default CampaignList
