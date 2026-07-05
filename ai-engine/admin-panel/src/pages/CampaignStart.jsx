import { useState, useEffect } from 'react'
import { useStore } from '../store'
import { useAction } from '../hooks/useAction.js'

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

  const handleDeploy = async (name) => {
    await deployCampaign(name)
  }

  const handleStartSession = async (name, continueFromLast = false) => {
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
                  onClick={() => handleStartSession(activeSession.campaign_name, true)}
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

        {/* Campaigns Section */}
        <div className="section-header">
          <div>
            <h2>📚 Campaigns</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4 }}>
              {activeSession
                ? 'Start a different campaign (will end the current session)'
                : 'Deploy, launch, and manage the campaigns you\'ve built.'}
            </p>
          </div>
          <button className="btn" onClick={() => { fetchActiveSession(); listCampaigns() }}>
            🔄 Refresh
          </button>
        </div>

        {error && <Alert type="error" message={error} />}

        {loading && campaigns.length === 0 && (
          <div className="loading">Loading campaigns…</div>
        )}

        {!loading && campaigns.length === 0 && (
          <div className="empty-state">
            <div style={{ fontSize: 48, marginBottom: 12 }}>📁</div>
            <p>No campaigns found.</p>
            <p style={{ fontSize: 12, marginTop: 4, color: 'var(--text-muted)' }}>
              Use Create Campaign to build a new campaign first.
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
              onDeploy={(name) => handleDeploy(name)}
              onStartSession={(name) => handleStartSession(name, false)}
              onContinue={(name) => handleStartSession(name, true)}
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

// Campaign card component — deploy/launch actions plus (when expanded)
// the world-content lifecycle actions: extend arc, analyze & enhance,
// restart, remove from world.
function CampaignCard({
  campaign,
  loading,
  deleteConfirm,
  onDeploy,
  onStartSession,
  onContinue,
  onDeleteRequest,
  onDeleteCancel,
  onDeleteConfirm,
}) {
  const { getCampaign, regenerateAssets, extendCampaignArc, teardownCampaign, restartCampaign, optimizeCampaign } = useStore()
  const [expanded, setExpanded] = useState(false)
  const [details, setDetails] = useState(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [regen, setRegen] = useState(null)

  const [extendLevel, setExtendLevel] = useState(5)
  const [extendState, runExtend, resetExtend] = useAction()
  const [teardownState, runTeardown, resetTeardown] = useAction()
  const [optimizeState, runOptimize, resetOptimize, patchOptimize] = useAction({ showDetails: false })
  const [restartState, runRestart, resetRestart] = useAction()

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

  const handleExtend = async () => {
    await runExtend(() => extendCampaignArc(name, extendLevel), { fallbackError: 'Extension failed' })
  }

  const handleOptimize = async () => {
    patchOptimize({ showDetails: false })
    const result = await runOptimize(() => optimizeCampaign(name), { fallbackError: 'Optimization failed' })
    if (result.ok) patchOptimize({ showDetails: true })
  }

  const handleRestart = async () => {
    if (!confirm(
      `Restart "${name}" from the beginning?\n\n` +
      `This erases ALL session history (conversations, events, sessions), removes the campaign's content from FoundryVTT, and redeploys everything fresh — maps, portraits, walls, and tokens.\n\n` +
      `This cannot be undone.`
    )) return
    await runRestart(() => restartCampaign(name), { fallbackError: 'Restart failed' })
  }

  const handleTeardown = async () => {
    if (!confirm(
      `Remove all AI-GM content for "${name}" from FoundryVTT?\n\n` +
      `This will delete scenes, actors, journals, loot tables, and playlists created by this campaign.\n\n` +
      `This cannot be undone.`
    )) return
    await runTeardown(() => teardownCampaign(name), { fallbackError: 'Teardown failed' })
  }

  // story arc count, derived from the full campaign data (loaded into `details.data`)
  const campaignArcs = (() => {
    const d = details?.data || details
    if (!d) return []
    return (d.story_arcs || []).filter(a => a.arc_number > 0)
  })()

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
            onClick={() => onDeploy(name)}
            title="Push campaign content to FoundryVTT without starting the GM session"
          >
            {loading ? '⏳' : '📦 Deploy'}
          </button>
          <button
            className="btn"
            disabled={loading}
            onClick={() => onContinue(name)}
            title="Resume GM session from where you left off"
          >
            ▶ Continue
          </button>
          <button
            className="btn btn-primary"
            disabled={loading}
            onClick={() => onStartSession(name)}
            title="Start a new GM session (deploy first if needed)"
          >
            {loading ? '⏳' : '🎮 Start GM'}
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
              {(regen.scenes_attached > 0 || regen.portraits_attached > 0) && `, attached ${regen.scenes_attached} scenes and ${regen.portraits_attached || 0} NPCs`}.
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
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 16 }}>
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
            <div style={{ marginBottom: 16 }}>
              <div className="label">Vault Status</div>
              <span className="badge badge-connected" style={{ marginTop: 4 }}>{details.status}</span>
            </div>
          )}

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
                value={extendLevel}
                onChange={(e) => setExtendLevel(Math.max(1, Math.min(20, parseInt(e.target.value) || 1)))}
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
                  onClick={handleViewDetails}
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
                  onClick={() => patchOptimize({ showDetails: !optimizeState.showDetails })}
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
                  Use Start GM to begin from the opening scene.
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
                  const total = Object.values({ ...fp, ...up }).reduce((s, v) => s + (typeof v === 'number' ? v : 0), 0)
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
