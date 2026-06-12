import React from 'react'
import { useStore } from '../store.js'

const Overrides = () => {
  const {
    chatTest,
    rollForm,
    rollResult,
    srdQuery,
    srdResults,
    testChat,
    performRoll,
    searchSrd,
    setChatTest,
    setRollForm,
    setSrdQuery,
    setSrdResults,
    aiRunning,
    pauseAI,
    resumeAI
  } = useStore()

  const rollTemplates = [
    '1d20', '2d6', '4d8+3', '8d6', '1d4', '1d100', '1d20+5', '2d20 advantage'
  ]

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>GM Overrides</h2>
          <p>Test the AI, roll dice manually, and search SRD rules</p>
        </div>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="label">AI Status</div>
          <div className="value">
            <span className={`badge ${aiRunning ? 'badge-connected' : 'badge-disconnected'}`}>
              {aiRunning ? 'Active' : 'Paused'}
            </span>
          </div>
          <div style={{ marginTop: '8px' }}>
            {aiRunning ? (
              <button className="btn btn-sm btn-danger" onClick={pauseAI}>⏸ Pause AI</button>
            ) : (
              <button className="btn btn-sm" onClick={resumeAI}>▶ Resume AI</button>
            )}
          </div>
        </div>

        <div className="stat-card">
          <div className="label">AI Name in Foundry</div>
          <div className="value" style={{ fontSize: '14px' }}>
            {localStorage.getItem('aiName') || 'Aethelwyrd AI'}
          </div>
        </div>

        <div className="stat-card">
          <div className="label">WebSocket</div>
          <div className="value" style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
            ws://localhost:3010/ws/api
          </div>
        </div>
      </div>

      {/* Chat test */}
      <div className="card" style={{ marginBottom: '16px' }}>
        <h3 style={{ fontSize: '14px', marginBottom: '12px' }}>🎤 Test AI Chat Response</h3>
        <div className="form-row">
          <div>
            <label style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Player Name</label>
            <input
              className="input"
              placeholder="Player name"
              value={chatTest.speaker}
              onChange={(e) => setChatTest({ speaker: e.target.value })}
            />
          </div>
          <div>
            <label style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Message</label>
            <input
              className="input"
              placeholder="Type what the player says..."
              value={chatTest.message}
              onChange={(e) => setChatTest({ message: e.target.value })}
              onKeyDown={(e) => e.key === 'Enter' && testChat()}
            />
          </div>
        </div>
        <div style={{ marginTop: '8px' }}>
          <button className="btn btn-primary" onClick={testChat} disabled={chatTest.loading}>
            {chatTest.loading ? 'Processing...' : 'Send to AI'}
          </button>
        </div>
        {chatTest.result && (
          <div style={{ marginTop: '12px', padding: '12px', background: 'var(--bg-primary)', borderRadius: '6px' }}>
            <pre style={{ fontSize: '12px', whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>
              {JSON.stringify(chatTest.result, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Manual dice roll */}
      <div className="card" style={{ marginBottom: '16px' }}>
        <h3 style={{ fontSize: '14px', marginBottom: '12px' }}>🎲 Manual Dice Roll</h3>
        <div className="form-row">
          <div>
            <label style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Formula</label>
            <input
              className="input"
              value={rollForm.formula}
              onChange={(e) => setRollForm('formula', e.target.value)}
            />
          </div>
          <div>
            <label style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Speaker</label>
            <input
              className="input"
              value={rollForm.speaker}
              onChange={(e) => setRollForm('speaker', e.target.value)}
            />
          </div>
        </div>
        <div style={{ marginTop: '8px' }}>
          <div style={{ display: 'flex', gap: '6px', marginBottom: '8px' }}>
            {rollTemplates.map(t => (
              <button key={t} className="btn btn-sm" onClick={() => setRollForm('formula', t)}>
                {t}
              </button>
            ))}
          </div>
          <button className="btn" onClick={performRoll}>
            Roll
          </button>
        </div>
        {rollResult && (
          <div style={{ marginTop: '12px', padding: '12px', background: 'var(--bg-primary)', borderRadius: '6px' }}>
            <pre style={{ fontSize: '12px', whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(rollResult, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* SRD Search */}
      <div className="card">
        <h3 style={{ fontSize: '14px', marginBottom: '12px' }}>📖 SRD Search</h3>
        <div style={{ display: 'flex', gap: '8px' }}>
          <input
            className="input"
            placeholder="Search rules (e.g., 'Spell Slots', 'Stealth')"
            value={srdQuery}
            onChange={(e) => setSrdQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && searchSrd()}
            style={{ flex: 1 }}
          />
          <button className="btn" onClick={searchSrd}>Search</button>
        </div>
        {srdResults && (
          <div style={{ marginTop: '12px', padding: '12px', background: 'var(--bg-primary)', borderRadius: '6px' }}>
            <pre style={{ fontSize: '12px', whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>
              {srdResults}
            </pre>
          </div>
        )}
      </div>
    </div>
  )
}

export default Overrides
