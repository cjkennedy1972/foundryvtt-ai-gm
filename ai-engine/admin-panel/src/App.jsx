import React, { useEffect } from 'react'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import SessionViewer from './pages/SessionViewer'
import CampaignWizard from './pages/CampaignWizard'
import CampaignStart from './pages/CampaignStart'
import NPCManager from './pages/NPCManager'
import Overrides from './pages/Overrides'
import { useStore } from './store.js'

const Sidebar = () => {
  const { activePage, setActivePage } = useStore()

  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: '📊' },
    { id: 'settings', label: 'AI Settings', icon: '⚙️' },
    { id: 'session', label: 'Session Viewer', icon: '📜' },
    { id: 'campaign', label: 'Campaign Wizard', icon: '🗺️' },
    { id: 'campaign-start', label: 'Campaign Start', icon: '▶️' },
    { id: 'npcs', label: 'NPC Manager', icon: '🧙' },
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
    </nav>
  )
}

const App = () => {
  const { activePage } = useStore()

  const renderPage = () => {
    switch (activePage) {
      case 'dashboard': return <Dashboard />
      case 'settings': return <Settings />
      case 'session': return <SessionViewer />
      case 'campaign': return <CampaignWizard />
      case 'campaign-start': return <CampaignStart />
      case 'npcs': return <NPCManager />
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
