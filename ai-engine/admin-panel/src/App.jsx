import React, { useEffect } from 'react'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import SessionViewer from './pages/SessionViewer'
import CampaignBuilder from './pages/CampaignBuilder'
import CampaignList from './pages/CampaignList'
import Overrides from './pages/Overrides'
import { useStore } from './store.js'

const Sidebar = () => {
  const { activePage, setActivePage } = useStore()

  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: '📊' },
    { id: 'settings', label: 'AI Settings', icon: '⚙️' },
    { id: 'session', label: 'Session Viewer', icon: '📜' },
    { id: 'campaigns', label: 'Campaigns', icon: '📚' },
    { id: 'campaign-builder', label: 'Build New', icon: '🏗️' },
    { id: 'overrides', label: 'GM Overrides', icon: '🎮' },
  ]

  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <h1>⚔️ Aethelwyrd AI</h1>
        <p>AI Gamemaster Engine</p>
      </div>
      <div className="sidebar-nav">
        {navItems.map((item) => (
          <div
            key={item.id}
            className={`nav-item ${activePage === item.id ? 'active' : ''}`}
            onClick={() => setActivePage(item.id)}
          >
            <span>{item.icon}</span>
            {item.label}
          </div>
        ))}
      </div>

      {/* Relay Admin Link */}
      <div style={{ borderTop: '1px solid var(--bg-tertiary)', paddingTop: '12px', marginTop: '8px' }}>
        <div className="nav-item" style={{ cursor: 'pointer' }}>
          <a
            href="http://localhost:13010"
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
    </nav>
  )
}

const App = () => {
  const { activePage, fetchStatus, fetchState, fetchSettings } = useStore()

  useEffect(() => {
    fetchStatus()
    fetchState()
    fetchSettings()
  }, [fetchStatus, fetchState, fetchSettings])

  const renderPage = () => {
    switch (activePage) {
      case 'dashboard': return <Dashboard />
      case 'settings': return <Settings />
      case 'session': return <SessionViewer />
      case 'campaigns': return <CampaignList />
      case 'campaign-builder': return <CampaignBuilder />
      case 'overrides': return <Overrides />
      default: return <Dashboard />
    }
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
