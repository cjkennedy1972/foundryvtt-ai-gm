import React, { useState, useEffect } from 'react'
import { useStore, API_BASE } from '../store.js'
import { safeFetch } from '../fetch.js'

const CampaignList = () => {
  const { extendCampaignArc, teardownCampaign } = useStore()
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [loadingCampaign, setLoadingCampaign] = useState(false)
  const [error, setError] = useState('')
  const [loadedData, setLoadedData] = useState(null)

  // Arc extension state
  const [extendLevel, setExtendLevel] = useState(5)
  const [extending, setExtending] = useState(false)
  const [extendResult, setExtendResult] = useState(null)
  const [extendError, setExtendError] = useState('')

  // Teardown state
  const [tearingDown, setTearingDown] = useState(false)
  const [teardownResult, setTeardownResult] = useState(null)
  const [teardownError, setTeardownError] = useState('')

  // Optimization state
  const [optimizing, setOptimizing] = useState(false)
  const [optimizeResult, setOptimizeResult] = useState(null)
  const [optimizeError, setOptimizeError] = useState('')
  const [showOptimizeDetails, setShowOptimizeDetails] = useState(false)

  const loadCampaigns = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/campaign/list`)
      const data = await res.json()
      if (data.error) setError(data.error)
      setCampaigns(data.campaigns || [])
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
    setExtendResult(null)
    setExtendError('')
    setTeardownResult(null)
    setTeardownError('')
    setOptimizeResult(null)
    setOptimizeError('')
    setShowOptimizeDetails(false)
    try {
      const res = await fetch(`${API_BASE}/campaign/get/${encodeURIComponent(name)}`)
      const data = await res.json()
      if (data.error) {
        setError(data.error)
        setSelected(null)
        return
      }
      setLoadedData(data)
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
      const res = await fetch(`${API_BASE}/campaign/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      })
      const data = await res.json()
      if (data.error) setError(data.error)
      else { loadCampaigns(); setSelected(null) }
    } catch (e) {
      setError(e.message)
    }
  }

  const handleTeardown = async () => {
    if (!selected) return
    if (!confirm(
      `Remove all AI-GM content for "${selected}" from FoundryVTT?\n\n` +
      `This will delete all scenes, actors, journals, loot tables, and playlists ` +
      `created by this campaign. It does NOT delete the vault files or local assets.\n\n` +
      `This cannot be undone.`
    )) return

    setTearingDown(true)
    setTeardownResult(null)
    setTeardownError('')
    const result = await teardownCampaign(selected)
    setTearingDown(false)
    if (result.ok) {
      setTeardownResult(result.data)
    } else {
      setTeardownError(result.error || 'Teardown failed')
    }
  }

  const handleOptimize = async () => {
    if (!selected) return
    setOptimizing(true)
    setOptimizeResult(null)
    setOptimizeError('')
    setShowOptimizeDetails(false)
    try {
      const res = await fetch(`${API_BASE}/campaign/analyze-and-optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      })
      const data = await res.json()
      if (data.error) {
        setOptimizeError(data.error)
      } else if (data.status === 'error') {
        setOptimizeError(data.error || 'Optimization failed')
      } else {
        setOptimizeResult(data)
        setShowOptimizeDetails(true)
      }
    } catch (e) {
      setOptimizeError(e.message)
    } finally {
      setOptimizing(false)
    }
  }

  const handleExtend = async () => {
    if (!selected) return
    setExtending(true)
    setExtendResult(null)
    setExtendError('')
    const result = await extendCampaignArc(selected, extendLevel)
    setExtending(false)
    if (result.ok) {
      setExtendResult(result.data)
    } else {
      setExtendError(result.error || 'Extension failed')
    }
  }

  // Derive existing arc count and last arc level from loaded campaign data
  const campaignArcs = (() => {
    if (!loadedData) return []
    const d = loadedData.data || loadedData
    return (d.story_arcs || []).filter(a => a.arc_number > 0)
  })()
  const lastArcLevel = (() => {
    if (!loadedData) return null
    const d = loadedData.data || loadedData
    // Try to read from campaign_data.last_arc or story_arcs
    const levelRange = d.campaign?.level_range || ''
    const parts = levelRange.replace('–', '-').split('-').map(Number).filter(Boolean)
    return parts.length >= 2 ? parts[1] : null
  })()

  const hasCampaigns = campaigns && campaigns.length > 0

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>Saved Campaigns</h2>
          <p>Browse, extend, or delete campaigns stored in the Obsidian vault</p>
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
                <h3 style={{ fontSize: '18px', marginBottom: '8px' }}>{selected}</h3>
                {(() => {
                  if (!loadedData) return null
                  const d = loadedData.data || loadedData
                  const scenes = d.scenes || []
                  const encounters = d.encounters || []
                  const levelRange = d.campaign?.level_range || d.level_range || '?'
                  return (
                    <div>
                      {d.description && (
                        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>{d.description}</p>
                      )}
                      <div style={{ display: 'flex', gap: '12px', marginBottom: '12px', fontSize: '12px', flexWrap: 'wrap' }}>
                        {d.theme && <span className="badge">{d.theme}</span>}
                        {levelRange !== '?' && <span className="badge">Levels {levelRange}</span>}
                        <span className="badge">{scenes.length} scene{scenes.length !== 1 ? 's' : ''}</span>
                        <span className="badge">{encounters.length} encounter{encounters.length !== 1 ? 's' : ''}</span>
                        {campaignArcs.length > 0 && (
                          <span className="badge" style={{ background: 'var(--accent-dim)' }}>
                            Arc {campaignArcs.length} deployed
                          </span>
                        )}
                      </div>

                      {/* Scenes list */}
                      {scenes.length > 0 && (
                        <div style={{ marginBottom: '16px' }}>
                          <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>
                            SCENES
                          </p>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                            {scenes.map((s, i) => (
                              <div key={i} style={{ fontSize: '12px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                                <span style={{ color: 'var(--text-secondary)', minWidth: '20px' }}>
                                  {s.arc_number ? `A${s.arc_number}` : 'A1'}
                                </span>
                                <span>{s.name}</span>
                                {s.act && <span style={{ color: 'var(--text-secondary)', fontSize: '11px' }}>Act {s.act}</span>}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })()}

                {/* ── Extend Arc panel ── */}
                <div style={{
                  marginTop: '16px',
                  padding: '14px',
                  background: 'var(--bg-tertiary)',
                  borderRadius: '8px',
                  border: '1px solid var(--bg-active)',
                }}>
                  <h4 style={{ fontSize: '13px', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    ➕ Generate Next Arc
                  </h4>
                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                    The AI will generate new scenes, NPCs, and encounters starting at the party's current level,
                    picking up the story where Arc {campaignArcs.length > 0 ? campaignArcs.length : 1} left off.
                    New content is deployed into FoundryVTT alongside existing scenes.
                  </p>
                  <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                    <label style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                      Party's current level:
                    </label>
                    <input
                      type="number"
                      min={1} max={20}
                      className="input"
                      style={{ width: '64px', fontSize: '13px' }}
                      value={extendLevel}
                      onChange={(e) => setExtendLevel(Math.max(1, Math.min(20, parseInt(e.target.value) || 1)))}
                    />
                    <button
                      className="btn btn-primary"
                      style={{ fontSize: '13px' }}
                      onClick={handleExtend}
                      disabled={extending}
                    >
                      {extending ? '⏳ Generating Arc...' : '🗺️ Extend Campaign'}
                    </button>
                  </div>

                  {extending && (
                    <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '10px' }}>
                      Generating new arc — this takes 2–5 minutes (LLM + map generation)…
                    </p>
                  )}

                  {extendError && (
                    <div style={{ marginTop: '10px', padding: '8px 12px', background: '#3a1f1f', borderRadius: '6px' }}>
                      <p style={{ color: '#ff9999', fontSize: '12px', margin: 0 }}>❌ {extendError}</p>
                    </div>
                  )}

                  {extendResult && (
                    <div style={{ marginTop: '10px', padding: '10px 12px', background: '#1f3a1f', borderRadius: '6px' }}>
                      <p style={{ color: '#99ff99', fontSize: '13px', margin: '0 0 6px', fontWeight: '600' }}>
                        ✅ Arc {extendResult.arc_number} — "{extendResult.arc_title}" deployed
                      </p>
                      <div style={{ fontSize: '12px', color: '#88cc88' }}>
                        {(extendResult.arc_data?.scenes || []).length} new scenes ·{' '}
                        {(extendResult.arc_data?.encounters || []).length} new encounters ·{' '}
                        {(extendResult.arc_data?.npcs || []).length} new NPCs
                      </div>
                      <button
                        className="btn btn-sm"
                        style={{ marginTop: '8px', fontSize: '12px' }}
                        onClick={() => { loadCampaign(selected) }}
                      >
                        🔄 Refresh campaign view
                      </button>
                    </div>
                  )}
                </div>

                {/* ── Optimize campaign panel ── */}
                <div style={{
                  marginTop: '12px',
                  padding: '14px',
                  background: 'var(--bg-tertiary)',
                  borderRadius: '8px',
                  border: '1px solid #3a5a7a',
                }}>
                  <h4 style={{ fontSize: '13px', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    ✨ Analyze & Optimize
                  </h4>
                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                    Analyze your campaign's structure and available modules to generate specific
                    enhancements for every scene, encounter, NPC, and narrative arc.
                  </p>
                  <button
                    className="btn btn-primary"
                    style={{ fontSize: '13px' }}
                    onClick={handleOptimize}
                    disabled={optimizing}
                  >
                    {optimizing ? '⏳ Analyzing campaign...' : '🔍 Analyze & Optimize'}
                  </button>

                  {optimizing && (
                    <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '10px' }}>
                      Analyzing campaign structure, discovering modules, mapping synergies... (this may take 1–2 minutes)
                    </p>
                  )}

                  {optimizeError && (
                    <div style={{ marginTop: '10px', padding: '8px 12px', background: '#3a1f1f', borderRadius: '6px' }}>
                      <p style={{ color: '#ff9999', fontSize: '12px', margin: 0 }}>❌ {optimizeError}</p>
                    </div>
                  )}

                  {optimizeResult && (
                    <div style={{ marginTop: '10px', padding: '10px 12px', background: '#1a2a2a', borderRadius: '6px' }}>
                      <p style={{ color: '#99ccff', fontSize: '13px', margin: '0 0 8px', fontWeight: '600' }}>
                        ✅ Campaign Analysis Complete
                      </p>
                      <div style={{ fontSize: '12px', color: '#88bbdd', marginBottom: '10px' }}>
                        <div style={{ marginBottom: '4px' }}>
                          📋 {optimizeResult.analysis?.scene_count || 0} scenes analyzed
                        </div>
                        <div style={{ marginBottom: '4px' }}>
                          ⚔️ {optimizeResult.analysis?.encounter_count || 0} encounters mapped
                        </div>
                        <div style={{ marginBottom: '4px' }}>
                          👥 {optimizeResult.analysis?.npc_count || 0} NPCs profiled
                        </div>
                        <div style={{ marginBottom: '4px' }}>
                          📚 {optimizeResult.modules?.total_installed || 0} modules discovered ({optimizeResult.modules?.enabled || 0} enabled)
                        </div>
                        <div>
                          🎯 {(optimizeResult.synergies?.scene_synergies || 0) + (optimizeResult.synergies?.encounter_synergies || 0) + (optimizeResult.synergies?.npc_synergies || 0)} module synergies identified
                        </div>
                      </div>

                      <button
                        className="btn btn-sm"
                        style={{ fontSize: '12px', marginBottom: '10px' }}
                        onClick={() => setShowOptimizeDetails(!showOptimizeDetails)}
                      >
                        {showOptimizeDetails ? '▼ Hide Details' : '▶ Show Details'}
                      </button>

                      {showOptimizeDetails && optimizeResult.enhancements && (
                        <div style={{ fontSize: '11px', color: '#99ccff', marginTop: '10px', maxHeight: '300px', overflowY: 'auto', background: '#0a1a2a', padding: '8px', borderRadius: '4px', border: '1px solid #2a4a6a' }}>
                          {optimizeResult.enhancements.scene_hooks && optimizeResult.enhancements.scene_hooks.length > 0 && (
                            <div style={{ marginBottom: '8px' }}>
                              <div style={{ fontWeight: '600', color: '#ccddff', marginBottom: '4px' }}>🎬 Scene Enhancements:</div>
                              {optimizeResult.enhancements.scene_hooks.slice(0, 2).map((hook, i) => (
                                <div key={i} style={{ marginBottom: '4px', paddingLeft: '12px', borderLeft: '2px solid #4a6a8a' }}>
                                  <div style={{ fontWeight: '500' }}>{hook.scene}</div>
                                  <div style={{ color: '#88aacc', fontSize: '10px' }}>
                                    {hook.hooks?.[0]?.hook?.substring(0, 100) || 'Enhancement available'}...
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}

                          {optimizeResult.enhancements.encounter_moments && optimizeResult.enhancements.encounter_moments.length > 0 && (
                            <div style={{ marginBottom: '8px' }}>
                              <div style={{ fontWeight: '600', color: '#ccddff', marginBottom: '4px' }}>⚔️ Encounter Enhancements:</div>
                              {optimizeResult.enhancements.encounter_moments.slice(0, 2).map((moment, i) => (
                                <div key={i} style={{ marginBottom: '4px', paddingLeft: '12px', borderLeft: '2px solid #4a6a8a' }}>
                                  <div style={{ fontWeight: '500' }}>{moment.encounter} (Drama: {moment.drama_level}/10)</div>
                                  <div style={{ color: '#88aacc', fontSize: '10px' }}>
                                    {moment.module_enhancements?.length || 0} module enhancements
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}

                          {optimizeResult.recommendations && optimizeResult.recommendations.length > 0 && (
                            <div>
                              <div style={{ fontWeight: '600', color: '#ccddff', marginBottom: '4px' }}>💡 Recommendations:</div>
                              {optimizeResult.recommendations.slice(0, 3).map((rec, i) => (
                                <div key={i} style={{ marginBottom: '4px', paddingLeft: '12px', borderLeft: '2px solid #6a8aaa', color: rec.priority === 'high' ? '#ffccaa' : '#88aacc' }}>
                                  <div style={{ fontWeight: '500' }}>[{rec.priority.toUpperCase()}] {rec.category}</div>
                                  <div style={{ fontSize: '10px' }}>{rec.action}</div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* ── Teardown panel ── */}
                <div style={{
                  marginTop: '12px',
                  padding: '14px',
                  background: '#2a1a1a',
                  borderRadius: '8px',
                  border: '1px solid #5a2a2a',
                }}>
                  <h4 style={{ fontSize: '13px', marginBottom: '6px', color: '#ffaaaa' }}>
                    🗑 Remove from World
                  </h4>
                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '10px' }}>
                    Deletes all scenes, actors, journals, loot tables, and playlists created
                    by this campaign from the connected FoundryVTT world.
                    Vault files and local assets are kept so you can re-deploy later.
                  </p>
                  <button
                    className="btn"
                    style={{ fontSize: '13px', borderColor: '#8b3333', color: '#ffaaaa', background: 'transparent' }}
                    onClick={handleTeardown}
                    disabled={tearingDown}
                  >
                    {tearingDown ? '⏳ Removing...' : '🗑 Remove Campaign from FoundryVTT'}
                  </button>

                  {teardownError && (
                    <div style={{ marginTop: '10px', padding: '8px 12px', background: '#3a1f1f', borderRadius: '6px' }}>
                      <p style={{ color: '#ff9999', fontSize: '12px', margin: 0 }}>❌ {teardownError}</p>
                    </div>
                  )}

                  {teardownResult && (
                    <div style={{ marginTop: '10px', padding: '10px 12px', background: '#1a2a1a', borderRadius: '6px' }}>
                      <p style={{ color: '#99ff99', fontSize: '13px', margin: '0 0 6px', fontWeight: '600' }}>
                        ✅ Removed from FoundryVTT
                      </p>
                      {(() => {
                        const fp = teardownResult.deleted?.flag_pass || {}
                        const up = teardownResult.deleted?.uuid_pass || {}
                        const total = Object.values({...fp,...up}).reduce((s,v) => s + (typeof v === 'number' ? v : 0), 0)
                        return (
                          <div style={{ fontSize: '12px', color: '#88cc88' }}>
                            {total} document{total !== 1 ? 's' : ''} deleted
                            {fp.scenes > 0 && ` · ${fp.scenes} scene${fp.scenes !== 1 ? 's' : ''}`}
                            {fp.actors > 0 && ` · ${fp.actors} actor${fp.actors !== 1 ? 's' : ''}`}
                            {fp.journal > 0 && ` · ${fp.journal} journal${fp.journal !== 1 ? 's' : ''}`}
                            {fp.tables > 0 && ` · ${fp.tables} table${fp.tables !== 1 ? 's' : ''}`}
                            {fp.playlists > 0 && ` · ${fp.playlists} playlist${fp.playlists !== 1 ? 's' : ''}`}
                          </div>
                        )
                      })()}
                      {teardownResult.errors?.length > 0 && (
                        <p style={{ color: '#ffbb88', fontSize: '11px', marginTop: '4px' }}>
                          ⚠️ {teardownResult.errors.join(' · ')}
                        </p>
                      )}
                    </div>
                  )}
                </div>
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
          💡 Tip: Use <strong>Extend Campaign</strong> when your party levels up to generate the next arc's content —
          new scenes, encounters, and NPCs tailored to their new power level, continuing the existing story.
        </p>
      </div>
    </div>
  )
}

export default CampaignList
