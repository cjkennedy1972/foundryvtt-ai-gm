import React from 'react'
import { useStore } from '../store.js'

const Settings = () => {
  const {
    settings,
    llmMode,
    setSetting,
    setLlmMode,
    saveSettings,
  } = useStore()

  const handleSave = async () => {
    await saveSettings()
    alert('Settings saved!\n\nNote: Changes to LLM Base URL or API Key require a server restart to take effect.')
  }

  const providers = [
    { id: 'anthropic', name: 'Anthropic (Claude)', base_url: 'https://api.anthropic.com/v1', models: ['claude-sonnet-4-0721', 'claude-sonnet-4', 'claude-3-5-sonnet-20241022', 'claude-3-opus-20240229'] },
    { id: 'google', name: 'Google (Gemini)', base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/', models: ['gemini-2.5-pro-preview-05-06', 'gemini-2.5-flash-preview-05-20', 'gemini-2.0-flash', 'gemini-1.5-pro'] },
    { id: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1', models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'] },
    { id: 'openrouter', name: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', models: ['anthropic/claude-sonnet-4-0721', 'anthropic/claude-sonnet-4', 'google/gemini-2.5-pro-preview-05-06', 'openai/gpt-4o', 'qwen/qwen-3-235b-a22b', 'qwen/qwen-3-30b-a3b'] },
    { id: 'local', name: 'Custom / Local (OpenAI-compatible)', base_url: '', models: [] },
  ]

  const currentProvider = providers.find(p => p.id === llmMode) || providers[0]

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>AI Settings</h2>
          <p>Configure the AI Gamemaster engine — local endpoints, commercial APIs, relay, and map generation</p>
        </div>
      </div>

      <div className="card" style={{ maxWidth: '700px' }}>
        {/* ── LLM Mode Toggle ── */}
        <div className="form-group">
          <label>LLM Provider Mode</label>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {providers.map(p => (
              <button
                key={p.id}
                className={`btn ${llmMode === p.id ? 'btn-primary' : 'btn-sm'}`}
                onClick={() => {
                  setLlmMode(p.id)
                  if (p.base_url) setSetting('llm_base_url', p.base_url)
                }}
                style={{
                  padding: '8px 16px',
                  borderRadius: '6px',
                  border: llmMode === p.id ? '2px solid var(--accent)' : '1px solid var(--bg-tertiary)',
                  background: llmMode === p.id ? 'var(--accent-dim)' : 'transparent',
                  cursor: 'pointer',
                  fontSize: '13px',
                }}
              >
                {p.name}
              </button>
            ))}
          </div>
        </div>

        {/* ── LLM Endpoint (local mode) ── */}
        <div className="form-group">
          <label>LLM Base URL <span style={{ fontSize: '11px', opacity: 0.6 }}>(OpenAI-compatible endpoint)</span></label>
          <input
            className="input"
            placeholder="http://localhost:8800/v1  or  http://192.168.1.100:8080/v1"
            value={settings.llm_base_url}
            onChange={(e) => setSetting('llm_base_url', e.target.value)}
          />
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            LocalAI, oMLX, vLLM, LM Studio, local Llama.cpp, etc. — must expose an OpenAI-compatible /v1/chat/completions endpoint.
          </div>
        </div>

        {/* ── LLM API Key ── */}
        <div className="form-group">
          <label>LLM API Key</label>
          <input
            className="input"
            type="password"
            placeholder="Your API key (leave empty for LocalAI/local endpoints)"
            value={settings.llm_api_key}
            onChange={(e) => setSetting('llm_api_key', e.target.value)}
          />
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            Required for cloud providers; omit for most local endpoints.
          </div>
        </div>

        {/* ── Model ── */}
        <div className="form-group">
          <label>Model</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            {currentProvider.models.length > 0 ? (
              <select
                className="select"
                value={settings.model}
                onChange={(e) => setSetting('model', e.target.value)}
                style={{ flex: 1 }}
              >
                {currentProvider.models.map(m => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            ) : (
              <input
                className="input"
                placeholder="Your custom model name (e.g. Qwen3.6-35B-A3B)"
                value={settings.model}
                onChange={(e) => setSetting('model', e.target.value)}
              />
            )}
          </div>
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            {currentProvider.models.length === 0
              ? 'Type your model name — it must match what your endpoint expects.'
              : 'Select a known model, or manually edit the field for a custom one.'
            }
          </div>
        </div>

        {/* ── Temperature ── */}
        <div className="form-group">
          <label>Temperature: {settings.temperature}</label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={settings.temperature}
            onChange={(e) => setSetting('temperature', parseFloat(e.target.value))}
            style={{ width: '100%' }}
          />
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            0 = deterministic & precise &nbsp;|&nbsp; 0.5 = balanced &nbsp;|&nbsp; 1 = creative &amp; varied
          </div>
        </div>

        <hr style={{ border: 'none', borderTop: '1px solid var(--bg-tertiary)', margin: '24px 0' }} />

        {/* ── AI Persona ── */}
        <h3 style={{ fontSize: '14px', marginBottom: '16px' }}>AI Persona</h3>

        <div className="form-group">
          <label>AI Name <span style={{ fontSize: '11px', opacity: 0.6 }}>(appears in Foundry chat)</span></label>
          <input
            className="input"
            value={settings.aiName}
            onChange={(e) => setSetting('aiName', e.target.value)}
            placeholder="Aethelwyrd GM"
          />
        </div>

        <div className="form-group">
          <label>AI Tone</label>
          <textarea
            className="textarea"
            rows={3}
            value={settings.aiTone}
            onChange={(e) => setSetting('aiTone', e.target.value)}
            placeholder="mysterious, immersive, high fantasy"
          />
        </div>

        <hr style={{ border: 'none', borderTop: '1px solid var(--bg-tertiary)', margin: '24px 0' }} />

        {/* ── Relay Settings ── */}
        <h3 style={{ fontSize: '14px', marginBottom: '16px' }}>Relay (FoundryVTT Connection)</h3>

        <div className="form-group">
          <label>Relay URL</label>
          <input
            className="input"
            value={settings.relayUrl}
            onChange={(e) => setSetting('relayUrl', e.target.value)}
            placeholder="http://localhost:3010"
          />
        </div>

        <div className="form-group">
          <label>Relay API Key</label>
          <input
            className="input"
            type="password"
            value={settings.relayApiKey}
            onChange={(e) => setSetting('relayApiKey', e.target.value)}
            placeholder="Auto-provisioned when relay_managed is true"
          />
        </div>

        <hr style={{ border: 'none', borderTop: '1px solid var(--bg-tertiary)', margin: '24px 0' }} />

        {/* ── ComfyUI (Map Generation) ── */}
        <h3 style={{ fontSize: '14px', marginBottom: '16px' }}>ComfyUI (Campaign Map Generation)</h3>

        <div className="form-group">
          <label>ComfyUI URL</label>
          <input
            className="input"
            value={settings.comfyuiUrl}
            onChange={(e) => setSetting('comfyuiUrl', e.target.value)}
            placeholder="http://127.0.0.1:18188"
          />
        </div>

        <div style={{ marginTop: '24px' }}>
          <button className="btn btn-primary" onClick={handleSave}>
            Save Settings
          </button>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '8px' }}>
            ⚠️ Changes to <strong>LLM Base URL</strong> or <strong>LLM API Key</strong> require a server restart to take effect. Other changes apply immediately.
          </p>
        </div>
      </div>
    </div>
  )
}

export default Settings
