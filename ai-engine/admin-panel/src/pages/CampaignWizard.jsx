import { useState, useEffect } from 'react'
import { useStore } from '../store'

// Step indicator component
function StepIndicator({ currentStep, steps }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 32, gap: 0 }}>
      {steps.map((step, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center' }}>
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 32, height: 32, borderRadius: '50%',
            background: i < currentStep - 1 ? 'var(--success)' :
                        i === currentStep - 1 ? 'var(--accent)' : 'var(--bg-tertiary)',
            border: '2px solid ' + (i < currentStep ? 'var(--accent)' : 'var(--border)'),
            color: i < currentStep ? 'white' : 'var(--text-muted)',
            fontWeight: 600, fontSize: 14,
            transition: 'all 0.3s ease',
          }}>
            {i < currentStep - 1 ? '✓' : i + 1}
          </div>
          {i < steps.length - 1 && (
            <div style={{
              width: 48, height: 2,
              background: i < currentStep - 1 ? 'var(--accent)' : 'var(--border)',
              transition: 'all 0.3s ease',
            }} />
          )}
        </div>
      ))}
      <div style={{ position: 'absolute', top: 16, right: 24, fontSize: 11, color: 'var(--text-muted)' }}>
        Step {currentStep} of {steps.length}
      </div>
    </div>
  )
}

// Info Form Step
function InfoStep() {
  const { campaignWizard, setWizardField, setWizardStep } = useStore()

  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Campaign Details</h2>
      <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 24 }}>
        Tell the AI GM about your campaign. This information guides the AI in generating the world, NPCs, quests, and locations.
      </p>

      <div className="form-group">
        <label>Campaign Name *</label>
        <input
          className="input"
          value={campaignWizard.name}
          onChange={(e) => setWizardField('name', e.target.value)}
          placeholder="e.g. The Hollow Crown"
        />
      </div>

      <div className="form-group">
        <label>Description / Theme</label>
        <textarea
          className="textarea"
          value={campaignWizard.description}
          onChange={(e) => setWizardField('description', e.target.value)}
          rows={3}
          placeholder="e.g. A dark fantasy campaign set in a crumbling kingdom where the arcane monarchy has fallen and warring factions vie for control."
        />
      </div>

      <div className="form-row">
        <div className="form-group">
          <label>Tone</label>
          <select
            className="select"
            value={campaignWizard.theme}
            onChange={(e) => setWizardField('theme', e.target.value)}
          >
            <option value="">Select tone...</option>
            <option value="High fantasy">High fantasy (heroic, wondrous)</option>
            <option value="Dark fantasy">Dark fantasy (grim, perilous)</option>
            <option value="Gothic horror">Gothic horror (cursed, mysterious)</option>
            <option value="Murder mystery">Murder mystery (suspenseful, investigative)</option>
            <option value="Political intrigue">Political intrigue (scheming, betrayal)</option>
            <option value="Dungeon crawl">Dungeon crawl (combat-focused, treasure)</option>
            <option value="Exploration">Exploration (discovery, wilderness)</option>
            <option value="Survival">Survival (harsh, resourceful)</option>
          </select>
        </div>
        <div className="form-group">
          <label>Scale</label>
          <select
            className="select"
            value={campaignWizard.scale}
            onChange={(e) => setWizardField('scale', e.target.value)}
          >
            <option value="">Select scale...</option>
            <option value="One-shot">One-shot</option>
            <option value="Arc (3-5 sessions)">Short arc (3-5 sessions)</option>
            <option value="Campaign (6-12 sessions)">Campaign (6-12 sessions)</option>
            <option value="Epic (12+ sessions)">Epic (12+ sessions)</option>
          </select>
        </div>
      </div>

      <div className="form-group">
        <label>Seed Ideas &amp; Inspiration</label>
        <textarea
          className="textarea"
          value={campaignWizard.seedIdeas}
          onChange={(e) => setWizardField('seedIdeas', e.target.value)}
          rows={4}
          placeholder="e.g. 'Incorporate a haunted forest with sentient trees. The party starts in a ruined temple. Maybe a rogue AI that poses as a deity?'"
        />
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          Optional — any specific ideas, characters, or story beats you want included.
        </p>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
        <button className="btn" onClick={() => setWizardStep(1)}>← Back</button>
        <button
          className="btn btn-primary"
          onClick={() => setWizardStep(2)}
        >
          Continue →
        </button>
      </div>
    </div>
  )
}

// World Scan Step
function ScanStep() {
  const { campaignWizard, scanWorld, setWizardStep } = useStore()
  const [scanning, setScanning] = useState(false)

  const handleScan = async () => {
    setScanning(true)
    const result = await scanWorld()
    setScanning(false)
    if (result.ok) {
      // Move to build step
      setWizardStep(3)
    }
  }

  const scan = campaignWizard.scanWorld

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>World Analysis</h2>
      <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 24 }}>
        Scan the connected FoundryVTT world to catalog scenes, actors, items, journal entries, and available modules.
      </p>

      {!scan && !scanning && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <p style={{ marginBottom: 20, color: 'var(--text-secondary)' }}>
            Click below to scan the currently connected FoundryVTT world.
          </p>
          <button className="btn btn-primary" onClick={handleScan} disabled={scanning}>
            {scanning ? 'Scanning...' : 'Search Scan World'}
          </button>
        </div>
      )}

      {scanning && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ fontSize: 24, marginBottom: 12 }}>⏳</div>
          <p style={{ color: 'var(--text-secondary)' }}>Scanning world... this may take a minute.</p>
        </div>
      )}

      {scan && scan.status === 'ok' && (
        <div>
          {/* World summary card */}
          <div className="card" style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h3 style={{ fontSize: 16, fontWeight: 600 }}>{scan.world?.name || 'Unknown World'}</h3>
                <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                  {scan.world?.version && `v${scan.world.version} · `}
                  {scan.world?.systems?.length || 0} modules · {scan.world?.totalActors || 0} actors · {scan.world?.totalItems || 0} items
                </p>
              </div>
              <span className="badge badge-connected">Connected</span>
            </div>
          </div>

          {/* Scenes */}
          {scan.scenes?.length > 0 && (
            <div className="card" style={{ marginBottom: 16 }}>
              <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                🗺️ Scenes ({scan.scenes.length})
              </h4>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
                {scan.scenes.map((scene, i) => (
                  <div key={i} style={{
                    background: 'var(--bg-tertiary)', borderRadius: 6, padding: 10,
                    border: scene.active ? '1px solid var(--accent)' : '1px solid var(--border)',
                  }}>
                    <div style={{ fontSize: 13, fontWeight: 500 }}>{scene.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                      {scene.tokenCount} tokens · {scene.width}×{scene.height}
                    </div>
                    {scene.active && <span className="badge badge-running" style={{ marginTop: 4 }}>Active</span>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Actors */}
          {scan.actors?.length > 0 && (
            <div className="card" style={{ marginBottom: 16 }}>
              <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                👥 Actors ({scan.actors.length})
              </h4>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {scan.actors.slice(0, 20).map((actor, i) => (
                  <span key={i} style={{
                    background: 'var(--bg-tertiary)', padding: '3px 8px', borderRadius: 4,
                    fontSize: 12, border: '1px solid var(--border)',
                  }}>
                    {actor.name || 'Unnamed'}
                  </span>
                ))}
                {scan.actors.length > 20 && (
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    +{scan.actors.length - 20} more...
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Items */}
          {scan.items?.length > 0 && (
            <div className="card" style={{ marginBottom: 16 }}>
              <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
                🎒 Items ({scan.items.length})
              </h4>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {scan.items.slice(0, 20).map((item, i) => (
                  <span key={i} style={{
                    background: 'var(--bg-tertiary)', padding: '3px 8px', borderRadius: 4,
                    fontSize: 12, border: '1px solid var(--border)',
                  }}>
                    {item.name} ({item.type})
                  </span>
                ))}
                {scan.items.length > 20 && (
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    +{scan.items.length - 20} more...
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Modules */}
          {scan.modules?.length > 0 && (
            <div className="card" style={{ marginBottom: 16 }}>
              <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
                🧩 Modules ({scan.modules.length})
              </h4>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {scan.modules.map((mod, i) => (
                  <span key={i} style={{
                    background: 'var(--bg-tertiary)', padding: '3px 8px', borderRadius: 4,
                    fontSize: 12, border: '1px solid var(--border)',
                  }}>
                    {mod.name || mod.id}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Capabilities */}
          {scan.capabilities?.modules?.length > 0 && (
            <div className="card" style={{ marginBottom: 16 }}>
              <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
                ⚡ Detected Capabilities
              </h4>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {scan.capabilities.modules.map((mod, i) => (
                  <span key={i} style={{
                    background: 'var(--accent-dim)', padding: '3px 8px', borderRadius: 4,
                    fontSize: 12, color: 'var(--accent)', border: '1px solid rgba(107,92,231,0.3)',
                  }}>
                    {mod}
                  </span>
                ))}
              </div>
              {scan.capabilities.suggestions?.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Suggested add-ons to enhance this campaign:</p>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {scan.capabilities.suggestions.map((s, i) => (
                      <span key={i} style={{
                        background: 'var(--bg-tertiary)', padding: '3px 8px', borderRadius: 4,
                        fontSize: 12, border: '1px dashed var(--border)',
                      }}>
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Journal & Quests */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
            {scan.journal?.length > 0 && (
              <div className="card">
                <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>📜 Journal ({scan.journal.length})</h4>
                {scan.journal.slice(0, 5).map((j, i) => (
                  <div key={i} style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
                    {j.title || j.name || `Entry ${i + 1}`}
                  </div>
                ))}
              </div>
            )}
            {scan.quests?.length > 0 && (
              <div className="card">
                <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>⚔️ Quests ({scan.quests.length})</h4>
                {scan.quests.slice(0, 5).map((q, i) => (
                  <div key={i} style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
                    {q.name || q.title || `Quest ${i + 1}`}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
            <button className="btn" onClick={() => setWizardStep(1)}>← Back</button>
            <button className="btn btn-primary" onClick={handleScan}>
              🔄 Rescan
            </button>
          </div>
        </div>
      )}

      {scan && scan.status === 'error' && (
        <div className="card" style={{ border: '1px solid var(--danger)' }}>
          <h4 style={{ color: 'var(--danger)', fontSize: 14, marginBottom: 8 }}>Scan Failed</h4>
          <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>{scan.error}</p>
          <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={handleScan}>
            Retry
          </button>
        </div>
      )}
    </div>
  )
}

// Build Step
function BuildStep() {
  const { campaignWizard, buildCampaign } = useStore()
  const [building, setBuilding] = useState(false)
  const [log, setLog] = useState([])

  const handleBuild = async () => {
    setBuilding(true)
    setLog([{ time: new Date().toLocaleTimeString(), msg: 'Starting campaign build...' }])

    const result = await buildCampaign()

    setLog((prev) => [
      ...prev,
      { time: new Date().toLocaleTimeString(), msg: result.ok ? 'Build completed successfully!' : `Build failed: ${result.error}` }
    ])
    setBuilding(false)
  }

  const steps = campaignWizard.buildResult?.steps_completed || []

  return (
    <div style={{ maxWidth: 700, margin: '0 auto' }}>
      <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Generate Campaign</h2>
      <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 24 }}>
        The AI will generate a complete campaign: NPCs, locations, quests, journal entries, loot tables, scenes, and maps.
      </p>

      {/* Build summary */}
      <div className="card" style={{ marginBottom: 24 }}>
        <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>{campaignWizard.name}</h4>
        <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          {campaignWizard.description}
        </p>
        <div style={{ display: 'flex', gap: 8, marginTop: 8, fontSize: 12, color: 'var(--text-muted)' }}>
          {campaignWizard.theme && <span>Theme {campaignWizard.theme}</span>}
          {campaignWizard.scale && <span>📏 {campaignWizard.scale}</span>}
        </div>
      </div>

      {/* Pipeline */}
      <div className="card" style={{ marginBottom: 24 }}>
        <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>Pipeline</h4>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {[
            { name: 'Scan FoundryVTT World', icon: 'Search' },
            { name: 'Generate Campaign Data (NPCs, Quests, Loot)', icon: 'Notes' },
            { name: 'Create Vault Folder with Registry', icon: 'Folder' },
            { name: 'Generate Maps & Portraits', icon: 'Art' },
            { name: 'Deploy to FoundryVTT', icon: 'Rocket' },
          ].map((step, i) => {
            const done = i < (steps?.length || 0)
            const current = i === (steps?.length || 0) && building
            const pending = i > (steps?.length || 0)

            return (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px',
                background: current ? 'var(--accent-dim)' : 'transparent',
                borderLeft: done ? '3px solid var(--success)' :
                             current ? '3px solid var(--accent)' : '3px solid var(--border)',
                borderRadius: '0 4px 4px 0',
              }}>
                <span style={{ fontSize: 16 }}>{step.icon}</span>
                <span style={{ fontSize: 13, color: done ? 'var(--success)' : current ? 'var(--accent)' : 'var(--text-secondary)' }}>
                  {step.name}
                </span>
                {current && (
                  <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--accent)', display: 'flex', alignItems: 'center' }}>
                    <span style={{ animation: 'pulse 1s infinite', marginRight: 4 }}>*</span> Running
                  </span>
                )}
                {done && <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--success)' }}>✓</span>}
              </div>
            )
          })}
        </div>
      </div>

      {/* Build Log */}
      {building && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Build Log</h4>
          <div style={{
            background: 'var(--bg-primary)', padding: 12, borderRadius: 6,
            maxHeight: 200, overflowY: 'auto', fontSize: 12, fontFamily: 'monospace',
          }}>
            {log.map((entry, i) => (
              <div key={i} style={{ color: 'var(--text-secondary)', marginBottom: 2 }}>
                <span style={{ color: 'var(--text-muted)' }}>{entry.time}</span> {entry.msg}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Error */}
      {campaignWizard.buildError && (
        <div className="card" style={{ border: '1px solid var(--danger)', marginBottom: 24 }}>
          <h4 style={{ color: 'var(--danger)', fontSize: 14, marginBottom: 8 }}>Build Error</h4>
          <p style={{ color: 'var(--text-secondary)', fontSize: 13, whiteSpace: 'pre-wrap' }}>
            {campaignWizard.buildError}
          </p>
          <button className="btn btn-primary" style={{ marginTop: 8 }} onClick={handleBuild}>
            Retry Build
          </button>
        </div>
      )}

      {/* Generate button */}
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <button
          className="btn btn-primary"
          onClick={handleBuild}
          disabled={building || !!campaignWizard.buildResult}
          style={{ padding: '12px 32px', fontSize: 14 }}
        >
          {building ? '⏳ Building Campaign...' :
           campaignWizard.buildResult ? '✓ Build Complete' : 'Rocket Generate Campaign'}
        </button>
      </div>
    </div>
  )
}

// Results Step
function ResultsStep() {
  const { campaignWizard } = useStore()
  const result = campaignWizard.buildResult

  if (!result) return null

  const steps = result.steps_completed || []
  const generatedData = result.generated_data || {}
  const maps = result.maps_generated || []

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Campaign Generated</h2>
      <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 24 }}>
        Your campaign has been generated and is ready to start playing.
      </p>

      {/* Success banner */}
      <div className="card" style={{
        background: 'rgba(76, 175, 80, 0.1)', border: '1px solid rgba(76, 175, 80, 0.3)',
        marginBottom: 24, padding: '16px 20px', borderRadius: 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 28 }}>Done</span>
          <div>
            <h3 style={{ fontSize: 16, fontWeight: 600, color: 'var(--success)' }}>Campaign Ready</h3>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
              {steps.length} steps completed · {maps.length} maps generated
            </p>
          </div>
        </div>
      </div>

      {/* Progress */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Progress</span>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {result.progress || 0}/{result.total_steps || steps.length}
          </span>
        </div>
        <div style={{
          height: 6, background: 'var(--bg-tertiary)', borderRadius: 3, overflow: 'hidden',
        }}>
          <div style={{
            height: '100%', width: `${
              result.total_steps > 0
                ? (result.progress / result.total_steps) * 100
                : steps.length > 0
                  ? (steps.length / 5) * 100
                  : 0
            }%`,
            background: 'var(--accent)', borderRadius: 3,
            transition: 'width 0.5s ease',
          }} />
        </div>
      </div>

      {/* Generated NPCs */}
      {generatedData.npcs?.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>👥 NPCs ({generatedData.npcs.length})</h4>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {generatedData.npcs.slice(0, 10).map((npc, i) => (
              <div key={i} style={{
                background: 'var(--bg-tertiary)', padding: '8px 12px', borderRadius: 6,
                border: '1px solid var(--border)', maxWidth: 200,
              }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{npc.name}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{npc.role || npc.type || 'NPC'}</div>
              </div>
            ))}
            {generatedData.npcs.length > 10 && (
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>+{generatedData.npcs.length - 10} more</span>
            )}
          </div>
        </div>
      )}

      {/* Generated Locations */}
      {generatedData.locations?.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>📍 Locations ({generatedData.locations.length})</h4>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {generatedData.locations.slice(0, 10).map((loc, i) => (
              <div key={i} style={{
                background: 'var(--bg-tertiary)', padding: '8px 12px', borderRadius: 6,
                border: '1px solid var(--border)', maxWidth: 200,
              }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{loc.name}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{loc.type || 'Location'}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Generated Quests */}
      {generatedData.quests?.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>⚔️ Quests ({generatedData.quests.length})</h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {generatedData.quests.slice(0, 5).map((q, i) => (
              <div key={i} style={{
                background: 'var(--bg-tertiary)', padding: '8px 12px', borderRadius: 6,
                border: '1px solid var(--border)',
              }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{q.title || q.name}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                  {q.difficulty || q.level || 'TBD'} · {q.type || 'Main Quest'}
                </div>
                {q.description && (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                    {q.description.slice(0, 100)}{q.description.length > 100 ? '...' : ''}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Generated Maps */}
      {maps.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>🗺️ Generated Maps ({maps.length})</h4>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 10 }}>
            {maps.map((map, i) => (
              <div key={i} style={{
                background: 'var(--bg-tertiary)', borderRadius: 6, overflow: 'hidden',
                border: '1px solid var(--border)',
              }}>
                {map.image_url && (
                  <img src={map.image_url} alt={map.name} style={{
                    width: '100%', height: 100, objectFit: 'cover',
                  }} />
                )}
                <div style={{ padding: '6px 8px' }}>
                  <div style={{ fontSize: 12, fontWeight: 500 }}>{map.name || `Map ${i + 1}`}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{map.location || 'Generated'}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
        <button
          className="btn btn-primary"
          onClick={() => {
            // Navigate to campaign start
            const store = useStore.getState()
            store.setActivePage('campaign-start')
          }}
        >
          ▶ Start Session
        </button>
        <button
          className="btn"
          onClick={() => {
            const store = useStore.getState()
            store.setWizardStep(1)
            store.setActivePage('campaign-builder')
          }}
        >
          ← Back to Builder
        </button>
      </div>
    </div>
  )
}

// --- Main CampaignWizard Component ---

export default function CampaignWizard() {
  const { campaignWizard } = useStore()
  const steps = ['Info', 'World Scan', 'Build', 'Results']

  return (
    <div style={{ maxHeight: 'calc(100vh - 80px)', overflowY: 'auto' }}>
      <div style={{ maxWidth: 900, margin: '0 auto', paddingTop: 16 }}>
        <StepIndicator currentStep={campaignWizard.currentStep} steps={steps} />

        {campaignWizard.currentStep === 1 && <InfoStep />}
        {campaignWizard.currentStep === 2 && <ScanStep />}
        {campaignWizard.currentStep === 3 && <BuildStep />}
        {campaignWizard.currentStep === 4 && <ResultsStep />}
      </div>
    </div>
  )
}
