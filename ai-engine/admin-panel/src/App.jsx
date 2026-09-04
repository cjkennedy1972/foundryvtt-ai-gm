import React, { useEffect, useState } from 'react'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import SessionViewer from './pages/SessionViewer'
import GMChat from './pages/GMChat'
import CampaignBuilder from './pages/CampaignBuilder'
import CampaignStart from './pages/CampaignStart'
import NPCManager from './pages/NPCManager'
import CanonReview from './pages/CanonReview'
import Overrides from './pages/Overrides'
import SetupWizard from './pages/SetupWizard'
import { useStore, API_BASE } from './store.js'
import { relayAdminUrl } from './config.js'

// Grouped by the phase of running a session: get oriented, build/manage a
// campaign, run a live session, occasional dev tooling, one-time setup.
const NAV_SECTIONS = [
  {
    label: null,
    items: [{ id: 'dashboard', label: 'Dashboard', icon: '📊' }],
  },
  {
    label: 'Campaigns',
    items: [
      { id: 'campaign-builder', label: 'Create Campaign', icon: '🏗️' },
      { id: 'campaign-start', label: 'Campaigns', icon: '📚' },
    ],
  },
  {
    label: 'Live Session',
    items: [
      { id: 'gm-chat', label: 'GM Chat', icon: '💬' },
      { id: 'session', label: 'Live Session', icon: '📜' },
      { id: 'npcs', label: 'NPC Manager', icon: '🧙' },
      { id: 'canon-review', label: 'Canon Review', icon: '📖' },
    ],
  },
  {
    label: 'Tools',
    items: [{ id: 'overrides', label: 'Dev Tools', icon: '🎮' }],
  },
  {
    label: 'Settings',
    items: [{ id: 'settings', label: 'AI Settings', icon: '⚙️' }],
  },
]

const SPOILER_PAGES = new Set(['gm-chat', 'session', 'npcs', 'canon-review', 'overrides'])

const Sidebar = () => {
  const { activePage, setActivePage, playModeSessions, campaignSession } = useStore()

  const activeCampaign = campaignSession.activeSession?.campaign_name
  const isPlayModeActive = activeCampaign && playModeSessions[activeCampaign]

  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <h1>⚔️ Aethelwyrd AI</h1>
        <p>AI Gamemaster Engine</p>
      </div>
      <div className="sidebar-nav">
        {NAV_SECTIONS.map((section, i) => (
          <div key={section.label || `section-${i}`} className="nav-section">
            {section.label && <div className="nav-section-label">{section.label}</div>}
            {section.items.map((item) => {
              const isSpoilerPage = SPOILER_PAGES.has(item.id)
              const showEmphasis = isSpoilerPage && isPlayModeActive

              return (
                <button
                  key={item.id}
                  type="button"
                  className={`nav-item ${activePage === item.id ? 'active' : ''}`}
                  aria-current={activePage === item.id ? 'page' : undefined}
                  onClick={() => setActivePage(item.id)}
                  style={showEmphasis ? {
                    opacity: 0.6,
                    fontSize: '12px',
                  } : undefined}
                  title={showEmphasis ? '🛡️ Spoiler content' : undefined}
                >
                  <span>{item.icon}</span>
                  {item.label}
                </button>
              )
            })}
          </div>
        ))}
      </div>

      {/* Play Mode Toggle */}
      {activeCampaign && (
        <div style={{ borderTop: '1px solid var(--bg-tertiary)', paddingTop: '12px', marginTop: '8px' }}>
          <button
            type="button"
            onClick={() => {
              const { setPlayMode } = useStore.getState()
              setPlayMode(activeCampaign, !isPlayModeActive)
            }}
            style={{
              display: 'block',
              width: '100%',
              padding: '10px 12px',
              borderRadius: '6px',
              border: isPlayModeActive ? '1px solid var(--accent)' : '1px solid var(--bg-active)',
              background: isPlayModeActive ? 'rgba(255, 152, 0, 0.15)' : 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              font: 'inherit',
              fontSize: '12px',
              cursor: 'pointer',
              textAlign: 'left',
              transition: 'all 0.2s',
            }}
          >
            {isPlayModeActive ? '🛡️ Play Mode: ON' : '▫️ Play Mode: OFF'}
          </button>
          {isPlayModeActive && (
            <p style={{
              fontSize: '11px',
              color: 'var(--text-secondary)',
              margin: '6px 0 0',
              lineHeight: '1.3',
            }}>
              Spoiler surfaces are hidden. Click to toggle.
            </p>
          )}
        </div>
      )}

      {/* Relay Admin Link — configurable via VITE_RELAY_ADMIN_URL env var */}
      {relayAdminUrl() && (
        <div style={{ borderTop: '1px solid var(--bg-tertiary)', paddingTop: '12px', marginTop: '8px' }}>
          <div className="nav-item" style={{ cursor: 'pointer' }}>
            <a
              href={relayAdminUrl()}
              target="_blank"
              rel="noreferrer"
              style={{
                color: 'var(--text-primary)',
                textDecoration: 'none',
                fontSize: '13px',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              🔗 Relay Admin <span style={{ fontSize: '10px', opacity: 0.6 }}>↗</span>
            </a>
          </div>
        </div>
      )}
    </nav>
  )
}

const App = () => {
  const { activePage, fetchStatus, fetchState, fetchSettings } = useStore()
  const [setupComplete, setSetupComplete] = useState(null)

  useEffect(() => {
    const checkSetup = async () => {
      try {
        const response = await fetch(`${API_BASE}/setup/status`)
        const data = await response.json()
        setSetupComplete(data.complete)
      } catch (e) {
        console.error('Failed to check setup status:', e)
        setSetupComplete(true) // Assume complete if check fails
      }
    }

    checkSetup()
    fetchStatus()
    fetchState()
    fetchSettings()
  }, [fetchStatus, fetchState, fetchSettings])

  const renderPage = () => {
    switch (activePage) {
      case 'dashboard': return <Dashboard />
      case 'settings': return <Settings />
      case 'gm-chat': return <GMChat />
      case 'session': return <SessionViewer />
      case 'campaign-builder': return <CampaignBuilder />
      case 'campaign-start': return <CampaignStart />
      case 'npcs': return <NPCManager />
      case 'canon-review': return <CanonReview />
      case 'overrides': return <Overrides />
      default: return <Dashboard />
    }
  }

  if (setupComplete === null) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 32, marginBottom: 16 }}>⏳</div>
          <p style={{ color: 'var(--text-secondary)' }}>Checking setup...</p>
        </div>
      </div>
    )
  }

  if (!setupComplete) {
    return <SetupWizard />
  }

  return (
    <div className="app-layout">
      <Sidebar />
      <main className="main-content">
        {renderPage()}
      </main>
    </div>
  )
}

export default App
