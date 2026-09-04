import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store.js'
import SpoilerWall from '../components/SpoilerWall.jsx'

const GMChat = () => {
  const { sendDirectGMMessage, gmChatMessages, gameState, engineStatus, playModeSessions, campaignSession } = useStore()

  const activeCampaign = campaignSession.activeSession?.campaign_name
  const isPlayModeActive = activeCampaign && playModeSessions[activeCampaign]
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const messagesEndRef = useRef(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [gmChatMessages])

  const handleSend = async () => {
    if (!input.trim()) return

    setLoading(true)
    try {
      await sendDirectGMMessage(input)
      setInput('')
    } catch (e) {
      console.error('Failed to send message:', e)
    } finally {
      setLoading(false)
    }
  }

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>Direct GM Chat</h2>
          <p>Ask the AI GM questions and get immediate responses</p>
        </div>
        {gameState?.mode === 'combat' && (
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <span className="badge badge-connected">⚔️ Combat Active</span>
            {gameState?.combat && (
              <>
                <span className="badge" style={{ backgroundColor: 'rgba(255, 152, 0, 0.1)', color: 'var(--text-primary)' }}>
                  Round {gameState.combat.round}
                </span>
                {engineStatus?.modules?.['midi-qol'] && (
                  <span className="badge" style={{ backgroundColor: 'rgba(76, 175, 80, 0.1)', color: 'var(--text-primary)' }}>
                    ⚙️ MIDI QOL
                  </span>
                )}
                {engineStatus?.modules?.['dae'] && (
                  <span className="badge" style={{ backgroundColor: 'rgba(76, 175, 80, 0.1)', color: 'var(--text-primary)' }}>
                    ✨ DAE
                  </span>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {isPlayModeActive ? (
        <SpoilerWall label="GM's unrevealed thoughts during play">
          <div className="card">
            <div className="gm-chat-container" style={{
              display: 'flex',
              flexDirection: 'column',
              height: '600px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: '8px',
              overflow: 'hidden',
            }}>
              {/* Messages Area */}
              <div className="gm-chat-messages" style={{
                flex: 1,
                overflowY: 'auto',
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '12px',
                backgroundColor: 'var(--bg-primary)',
              }}>
                {gmChatMessages && gmChatMessages.length > 0 ? (
                  gmChatMessages.map((msg, i) => (
                    <div
                      key={i}
                      className={`chat-message chat-message-${msg.role}`}
                      style={{
                        display: 'flex',
                        justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                        marginBottom: '8px',
                      }}
                    >
                      <div
                        style={{
                          maxWidth: '80%',
                          padding: '12px 16px',
                          borderRadius: '8px',
                          backgroundColor: msg.role === 'user' ? 'var(--accent)' : 'var(--bg-tertiary)',
                          color: msg.role === 'user' ? 'white' : 'var(--text-primary)',
                          wordWrap: 'break-word',
                        }}
                      >
                        {msg.content}
                      </div>
                    </div>
                  ))
                ) : (
                  <div style={{ textAlign: 'center', opacity: 0.6, marginTop: 'auto', marginBottom: 'auto' }}>
                    <p>Start a conversation with the AI GM</p>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Input Area */}
              <div style={{
                padding: '16px',
                backgroundColor: 'var(--bg-secondary)',
                borderTop: '1px solid var(--bg-tertiary)',
                display: 'flex',
                gap: '8px',
              }}>
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyPress={handleKeyPress}
                  placeholder="Ask the GM anything... (Shift+Enter for new line)"
                  disabled={loading}
                  style={{
                    flex: 1,
                    padding: '12px',
                    borderRadius: '6px',
                    border: '1px solid var(--bg-tertiary)',
                    backgroundColor: 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                    fontFamily: 'inherit',
                    resize: 'none',
                    maxHeight: '100px',
                    fontSize: '14px',
                  }}
                  rows="3"
                />
                <button
                  onClick={handleSend}
                  disabled={loading || !input.trim()}
                  className="btn btn-primary"
                  style={{
                    height: '100%',
                    minWidth: '80px',
                  }}
                >
                  {loading ? '...' : 'Send'}
                </button>
              </div>
            </div>
          </div>
        </SpoilerWall>
      ) : (
        <div className="card">
          <div className="gm-chat-container" style={{
            display: 'flex',
            flexDirection: 'column',
            height: '600px',
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: '8px',
            overflow: 'hidden',
          }}>
            {/* Messages Area */}
            <div className="gm-chat-messages" style={{
              flex: 1,
              overflowY: 'auto',
              padding: '16px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
              backgroundColor: 'var(--bg-primary)',
            }}>
              {gmChatMessages && gmChatMessages.length > 0 ? (
                gmChatMessages.map((msg, i) => (
                  <div
                    key={i}
                    className={`chat-message chat-message-${msg.role}`}
                    style={{
                      display: 'flex',
                      justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                      marginBottom: '8px',
                    }}
                  >
                    <div
                      style={{
                        maxWidth: '80%',
                        padding: '12px 16px',
                        borderRadius: '8px',
                        backgroundColor: msg.role === 'user' ? 'var(--accent)' : 'var(--bg-tertiary)',
                        color: msg.role === 'user' ? 'white' : 'var(--text-primary)',
                        wordWrap: 'break-word',
                      }}
                    >
                      {msg.content}
                    </div>
                  </div>
                ))
              ) : (
                <div style={{ textAlign: 'center', opacity: 0.6, marginTop: 'auto', marginBottom: 'auto' }}>
                  <p>Start a conversation with the AI GM</p>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input Area */}
            <div style={{
              padding: '16px',
              backgroundColor: 'var(--bg-secondary)',
              borderTop: '1px solid var(--bg-tertiary)',
              display: 'flex',
              gap: '8px',
            }}>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="Ask the GM anything... (Shift+Enter for new line)"
                disabled={loading}
                style={{
                  flex: 1,
                  padding: '12px',
                  borderRadius: '6px',
                  border: '1px solid var(--bg-tertiary)',
                  backgroundColor: 'var(--bg-primary)',
                  color: 'var(--text-primary)',
                  fontFamily: 'inherit',
                  resize: 'none',
                  maxHeight: '100px',
                  fontSize: '14px',
                }}
                rows="3"
              />
              <button
                onClick={handleSend}
                disabled={loading || !input.trim()}
                className="btn btn-primary"
                style={{
                  height: '100%',
                  minWidth: '80px',
                }}
              >
                {loading ? '...' : 'Send'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default GMChat
