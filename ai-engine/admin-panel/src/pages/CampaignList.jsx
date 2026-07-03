import React, { useState, useEffect } from 'react'
import { useStore, API_BASE } from '../store.js'
import { safeFetch } from '../fetch.js'

// ============================================================================
// THEME & STYLING CONFIGURATION - Single source of truth
// ============================================================================

const SPACING = {
  xs: '4px',
  sm: '6px',
  md: '8px',
  lg: '12px',
  xl: '16px',
  xxl: '24px',
}

const TYPOGRAPHY = {
  xs: '10px',
  sm: '11px',
  md: '12px',
  lg: '13px',
  xl: '14px',
  h3: '18px',
  h4: '13px',
}

const COLORS = {
  success: { bg: '#1a2a1a', border: '#2a4a2a', text: '#88cc88' },
  error: { bg: '#3a1f1f', border: '#6a3030', text: '#ff9999' },
  info: { bg: 'var(--bg-tertiary)', border: 'var(--bg-active)', text: 'var(--text-secondary)' },
  optimize: { bg: 'var(--bg-tertiary)', border: 'var(--bg-active)', text: '#88bbdd' },
  danger: { bg: '#2a1a1a', border: '#5a2a2a', text: '#ffaaaa' },
}

const THEME = {
  layout: {
    sidebarWidth: '240px',
    gap: SPACING.lg,
  },
  panel: {
    padding: SPACING.lg,
    borderRadius: '8px',
    marginTop: SPACING.lg,
  },
  heading: {
    fontSize: TYPOGRAPHY.h4,
    marginBottom: SPACING.md,
    fontWeight: '600',
    display: 'flex',
    alignItems: 'center',
    gap: SPACING.sm,
  },
  description: {
    fontSize: TYPOGRAPHY.md,
    color: 'var(--text-secondary)',
    marginBottom: SPACING.lg,
    lineHeight: '1.4',
  },
  section: {
    marginTop: SPACING.md,
    padding: `${SPACING.md} ${SPACING.lg}`,
    borderRadius: '6px',
    borderLeft: '3px solid',
  },
  badge: {
    fontSize: TYPOGRAPHY.xs,
    padding: `${SPACING.xs} ${SPACING.sm}`,
  },
}

// ============================================================================
// REUSABLE COMPONENTS
// ============================================================================

// Message/Alert component
const Alert = ({ type = 'error', message }) => {
  const color = COLORS[type]
  return (
    <div style={{
      ...THEME.section,
      background: color.bg,
      borderLeftColor: color.text,
      borderTop: `1px solid ${color.border}`,
      borderRight: `1px solid ${color.border}`,
      borderBottom: `1px solid ${color.border}`,
    }}>
      <p style={{ color: color.text, fontSize: TYPOGRAPHY.md, margin: 0 }}>
        {type === 'error' && '❌'} {type === 'success' && '✅'} {type === 'info' && 'ℹ️'} {message}
      </p>
    </div>
  )
}

// Stats grid component
const StatGrid = ({ stats, color }) => (
  <div style={{
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: SPACING.lg,
    marginBottom: SPACING.lg,
    fontSize: TYPOGRAPHY.md,
    color: color,
  }}>
    {stats.map((stat, i) => (
      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: SPACING.sm }}>
        <span>{stat.icon}</span>
        <span>{stat.label}</span>
      </div>
    ))}
  </div>
)

// Module badge list component
const ModuleList = ({ modules, color }) => {
  if (!modules || modules.length === 0) return null

  return (
    <div style={{ marginBottom: SPACING.lg }}>
      <p style={{
        fontSize: TYPOGRAPHY.sm,
        fontWeight: '600',
        color,
        marginBottom: SPACING.sm,
      }}>Discovered Modules:</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: SPACING.xs }}>
        {modules.slice(0, 8).map((mod, i) => (
          <span key={i} className="badge" style={{
            ...THEME.badge,
            background: mod.enabled ? 'var(--accent-dim)' : 'var(--bg-active)',
          }}>
            {mod.name.substring(0, 20)}
          </span>
        ))}
        {modules.length > 8 && (
          <span className="badge" style={THEME.badge}>
            +{modules.length - 8} more
          </span>
        )}
      </div>
    </div>
  )
}

// Action panel wrapper component
const ActionPanel = ({ icon, title, description, children, color = COLORS.info }) => (
  <div style={{
    ...THEME.panel,
    background: color.bg,
    border: `1px solid ${color.border}`,
  }}>
    <h4 style={{ ...THEME.heading, color: color.text }}>
      {icon} {title}
    </h4>
    <p style={THEME.description}>{description}</p>
    {children}
  </div>
)

// Results panel wrapper component
const ResultsPanel = ({ result, color, children }) => (
  <div style={{
    ...THEME.section,
    background: color.bg,
    borderLeftColor: color.text,
    borderTop: `1px solid ${color.border}`,
    borderRight: `1px solid ${color.border}`,
    borderBottom: `1px solid ${color.border}`,
  }}>
    {result && (
      <>
        <p style={{
          color: color.text,
          fontSize: TYPOGRAPHY.lg,
          margin: `0 0 ${SPACING.lg} 0`,
          fontWeight: '600',
        }}>
          ✅ {result.title}
        </p>
        {children}
      </>
    )}
  </div>
)

// ============================================================================
// MAIN COMPONENT
// ============================================================================

const CampaignList = () => {
  const { extendCampaignArc, teardownCampaign, restartCampaign } = useStore()
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [loadingCampaign, setLoadingCampaign] = useState(false)
  const [error, setError] = useState('')
  const [loadedData, setLoadedData] = useState(null)

  // Panel states - grouped by action
  const [extendState, setExtendState] = useState({
    level: 5,
    loading: false,
    result: null,
    error: '',
  })

  const [teardownState, setTeardownState] = useState({
    loading: false,
    result: null,
    error: '',
  })

  const [optimizeState, setOptimizeState] = useState({
    loading: false,
    result: null,
    error: '',
    showDetails: false,
  })

  const [restartState, setRestartState] = useState({
    loading: false,
    result: null,
    error: '',
  })

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
    setExtendState({ level: 5, loading: false, result: null, error: '' })
    setTeardownState({ loading: false, result: null, error: '' })
    setOptimizeState({ loading: false, result: null, error: '', showDetails: false })
    setRestartState({ loading: false, result: null, error: '' })

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
      if (data.error) {
        setError(data.error)
      } else {
        loadCampaigns()
        setSelected(null)
      }
    } catch (e) {
      setError(e.message)
    }
  }

  const handleTeardown = async () => {
    if (!selected) return
    if (!confirm(
      `Remove all AI-GM content for "${selected}" from FoundryVTT?\n\n` +
      `This will delete scenes, actors, journals, loot tables, and playlists created by this campaign.\n\n` +
      `This cannot be undone.`
    )) return

    setTeardownState(s => ({ ...s, loading: true, error: '', result: null }))
    const result = await teardownCampaign(selected)
    setTeardownState(s => ({
      ...s,
      loading: false,
      result: result.ok ? result.data : null,
      error: result.ok ? '' : (result.error || 'Teardown failed'),
    }))
  }

  const handleExtend = async () => {
    if (!selected) return
    setExtendState(s => ({ ...s, loading: true, error: '', result: null }))
    const result = await extendCampaignArc(selected, extendState.level)
    setExtendState(s => ({
      ...s,
      loading: false,
      result: result.ok ? result.data : null,
      error: result.ok ? '' : (result.error || 'Extension failed'),
    }))
  }

  const handleRestart = async () => {
    if (!selected) return
    if (!confirm(
      `Restart "${selected}" from the beginning?\n\n` +
      `This erases ALL session history (conversations, events, sessions), removes the campaign's content from FoundryVTT, and redeploys everything fresh — maps, portraits, walls, and tokens.\n\n` +
      `This cannot be undone.`
    )) return

    setRestartState(s => ({ ...s, loading: true, error: '', result: null }))
    const result = await restartCampaign(selected)
    setRestartState(s => ({
      ...s,
      loading: false,
      result: result.ok ? result.data : null,
      error: result.ok ? '' : (result.error || 'Restart failed'),
    }))
  }

  const handleOptimize = async () => {
    if (!selected) return
    setOptimizeState(s => ({ ...s, loading: true, error: '', result: null, showDetails: false }))
    try {
      const res = await fetch(`${API_BASE}/campaign/analyze-and-optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign_name: selected })
      })
      const data = await res.json()
      if (data.error || data.status === 'error') {
        setOptimizeState(s => ({ ...s, loading: false, error: data.error || 'Optimization failed' }))
      } else {
        setOptimizeState(s => ({ ...s, loading: false, result: data, showDetails: true }))
      }
    } catch (e) {
      setOptimizeState(s => ({ ...s, loading: false, error: e.message }))
    }
  }

  // Derive campaign metadata
  const campaignArcs = (() => {
    if (!loadedData) return []
    const d = loadedData.data || loadedData
    return (d.story_arcs || []).filter(a => a.arc_number > 0)
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

      {error && <Alert type="error" message={error} />}

      {!hasCampaigns && !loading && !error && (
        <div style={{ padding: SPACING.xxl, textAlign: 'center', color: 'var(--text-secondary)' }}>
          <p style={{ fontSize: TYPOGRAPHY.xl, marginBottom: SPACING.md }}>No campaigns found</p>
          <p style={{ fontSize: TYPOGRAPHY.lg }}>Build a new campaign to get started.</p>
        </div>
      )}

      {hasCampaigns && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: `${THEME.layout.sidebarWidth} 1fr`,
          gap: THEME.layout.gap,
        }}>
          {/* Campaign List Sidebar */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: SPACING.xs }}>
            {campaigns.map(c => (
              <div key={c.name} style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: `${SPACING.md} ${SPACING.lg}`,
                background: selected === c.name ? 'var(--bg-active)' : 'transparent',
                borderRadius: '6px',
                cursor: 'pointer',
                border: selected === c.name ? '1px solid var(--bg-active)' : '1px solid transparent',
                transition: 'all 0.2s ease',
              }}
                onClick={() => loadCampaign(c.name)}
              >
                <span style={{
                  fontSize: TYPOGRAPHY.xl,
                  fontWeight: selected === c.name ? '600' : '400',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}>
                  {c.name}
                </span>
                <button
                  className="btn btn-sm"
                  style={{
                    background: 'transparent',
                    border: 'none',
                    padding: SPACING.xs,
                    fontSize: TYPOGRAPHY.lg,
                    color: '#ff6666',
                    cursor: 'pointer',
                  }}
                  onClick={(e) => { e.stopPropagation(); deleteCampaign(c.name) }}
                  title="Delete campaign"
                  aria-label={`Delete campaign ${c.name}`}
                >
                  🗑
                </button>
              </div>
            ))}
          </div>

          {/* Campaign Details Panel */}
          <div className="card">
            {loadingCampaign ? (
              <p style={{ color: 'var(--text-secondary)' }}>Loading campaign...</p>
            ) : selected ? (
              <div>
                {/* Campaign Header */}
                <h3 style={{ fontSize: TYPOGRAPHY.h3, marginBottom: SPACING.md }}>{selected}</h3>
                {(() => {
                  if (!loadedData) return null
                  const d = loadedData.data || loadedData
                  const scenes = d.scenes || []
                  const encounters = d.encounters || []
                  const levelRange = d.campaign?.level_range || d.level_range || '?'

                  return (
                    <div>
                      {d.description && (
                        <p style={{ fontSize: TYPOGRAPHY.lg, color: 'var(--text-secondary)', marginBottom: SPACING.md }}>
                          {d.description}
                        </p>
                      )}

                      {/* Metadata Badges */}
                      <div style={{
                        display: 'flex',
                        gap: SPACING.lg,
                        marginBottom: SPACING.lg,
                        fontSize: TYPOGRAPHY.md,
                        flexWrap: 'wrap',
                      }}>
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

                      {/* Scenes List */}
                      {scenes.length > 0 && (
                        <div style={{ marginBottom: SPACING.lg }}>
                          <p style={{
                            fontSize: TYPOGRAPHY.md,
                            color: 'var(--text-secondary)',
                            marginBottom: SPACING.sm,
                            fontWeight: '600',
                          }}>SCENES</p>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: SPACING.xs }}>
                            {scenes.map((s, i) => (
                              <div key={i} style={{
                                fontSize: TYPOGRAPHY.md,
                                display: 'flex',
                                gap: SPACING.lg,
                                alignItems: 'center',
                              }}>
                                <span style={{ color: 'var(--text-secondary)', minWidth: '20px', fontWeight: '600' }}>
                                  {s.arc_number ? `A${s.arc_number}` : 'A1'}
                                </span>
                                <span>{s.name}</span>
                                {s.act && (
                                  <span style={{ color: 'var(--text-secondary)', fontSize: TYPOGRAPHY.sm }}>
                                    Act {s.act}
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })()}

                {/* ── Extend Arc Action Panel ── */}
                <ActionPanel
                  icon="➕"
                  title="Generate Next Arc"
                  description={`The AI will generate new scenes, NPCs, and encounters starting at the party's current level, picking up the story where Arc ${campaignArcs.length > 0 ? campaignArcs.length : 1} left off.`}
                  color={COLORS.info}
                >
                  <div style={{ display: 'flex', gap: SPACING.lg, alignItems: 'center', flexWrap: 'wrap' }}>
                    <label style={{
                      fontSize: TYPOGRAPHY.md,
                      color: 'var(--text-secondary)',
                      whiteSpace: 'nowrap',
                    }}>
                      Party's current level:
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      className="input"
                      style={{ width: '64px', fontSize: TYPOGRAPHY.lg }}
                      value={extendState.level}
                      onChange={(e) => setExtendState(s => ({
                        ...s,
                        level: Math.max(1, Math.min(20, parseInt(e.target.value) || 1))
                      }))}
                    />
                    <button
                      className="btn btn-primary"
                      style={{ fontSize: TYPOGRAPHY.lg }}
                      onClick={handleExtend}
                      disabled={extendState.loading}
                    >
                      {extendState.loading ? '⏳ Generating Arc...' : '🗺️ Extend Campaign'}
                    </button>
                  </div>

                  {extendState.loading && (
                    <p style={THEME.description}>
                      Generating new arc — this takes 2–5 minutes…
                    </p>
                  )}

                  {extendState.error && <Alert type="error" message={extendState.error} />}

                  {extendState.result && (
                    <ResultsPanel result={{ title: `Arc ${extendState.result.arc_number} — "${extendState.result.arc_title}" deployed` }} color={COLORS.success}>
                      <div style={{ fontSize: TYPOGRAPHY.md, color: COLORS.success.text, marginBottom: SPACING.lg }}>
                        {(extendState.result.arc_data?.scenes || []).length} new scenes ·{' '}
                        {(extendState.result.arc_data?.encounters || []).length} new encounters ·{' '}
                        {(extendState.result.arc_data?.npcs || []).length} new NPCs
                      </div>
                      <button
                        className="btn btn-sm"
                        style={{ fontSize: TYPOGRAPHY.md }}
                        onClick={() => loadCampaign(selected)}
                      >
                        🔄 Refresh
                      </button>
                    </ResultsPanel>
                  )}
                </ActionPanel>

                {/* ── Optimize Campaign Action Panel ── */}
                <ActionPanel
                  icon="✨"
                  title="Analyze & Enhance"
                  description="Analyze the campaign and apply scene enhancements directly: walls, doors, lights, ambient sounds, and fog/vision config on every deployed scene. Skips anything already in place."
                  color={COLORS.optimize}
                >
                  <button
                    className="btn btn-primary"
                    style={{ fontSize: TYPOGRAPHY.lg }}
                    onClick={handleOptimize}
                    disabled={optimizeState.loading}
                  >
                    {optimizeState.loading ? '⏳ Enhancing scenes...' : '✨ Analyze & Enhance'}
                  </button>

                  {optimizeState.loading && (
                    <p style={THEME.description}>
                      Placing walls, lights, and sounds on deployed scenes…
                    </p>
                  )}

                  {optimizeState.error && <Alert type="error" message={optimizeState.error} />}

                  {optimizeState.result && (
                    <ResultsPanel result={{ title: 'Enhancement Complete' }} color={COLORS.optimize}>
                      <StatGrid
                        stats={[
                          { icon: '📋', label: `${optimizeState.result.analysis?.scene_count || 0} scenes` },
                          { icon: '⚔️', label: `${optimizeState.result.analysis?.encounter_count || 0} encounters` },
                          { icon: '👥', label: `${optimizeState.result.analysis?.npc_count || 0} NPCs` },
                          { icon: '📚', label: `${optimizeState.result.modules?.enabled || 0} active modules` },
                          { icon: '🏗️', label: `${optimizeState.result.applied?.scenes_enriched || 0} scenes enhanced` },
                        ]}
                        color={COLORS.optimize.text}
                      />
                      {optimizeState.result.applied?.errors?.length > 0 && (
                        <p style={{ color: '#ffaa88', fontSize: TYPOGRAPHY.sm }}>
                          ⚠️ {optimizeState.result.applied.errors.slice(0, 3).join(' · ')}
                        </p>
                      )}

                      <ModuleList
                        modules={optimizeState.result.modules?.modules_list}
                        color={COLORS.optimize.text}
                      />

                      <button
                        className="btn btn-sm"
                        style={{ fontSize: TYPOGRAPHY.md, marginBottom: SPACING.lg }}
                        onClick={() => setOptimizeState(s => ({ ...s, showDetails: !s.showDetails }))}
                      >
                        {optimizeState.showDetails ? '▼ Hide Details' : '▶ Show Details'}
                      </button>

                      {optimizeState.showDetails && optimizeState.result.enhancements && (
                        <div style={{
                          fontSize: TYPOGRAPHY.sm,
                          marginTop: SPACING.lg,
                          background: 'rgba(0,0,0,0.2)',
                          padding: SPACING.lg,
                          borderRadius: '6px',
                          border: `1px solid rgba(136, 187, 221, 0.3)`,
                        }}>
                          {optimizeState.result.recommendations && optimizeState.result.recommendations.length > 0 && (
                            <div style={{ marginBottom: SPACING.lg }}>
                              <p style={{
                                fontSize: TYPOGRAPHY.md,
                                fontWeight: '600',
                                color: COLORS.optimize.text,
                                marginBottom: SPACING.md,
                              }}>💡 Top Recommendations:</p>
                              {optimizeState.result.recommendations.slice(0, 3).map((rec, i) => {
                                const priorityColor = rec.priority === 'high' ? '#ffccaa' : rec.priority === 'medium' ? COLORS.optimize.text : '#88aacc'
                                return (
                                  <div key={i} style={{
                                    marginBottom: SPACING.lg,
                                    paddingLeft: SPACING.lg,
                                    borderLeft: `2px solid ${priorityColor}`,
                                  }}>
                                    <div style={{
                                      fontWeight: '500',
                                      color: priorityColor,
                                      fontSize: TYPOGRAPHY.md,
                                      marginBottom: SPACING.xs,
                                    }}>
                                      [{rec.priority.toUpperCase()}] {rec.category}
                                    </div>
                                    <div style={{ color: COLORS.optimize.text, fontSize: TYPOGRAPHY.sm }}>
                                      {rec.action}
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          )}
                        </div>
                      )}
                    </ResultsPanel>
                  )}
                </ActionPanel>

                {/* ── Restart Action Panel ── */}
                <ActionPanel
                  icon="🔄"
                  title="Restart Campaign"
                  description="Erase all session history, remove this campaign's content from FoundryVTT, and redeploy it fresh from the vault — back to the opening scene with no AI memory of prior play."
                  color={COLORS.danger}
                >
                  <button
                    className="btn"
                    style={{
                      fontSize: TYPOGRAPHY.lg,
                      borderColor: '#8b3333',
                      color: COLORS.danger.text,
                      background: 'transparent',
                    }}
                    onClick={handleRestart}
                    disabled={restartState.loading}
                  >
                    {restartState.loading ? '⏳ Restarting...' : '🔄 Restart from Beginning'}
                  </button>

                  {restartState.error && <Alert type="error" message={restartState.error} />}

                  {restartState.result && (
                    <ResultsPanel result={{ title: 'Campaign Restarted' }} color={COLORS.success}>
                      <div style={{ fontSize: TYPOGRAPHY.md, color: COLORS.success.text, marginBottom: SPACING.lg }}>
                        {restartState.result.sessions_deleted || 0} session{restartState.result.sessions_deleted !== 1 ? 's' : ''} of history erased
                        {' · '}{restartState.result.scenes_deployed || 0} scenes redeployed
                        {' · '}{restartState.result.npcs_deployed || 0} NPCs redeployed
                        {' · '}{restartState.result.enrichment?.enriched || 0} scenes enriched
                      </div>
                      <p style={{ fontSize: TYPOGRAPHY.sm, color: 'var(--text-secondary)', margin: 0 }}>
                        Use Start Session to begin from the opening scene.
                      </p>
                    </ResultsPanel>
                  )}
                </ActionPanel>

                {/* ── Teardown Action Panel ── */}
                <ActionPanel
                  icon="🗑"
                  title="Remove from World"
                  description="Deletes all scenes, actors, journals, and playlists created by this campaign. Vault files are preserved."
                  color={COLORS.danger}
                >
                  <button
                    className="btn"
                    style={{
                      fontSize: TYPOGRAPHY.lg,
                      borderColor: '#8b3333',
                      color: COLORS.danger.text,
                      background: 'transparent',
                    }}
                    onClick={handleTeardown}
                    disabled={teardownState.loading}
                  >
                    {teardownState.loading ? '⏳ Removing...' : '🗑 Remove Campaign'}
                  </button>

                  {teardownState.error && <Alert type="error" message={teardownState.error} />}

                  {teardownState.result && (
                    <ResultsPanel result={{ title: 'Removed from FoundryVTT' }} color={COLORS.success}>
                      {(() => {
                        const fp = teardownState.result.deleted?.flag_pass || {}
                        const up = teardownState.result.deleted?.uuid_pass || {}
                        const total = Object.values({...fp, ...up}).reduce((s, v) => s + (typeof v === 'number' ? v : 0), 0)
                        return (
                          <div style={{ fontSize: TYPOGRAPHY.md, color: COLORS.success.text, marginBottom: SPACING.lg }}>
                            {total} document{total !== 1 ? 's' : ''} deleted
                            {fp.scenes > 0 && ` · ${fp.scenes} scene${fp.scenes !== 1 ? 's' : ''}`}
                            {fp.actors > 0 && ` · ${fp.actors} actor${fp.actors !== 1 ? 's' : ''}`}
                            {fp.journal > 0 && ` · ${fp.journal} journal${fp.journal !== 1 ? 's' : ''}`}
                            {fp.tables > 0 && ` · ${fp.tables} table${fp.tables !== 1 ? 's' : ''}`}
                            {fp.playlists > 0 && ` · ${fp.playlists} playlist${fp.playlists !== 1 ? 's' : ''}`}
                          </div>
                        )
                      })()}
                      {teardownState.result.errors?.length > 0 && (
                        <p style={{ color: '#ffaa88', fontSize: TYPOGRAPHY.sm, margin: 0 }}>
                          ⚠️ {teardownState.result.errors.join(' · ')}
                        </p>
                      )}
                    </ResultsPanel>
                  )}
                </ActionPanel>
              </div>
            ) : (
              <p style={{
                color: 'var(--text-secondary)',
                textAlign: 'center',
                padding: SPACING.xxl,
              }}>
                Select a campaign from the list to view its details
              </p>
            )}
          </div>
        </div>
      )}

      {/* Footer Tip */}
      <div style={{
        marginTop: SPACING.xxl,
        paddingTop: SPACING.lg,
        borderTop: '1px solid var(--bg-tertiary)',
      }}>
        <p style={{ fontSize: TYPOGRAPHY.lg, color: 'var(--text-secondary)', marginBottom: SPACING.lg }}>
          💡 <strong>Tip:</strong> Use <strong>Extend Campaign</strong> when your party levels up to generate the next arc's content.
        </p>
      </div>
    </div>
  )
}

export default CampaignList
