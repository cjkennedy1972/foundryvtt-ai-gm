import { useState } from 'react'

// Step 1: Welcome
function WelcomeStep({ onNext }) {
  return (
    <div style={{ maxWidth: 600, margin: '0 auto', textAlign: 'center' }}>
      <div style={{ fontSize: 48, marginBottom: 24 }}>🧙</div>
      <h1 style={{ fontSize: 32, fontWeight: 600, marginBottom: 16 }}>Welcome to AI Gamemaster</h1>
      <p style={{ fontSize: 16, color: 'var(--text-secondary)', marginBottom: 32, lineHeight: 1.6 }}>
        This wizard will set up your AI-driven D&D game master in just a few minutes.
        We'll configure your LLM, connect FoundryVTT, and get you ready to play.
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
        <label htmlFor="wizard-base-url">LLM Base URL</label>
        <input id="wizard-base-url"
          className="input"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="http://localhost:8800/v1"
        />
      </div>

      <div className="form-group">
        <label htmlFor="wizard-api-key">API Key</label>
        <input id="wizard-api-key"
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
          <label htmlFor="wizard-model">Select Model</label>
          <select id="wizard-model"
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

// Step 3: Foundry login (the engine logs in as a GM user; nothing is sent from here)
function FoundryLoginStep({ onNext, onBack }) {
  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 16 }}>Connect FoundryVTT</h2>
      <p style={{ color: 'var(--text-secondary)', marginBottom: 24 }}>
        The AI GM logs into your Foundry world as its own Gamemaster account. You create that account
        in Foundry, then give its login to the engine.
      </p>

      <div className="card" style={{ marginBottom: 24 }}>
        <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Next Steps:</h4>
        <ol style={{ fontSize: 13, lineHeight: 1.8, color: 'var(--text-secondary)', paddingLeft: 20 }}>
          <li>In FoundryVTT, create your world with the D&amp;D 5e system</li>
          <li>Create a GM user for the engine (for example <code>ai-gm</code>) with a password</li>
          <li>In <code>ai-engine/.env</code>, set <code>FOUNDRY_USERNAME</code> and <code>FOUNDRY_PASSWORD</code> to that user</li>
          <li>Open the world in Foundry. The engine logs into whichever world Foundry is serving</li>
        </ol>
        <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 12 }}>
          The setup wizard writes these two keys empty, so fill them in before you start the engine.
        </p>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
        <button className="btn" onClick={onBack}>← Back</button>
        <button className="btn btn-primary" onClick={onNext}>
          Continue →
        </button>
      </div>
    </div>
  )
}

// Step 4: Campaign Configuration
function CampaignConfigStep({ onNext, onBack }) {
  const [vaultPath, setVaultPath] = useState('~/Vaults/MyStuff/Dungeons_and_Dragons')
  const [aiName, setAiName] = useState('Sage')
  const [aiTone, setAiTone] = useState('mysterious, immersive, high fantasy')

  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 16 }}>Campaign Settings</h2>

      <div className="form-group">
        <label htmlFor="wizard-vault-path">Campaign Vault Path</label>
        <input id="wizard-vault-path"
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
        <label htmlFor="wizard-gm-name">GM Name</label>
        <input id="wizard-gm-name"
          className="input"
          value={aiName}
          onChange={(e) => setAiName(e.target.value)}
          placeholder="Sage"
        />
      </div>

      <div className="form-group">
        <label htmlFor="wizard-gm-tone">GM Tone & Personality</label>
        <textarea id="wizard-gm-tone"
          className="textarea"
          value={aiTone}
          onChange={(e) => setAiTone(e.target.value)}
          rows={3}
          placeholder="mysterious, immersive, high fantasy"
        />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
        <button className="btn" onClick={onBack}>← Back</button>
        <button className="btn btn-primary" onClick={() => onNext({ vaultPath, aiName, aiTone })}>
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
  const [saveError, setSaveError] = useState('')

  // step is 1-based and names the step on screen, so each handler moves to
  // step + 1.
  const handleLLMNext = (llmConfig) => {
    setConfig(llmConfig)
    setStep(3)
  }

  const handleFoundryNext = () => {
    setStep(4)
  }

  const handleCampaignNext = async (campaignConfig) => {
    setSaving(true)
    setSaveError('')
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
        setConfig({ ...config, ...campaignConfig })
        setStep(5)
      } else {
        setSaveError(data.detail || data.message || 'Saving the configuration failed.')
      }
    } catch (e) {
      console.error('Failed to write .env:', e)
      setSaveError(e.message)
    }
    setSaving(false)
  }

  const steps = ['Welcome', 'LLM', 'Foundry', 'Campaign', 'Complete']
  const stepIndicator = steps.map((s, i) => (
    <div key={i} style={{ display: 'inline-block', marginRight: 16 }}>
      <span style={{
        display: 'inline-block',
        width: 32,
        height: 32,
        borderRadius: '50%',
        background: i < step - 1 ? 'var(--success)' : i === step - 1 ? 'var(--accent)' : 'var(--bg-tertiary)',
        color: i <= step - 1 ? 'white' : 'var(--text-muted)',
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

        {step === 1 && <WelcomeStep onNext={() => setStep(2)} />}
        {step === 2 && <LLMConfigStep onNext={handleLLMNext} onBack={() => setStep(1)} />}
        {step === 3 && <FoundryLoginStep onNext={handleFoundryNext} onBack={() => setStep(2)} />}
        {step === 4 && <CampaignConfigStep onNext={handleCampaignNext} onBack={() => setStep(3)} />}
        {step === 5 && <CompleteStep config={config} />}

        {saveError && (
          <div role="alert" style={{ maxWidth: 600, margin: '16px auto 0', background: 'rgba(244, 67, 54, 0.1)', border: '1px solid rgba(244, 67, 54, 0.3)', borderRadius: 6, padding: 12, color: 'var(--danger)', fontSize: 14 }}>
            {saveError}
          </div>
        )}

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
