import React, { useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import { useStore } from '../store.js'

const SessionViewer = () => {
  const { events, fetchEvents, fetchState, gameState, aiRunning } = useStore()

  useEffect(() => {
    fetchEvents(100)
    fetchState()
  }, [])

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>Session Viewer</h2>
          <p>View game session events and AI actions</p>
        </div>
        <button className="btn btn-sm" onClick={() => fetchEvents(100)}>
          ↻ Refresh
        </button>
      </div>

      {aiRunning && (
        <div style={{ marginBottom: '16px' }}>
          <span className="badge badge-running">AI Active — listening to player messages in Foundry</span>
        </div>
      )}

      <div className="card">
        {events && events.length > 0 ? (
          <div className="event-log">
            {events.map((evt, i) => (
              <div key={i} className="event-entry">
                <span className="timestamp">{evt.timestamp || ''}</span>
                <span className="message">{evt.description}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <p>No session events yet. Start a game session in FoundryVTT to see activity here.</p>
          </div>
        )}
      </div>
    </div>
  )
}

export default SessionViewer
