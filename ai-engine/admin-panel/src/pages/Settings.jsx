import React from 'react'
import { useStore } from '../store'

const Settings = () => {
  const {
    settings,
    setSetting,
    saveSettings,
    fetchStatus
  } = useStore()

  const handleSave = async () => {
    await saveSettings()
    await fetchStatus()
    alert('Settings saved!')
  }

  const models = [
    'anthropic/claude-sonnet-4-0721',
    'anthropic/claude-sonnet-4',
    'google/gemini-2.5-pro-preview-05-06',
    'openai/gpt-4o',
    'meta-llama/llama-3.3-70b-instruct'
  ]

  const tones = [
    'Descriptive, atmospheric, and player-centric',
    'Dramatic and cinematic',
    'Grimdark and serious',
    'Whimsical and lighthearted',
    'Minimalist and to the point',
    'Custom (type your own below)'
  ]

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>AI Settings</h2>
          <p>Configure the AI Gamemaster behavior and model</p>
        </div>
      </div>

      <div className="card" style={{ maxWidth: '600px' }}>
        <div className="form-group">
          <label>AI Model</label>
          <select
            className="select"
            value={settings.model}
            onChange={(e) => setSetting('model', e.target.value)}
          >
            {models.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>

        <div className="form-group">
          <label>Temperature</label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={settings.temperature}
            onChange={(e) => setSetting('temperature', parseFloat(e.target.value))}
            style={{ width: '100%' }}
          />
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            {settings.temperature} — Low={settings.temperature < 0.3 ? '✓' : ''} | 
            Medium={settings.temperature >= 0.3 && settings.temperature <= 0.7 ? '✓' : ''} | 
            High={settings.temperature > 0.7 ? '✓' : ''}
          </div>
        </div>

        <div className="form-group">
          <label>AI Name (appears in Foundry chat)</label>
          <input
            className="input"
            value={settings.aiName}
            onChange={(e) => setSetting('aiName', e.target.value)}
          />
        </div>

        <div className="form-group">
          <label>AI Tone</label>
          <select
            className="select"
            value={settings.aiTone}
            onChange={(e) => setSetting('aiTone', e.target.value)}
          >
            {tones.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>

        <div className="form-group">
          <label>Relay URL</label>
          <input
            className="input"
            value={settings.relayUrl}
            onChange={(e) => setSetting('relayUrl', e.target.value)}
          />
        </div>

        <div className="form-group">
          <label>Relay API Key</label>
          <input
            className="input"
            type="password"
            value={settings.relayApiKey}
            onChange={(e) => setSetting('relayApiKey', e.target.value)}
          />
        </div>

        <button className="btn btn-primary" onClick={handleSave}>
          Save Settings
        </button>
      </div>
    </div>
  )
}

export default Settings
