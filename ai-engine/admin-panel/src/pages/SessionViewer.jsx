import React, { useEffect } from 'react'
import { useStore } from '../store.js'
import SpoilerWall from '../components/SpoilerWall.jsx'

const SessionViewer = () => {
  const { events, fetchEvents, fetchState, gameState, aiRunning, interactiveSessions, fetchInteractiveSessions, playModeSessions, campaignSession } = useStore()

  const activeCampaign = campaignSession.activeSession?.campaign_name
  const isPlayModeActive = activeCampaign && playModeSessions[activeCampaign]

  useEffect(() => {
    fetchEvents(100)
    fetchState()
    fetchInteractiveSessions()
  }, [])

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>Session Viewer</h2>
          <p>View game session events, AI actions, and live relay connections</p>
        </div>
        <button className="btn btn-sm" onClick={() => {
          fetchEvents(100)
          fetchInteractiveSessions()
        }}>
          ↻ Refresh
        </button>
      </div>

      {aiRunning && (
        <div style={{ marginBottom: '16px' }}>
          <span className="badge badge-running">AI Active — listening to player messages in Foundry</span>
        </div>
      )}

      {isPlayModeActive ? (
        <SpoilerWall label="live session state and events">
          <>
            {/* Interactive Sessions */}
            <div className="card">
              <div className="card-header">
                <h3>Active Interactive Sessions</h3>
              </div>
              {interactiveSessions && interactiveSessions.length > 0 ? (
                <div className="session-list">
                  {interactiveSessions.map((session) => (
                    <div key={session.sessionId} className="session-item">
                      <div className="session-info">
                        <div className="session-id"><strong>Session:</strong> {session.sessionId}</div>
                        <div className="session-state">
                          <span className={`badge badge-${session.state === 'active' ? 'active' : 'pending'}`}>
                            {session.state}
                          </span>
                        </div>
                        <div className="session-detail"><small>Client: {session.clientId}</small></div>
                        <div className="session-detail"><small>Consumer: {session.consumerId}</small></div>
                        <div className="session-detail"><small>Created: {new Date(session.createdAt).toLocaleString()}</small></div>
                        <div className="session-detail"><small>Last Activity: {new Date(session.lastActivity).toLocaleString()}</small></div>
                        {session.quality && <div className="session-detail"><small>Quality: {session.quality}</small></div>}
                        {session.scale && <div className="session-detail"><small>Scale: {session.scale}</small></div>}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty-state">
                  <p>No active interactive sessions.</p>
                </div>
              )}
            </div>

            {/* Session Events */}
            <div className="card">
              <div className="card-header">
                <h3>Session Events</h3>
              </div>
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
          </>
        </SpoilerWall>
      ) : (
        <>
          {/* Interactive Sessions */}
          <div className="card">
            <div className="card-header">
              <h3>Active Interactive Sessions</h3>
            </div>
            {interactiveSessions && interactiveSessions.length > 0 ? (
              <div className="session-list">
                {interactiveSessions.map((session) => (
                  <div key={session.sessionId} className="session-item">
                    <div className="session-info">
                      <div className="session-id"><strong>Session:</strong> {session.sessionId}</div>
                      <div className="session-state">
                        <span className={`badge badge-${session.state === 'active' ? 'active' : 'pending'}`}>
                          {session.state}
                        </span>
                      </div>
                      <div className="session-detail"><small>Client: {session.clientId}</small></div>
                      <div className="session-detail"><small>Consumer: {session.consumerId}</small></div>
                      <div className="session-detail"><small>Created: {new Date(session.createdAt).toLocaleString()}</small></div>
                      <div className="session-detail"><small>Last Activity: {new Date(session.lastActivity).toLocaleString()}</small></div>
                      {session.quality && <div className="session-detail"><small>Quality: {session.quality}</small></div>}
                      {session.scale && <div className="session-detail"><small>Scale: {session.scale}</small></div>}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <p>No active interactive sessions.</p>
              </div>
            )}
          </div>

          {/* Session Events */}
          <div className="card">
            <div className="card-header">
              <h3>Session Events</h3>
            </div>
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
        </>
      )}
    </div>
  )
}

export default SessionViewer
