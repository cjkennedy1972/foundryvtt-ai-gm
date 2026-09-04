import { useState, useEffect } from 'react'

// Step 1: Welcome
function WelcomeStep({ onNext }) {
  return (
    <div style={{ maxWidth: 600, margin: '0 auto', textAlign: 'center' }}>
      <div style={{ fontSize: 48, marginBottom: 24 }}>🧙</div>
      <h1 style={{ fontSize: 32, fontWeight: 600, marginBottom: 16 }}>Welcome to AI Gamemaster</h1>
      <p style={{ fontSize: 16, color: 'var(--text-secondary)', marginBottom: 32, lineHeight: 1.6 }}>
        This wizard will set up your AI-driven D&D game master in just a few minutes.
        We'll configure your LLM, set up the relay, and get you ready to play.
      </p>
      <button className="btn btn-primary" onClick={onNext} style={{ padding: '12px 32px', fontSize: 16 }}>
        Get Started →
      </button>
    </div>
  )
}

// Step 2: LLM Configuration
function LLMConfigStep({ onNext, onBack }) {
  const [baseUrl, setBaseUrl] = useState('http://localhost:8800/v1')
  const [apiKey, setApiKey] = useState('')
  const [models, setModels] = useState([])
  const [selectedModel, setSelectedModel] = useState('')
  const [probing, setProbing] = useState(false)
  const [error, setError] = useState('')

  const handleProbe = async () => {
    setProbing(true)
    setError('')
    try {
      const response = await fetch('/api/setup/probe-llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: baseUrl.trim(),
          api_key: apiKey.trim(),
        }),
      })
      const data = await response.json()
      if (data.healthy && data.models) {
        setModels(data.models)
        if (data.models.length > 0) {
          setSelectedModel(data.models[0].id)
        }
      } else {
        setError(data.message || 'Failed to probe LLM endpoint')
      }
    } catch (e) {
      setError(e.message)
    }
    setProbing(false)
  }

  const canContinue = selectedModel && !error
  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 16 }}>LLM Configuration</h2>
      <p style={{ color: 'var(--text-secondary)', marginBottom: 24 }}>
        Connect to your LLM endpoint (oMLX, OpenRouter, or any OpenAI-compatible API).
      </p>

      <div className="form-group">
        <label>LLM Base URL</label>
        <input
          className="input"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="http://localhost:8800/v1"
        />
      </div>

      <div className="form-group">
        <label>API Key</label>
        <input
          className="input"
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="Your LLM API key"
        />
      </div>

      {error && (
        <div style={{ background: 'rgba(244, 67, 54, 0.1)', border: '1px solid rgba(244, 67, 54, 0.3)', borderRadius: 6, padding: 12, marginBottom: 16, color: 'var(--danger)', fontSize: 14 }}>
          {error}
        </div>
      )}

      <button
        className="btn btn-primary"
        onClick={handleProbe}
        disabled={!apiKey.trim() || probing}
        style={{ width: '100%', marginBottom: 16 }}
      >
        {probing ? '⏳ Probing...' : '🔍 List Available Models'}
      </button>

      {models.length > 0 && (
        <div className="form-group">
          <label>Select Model</label>
          <select
            className="select"
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name || m.id}
              </option>
            ))}
          </select>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
        <button className="btn" onClick={onBack}>← Back</button>
        <button
          className="btn btn-primary"
          onClick={() => onNext({ baseUrl, apiKey, selectedModel })}
          disabled={!canContinue}
        >
          Continue →
        </button>
      </div>
    </div>
  )
}

// Step 3: Relay & Pairing
function RelayConfigStep({ onNext, onBack }) {
  const [starting, setStarting] = useState(false)
  const [relayReady, setRelayReady] = useState(false)
  const [pairingCode, setPairingCode] = useState('')
  const [dashboardUrl, setDashboardUrl] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const startRelay = async () => {
      setStarting(true)
      try {
        const response = await fetch('/api/setup/start-wizard', { method: 'POST' })
        const data = await response.json()
        if (data.status === 'ok') {
          setRelayReady(true)
          setDashboardUrl(data.dashboard_url)
          // Fetch pairing code
          const codeResp = await fetch('/api/setup/pairing-code')
          const codeData = await codeResp.json()
          setPairingCode(codeData.code)
        }
      } catch (e) {
        setError(e.message)
      }
      setStarting(false)
    }
    startRelay()
  }, [])

  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 16 }}>Relay & Pairing</h2>

      {!relayReady && (
        <div style={{ textAlign: 'center', padding: '48px 24px' }}>
          <div style={{ fontSize: 32, marginBottom: 16 }}>⚙️</div>
          <p style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
            {starting ? 'Starting relay...' : 'Initializing relay'}
          </p>
          {error && <div style={{ color: 'var(--danger)', fontSize: 14 }}>{error}</div>}
        </div>
      )}

      {relayReady && (
        <div>
          <p style={{ color: 'var(--text-secondary)', marginBottom: 24 }}>
            The relay is running. Next, you'll pair your FoundryVTT world with this AI GM instance.
          </p>

          <div className="card" style={{ marginBottom: 24, background: 'rgba(76, 175, 80, 0.1)', border: '1px solid rgba(76, 175, 80, 0.3)' }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Pairing Code</h3>
            <div style={{
              background: 'var(--bg-primary)',
              padding: 12,
              borderRadius: 4,
              fontFamily: 'monospace',
              fontSize: 12,
              wordBreak: 'break-all',
              marginBottom: 12,
            }}>
              {pairingCode}
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Keep this handy. You'll paste it into your Foundry module settings.
            </p>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Next Steps:</h4>
            <ol style={{ fontSize: 13, lineHeight: 1.8, color: 'var(--text-secondary)', paddingLeft: 20 }}>
              <li>Open <a href={dashboardUrl} target="_blank" rel="noopener noreferrer">the relay dashboard</a></li>
              <li>Log in with credentials shown in the relay terminal</li>
              <li>Your Foundry world should appear in "Interactive Sessions"</li>
              <li>Install the "AI Gamemaster" module in your world</li>
              <li>Paste the pairing code above into Settings → Modules → AI Gamemaster → Pairing Code</li>
              <li>Save and reload</li>
            </ol>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
            <button className="btn" onClick={onBack}>← Back</button>
            <button className="btn btn-primary" onClick={onNext}>
              Continue →
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// Step 4: Campaign Configuration
function CampaignConfigStep({ onNext, onBack }) {
  const [campaignName, setCampaignName] = useState('My Campaign')
  const [vaultPath, setVaultPath] = useState('~/Vaults/MyStuff/Dungeons_and_Dragons')
  const [aiName, setAiName] = useState('Sage')
  const [aiTone, setAiTone] = useState('mysterious, immersive, high fantasy')

  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 16 }}>Campaign Settings</h2>

      <div className="form-group">
        <label>Campaign Name (optional)</label>
        <input
          className="input"
          value={campaignName}
          onChange={(e) => setCampaignName(e.target.value)}
          placeholder="My Campaign"
        />
      </div>

      <div className="form-group">
        <label>Campaign Vault Path</label>
        <input
          className="input"
          value={vaultPath}
          onChange={(e) => setVaultPath(e.target.value)}
          placeholder="~/Vaults/MyStuff/Dungeons_and_Dragons"
        />
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          Directory where campaign notes and lore are stored.
        </p>
      </div>

      <div className="form-group">
        <label>GM Name</label>
        <input
          className="input"
          value={aiName}
          onChange={(e) => setAiName(e.target.value)}
          placeholder="Sage"
        />
      </div>

      <div className="form-group">
        <label>GM Tone & Personality</label>
        <textarea
          className="textarea"
          value={aiTone}
          onChange={(e) => setAiTone(e.target.value)}
          rows={3}
          placeholder="mysterious, immersive, high fantasy"
        />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
        <button className="btn" onClick={onBack}>← Back</button>
        <button className="btn btn-primary" onClick={() => onNext({ campaignName, vaultPath, aiName, aiTone })}>
          Complete Setup →
        </button>
      </div>
    </div>
  )
}

// Step 5: Complete
function CompleteStep({ config }) {
  const [reloading, setReloading] = useState(false)

  const handleReload = () => {
    setReloading(true)
    setTimeout(() => window.location.reload(), 2000)
  }

  if (reloading) {
    return (
      <div style={{ maxWidth: 600, margin: '0 auto', textAlign: 'center' }}>
        <div style={{ fontSize: 48, marginBottom: 24 }}>⚡</div>
        <h2 style={{ fontSize: 28, fontWeight: 600, marginBottom: 16 }}>Loading...</h2>
        <p style={{ fontSize: 16, color: 'var(--text-secondary)' }}>
          Your configuration is being applied. Restarting in a moment...
        </p>
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 600, margin: '0 auto', textAlign: 'center' }}>
      <div style={{ fontSize: 48, marginBottom: 24 }}>✨</div>
      <h2 style={{ fontSize: 28, fontWeight: 600, marginBottom: 16, color: 'var(--success)' }}>Setup Complete!</h2>
      <p style={{ fontSize: 16, color: 'var(--text-secondary)', marginBottom: 32, lineHeight: 1.6 }}>
        Your AI Gamemaster is ready to run your campaign. Your configuration has been saved and all credentials are provisioned.
      </p>
      <button
        className="btn btn-primary"
        onClick={handleReload}
        style={{ padding: '12px 32px', fontSize: 16 }}
      >
        ▶ Start Building Campaign
      </button>
    </div>
  )
}

// Main SetupWizard Component
export default function SetupWizard() {
  const [step, setStep] = useState(1)
  const [config, setConfig] = useState({})
  const [saving, setSaving] = useState(false)

  const handleLLMNext = (llmConfig) => {
    setConfig(llmConfig)
    setStep(2)
  }

  const handleRelayNext = () => {
    setStep(3)
  }

  const handleCampaignNext = async (campaignConfig) => {
    setSaving(true)
    try {
      const response = await fetch('/api/setup/write-env', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          llm_api_key: config.apiKey,
          llm_base_url: config.baseUrl,
          model: config.selectedModel,
          campaign_vault_path: campaignConfig.vaultPath,
          ai_name: campaignConfig.aiName,
          ai_tone: campaignConfig.aiTone,
        }),
      })
      const data = await response.json()
      if (data.status === 'ok') {
        // Provision the relay scoped key for security
        await fetch('/api/setup/provision-relay-scoped-key', { method: 'POST' })
        setConfig({ ...config, ...campaignConfig })
        setStep(5)
      }
    } catch (e) {
      console.error('Failed to write .env:', e)
    }
    setSaving(false)
  }

  const steps = ['Welcome', 'LLM', 'Relay', 'Campaign', 'Complete']
  const stepIndicator = steps.map((s, i) => (
    <div key={i} style={{ display: 'inline-block', marginRight: 16 }}>
      <span style={{
        display: 'inline-block',
        width: 32,
        height: 32,
        borderRadius: '50%',
        background: i < step ? 'var(--success)' : i === step - 1 ? 'var(--accent)' : 'var(--bg-tertiary)',
        color: i < step || i === step - 1 ? 'white' : 'var(--text-muted)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontWeight: 600,
        fontSize: 14,
      }}>
        {i < step - 1 ? '✓' : i + 1}
      </span>
    </div>
  ))

  return (
    <div style={{
      minHeight: '100vh',
      background: 'linear-gradient(135deg, rgba(107,92,231,0.1) 0%, rgba(147,112,219,0.05) 100%)',
      paddingTop: 40,
      paddingBottom: 40,
    }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <div style={{ textAlign: 'center', marginBottom: 40 }}>
          {stepIndicator}
        </div>

        {step === 1 && <WelcomeStep onNext={handleLLMNext} />}
        {step === 2 && <LLMConfigStep onNext={handleLLMNext} onBack={() => setStep(1)} />}
        {step === 3 && <RelayConfigStep onNext={handleRelayNext} onBack={() => setStep(2)} />}
        {step === 4 && <CampaignConfigStep onNext={handleCampaignNext} onBack={() => setStep(3)} />}
        {step === 5 && <CompleteStep config={config} />}

        {saving && (
          <div style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}>
            <div style={{
              background: 'var(--bg-primary)',
              padding: 32,
              borderRadius: 12,
              textAlign: 'center',
            }}>
              <div style={{ fontSize: 32, marginBottom: 16 }}>⏳</div>
              <p style={{ color: 'var(--text-secondary)' }}>Writing configuration...</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
