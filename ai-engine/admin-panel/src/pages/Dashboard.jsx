import React, { useEffect, useState } from 'react'
import { useStore } from '../store.js'

const Dashboard = () => {
  const {
    engineStatus,
    gameState,
    events,
    aiRunning,
    wsConnected,
    fetchStatus,
    fetchState,
    fetchEvents,
    connectWS,
    pauseAI,
    resumeAI,
    relayStart,
    relayStop,
    relayRestart,
  } = useStore()

  const [relayBusy, setRelayBusy] = useState(false)
  const [relayMsg, setRelayMsg] = useState(null)

  const handleRelay = async (action) => {
    setRelayBusy(true)
    setRelayMsg(null)
    try {
      const fns = { start: relayStart, stop: relayStop, restart: relayRestart }
      const result = await fns[action]()
      if (result?.error) setRelayMsg({ type: 'error', text: result.error })
      else setRelayMsg({ type: 'ok', text: `Relay ${action}ed` })
    } catch (e) {
      setRelayMsg({ type: 'error', text: e.message })
    } finally {
      setRelayBusy(false)
      setTimeout(() => setRelayMsg(null), 4000)
    }
  }

  useEffect(() => {
    fetchStatus()
    fetchState()
    fetchEvents()
    connectWS()

    const interval = setInterval(() => {
      fetchStatus()
      fetchState()
    }, 10000)

    return () => clearInterval(interval)
  }, [])

  if (!engineStatus) {
    return (
      <div className="loading">
        <div>Connecting to engine...</div>
      </div>
    )
  }

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>Dashboard</h2>
          <p>Real-time overview of the AI Gamemaster engine</p>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span className={`badge ${engineStatus.connected ? 'badge-connected' : 'badge-disconnected'}`}>
            {engineStatus.connected ? 'Connected to Foundry' : 'Disconnected'}
          </span>
          <span className={`badge ${engineStatus.relay?.running ? 'badge-connected' : 'badge-disconnected'}`}>
            {engineStatus.relay?.crashed ? 'Relay Crashed' : engineStatus.relay?.running ? 'Relay Up' : 'Relay Down'}
          </span>
          <span className={`badge ${wsConnected ? 'badge-connected' : 'badge-disconnected'}`}>
            {wsConnected ? 'WS Live' : 'WS Offline'}
          </span>
          <span className={`badge ${aiRunning ? 'badge-running' : 'badge-disconnected'}`}>
            {aiRunning ? 'AI Active' : 'AI Paused'}
          </span>
        </div>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="label">AI Model</div>
          <div className="value" style={{ fontSize: '14px' }}>{engineStatus.model || 'Not configured'}</div>
        </div>
        <div className="stat-card">
          <div className="label">Campaign</div>
          <div className="value">{gameState?.campaign || 'Loading...'}</div>
        </div>
        <div className="stat-card">
          <div className="label">Session</div>
          <div className="value">#{gameState?.session_number || 0}</div>
        </div>
        <div className="stat-card">
          <div className="label">Mode</div>
          <div className="value" style={{ textTransform: 'capitalize' }}>{gameState?.mode || 'exploration'}</div>
        </div>
        <div className="stat-card">
          <div className="label">Scene</div>
          <div className="value" style={{ fontSize: '13px' }}>{gameState?.current_scene || 'None'}</div>
        </div>
        <div className="stat-card">
          <div className="label">Context Window</div>
          <div className="value">{engineStatus.conversation_length} messages</div>
        </div>
        <div className="stat-card">
          <div className="label">Relay</div>
          <div className="value" style={{ fontSize: '13px' }}>
            {engineStatus.relay?.dashboard_url ? (
              <a href={engineStatus.relay.dashboard_url} target="_blank" rel="noreferrer">
                Open Relay Dashboard ↗
              </a>
            ) : 'Not managed'}
            {engineStatus.relay?.restarts > 0 && ` (${engineStatus.relay.restarts} restarts)`}
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: '16px' }}>
        <h3 style={{ margin: '0 0 12px', fontSize: '14px', fontWeight: 600 }}>Active Modules</h3>
        {engineStatus?.modules && Object.keys(engineStatus.modules).length > 0 ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '8px' }}>
            {Object.entries(engineStatus.modules).map(([modId, modInfo]) => (
              <div key={modId} className="module-badge" style={{
                padding: '8px 12px',
                backgroundColor: 'rgba(76, 175, 80, 0.1)',
                border: '1px solid rgba(76, 175, 80, 0.3)',
                borderRadius: '4px',
                fontSize: '12px',
              }}>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{modId}</div>
                {modInfo.title && <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>{modInfo.title}</div>}
                {modInfo.version && <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>v{modInfo.version}</div>}
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-state" style={{ padding: '12px', textAlign: 'center' }}>
            <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-muted)' }}>No modules detected</p>
          </div>
        )}
      </div>

      <div className="card" style={{ marginBottom: '16px' }}>
        <h3 style={{ margin: '0 0 12px', fontSize: '14px', fontWeight: 600 }}>Service Controls</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: 500 }}>AI Engine</div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="btn" onClick={pauseAI} disabled={!aiRunning}>⏸ Pause</button>
              <button className="btn btn-primary" onClick={resumeAI} disabled={aiRunning}>▶ Resume</button>
            </div>
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: 500 }}>
              Relay
              {engineStatus?.relay?.adopted && <span style={{ marginLeft: '6px', opacity: 0.6 }}>(external — controls disabled)</span>}
            </div>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <button
                className="btn btn-primary"
                onClick={() => handleRelay('start')}
                disabled={relayBusy || engineStatus?.relay?.running || engineStatus?.relay?.adopted}
              >
                ▶ Start
              </button>
              <button
                className="btn"
                onClick={() => handleRelay('stop')}
                disabled={relayBusy || !engineStatus?.relay?.running || engineStatus?.relay?.adopted}
              >
                ⏹ Stop
              </button>
              <button
                className="btn"
                onClick={() => handleRelay('restart')}
                disabled={relayBusy || engineStatus?.relay?.adopted}
              >
                {relayBusy ? '...' : '↺ Restart'}
              </button>
              {relayMsg && (
                <span style={{ fontSize: '12px', color: relayMsg.type === 'error' ? 'var(--danger)' : 'var(--success)' }}>
                  {relayMsg.text}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="section-header">
          <div>
            <h2 style={{ fontSize: '15px' }}>Recent Activity</h2>
          </div>
        </div>
        {events && events.length > 0 ? (
          <div className="event-log">
            {events.slice(-20).reverse().map((evt, i) => (
              <div key={i} className="event-entry">
                <span className="timestamp">{evt.timestamp || ''}</span>
                <span className="message">{evt.description}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <p>No events recorded yet</p>
          </div>
        )}
      </div>
    </div>
  )
}

export default Dashboard
