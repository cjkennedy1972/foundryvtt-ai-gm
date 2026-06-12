import React, { useEffect } from 'react'
import { useStore } from '../store.js'

const Dashboard = () => {
  const {
    engineStatus,
    gameState,
    events,
    aiRunning,
    setEngineStatus,
    setGameState,
    setEvents,
    setAiRunning,
    fetchStatus,
    fetchState,
    fetchEvents
  } = useStore()

  useEffect(() => {
    fetchStatus()
    fetchState()
    fetchEvents()

    // Poll for updates
    const interval = setInterval(() => {
      fetchStatus()
      fetchState()
    }, 5000)

    // WebSocket for real-time events
    const ws = new WebSocket('ws://localhost:8000/admin/ws')
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (data.type === 'ai_paused') {
        setAiRunning(false)
      } else if (data.type === 'ai_resumed') {
        setAiRunning(true)
      }
    }

    return () => {
      clearInterval(interval)
      ws.close()
    }
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
          <div className="value">{gameState?.campaign || 'Aethelwyrd'}</div>
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
      </div>

      {/* Recent Events */}
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
