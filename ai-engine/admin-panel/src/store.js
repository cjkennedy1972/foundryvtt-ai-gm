import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'
import { API_BASE, wsUrl, SECRET_KEYS } from './config.js'
import { safeFetch } from './fetch.js'

// Expose for components that need it without importing config directly
export { API_BASE, wsUrl, SECRET_KEYS }

export const useStore = create(
  subscribeWithSelector((set, get) => ({
    // ── UI state ──────────────────────────────────────────────────────────

    activePage: 'dashboard',
    setActivePage: (page) => set({ activePage: page }),

    // Global error/status message for surfacing fetch failures to the UI
    statusMessage: null,
    setStatusMessage: (msg) => set({ statusMessage: msg }),

    // ── Engine status & game state ────────────────────────────────────────

    engineStatus: null,
    aiRunning: false,
    gameState: null,

    // ── Session events ────────────────────────────────────────────────────

    events: [],

    // ── NPC list ──────────────────────────────────────────────────────────

    npcs: [],

    // ── LLM mode: 'local' or 'commercial' ─────────────────────────────────

    llmMode: 'local',
    setLlmMode: (mode) => set({ llmMode: mode }),

    // ── Settings form ─────────────────────────────────────────────────────

    settings: {
      model: '',
      llm_base_url: '',
      llm_api_key: '',
      temperature: 0.7,
      aiName: '',
      aiTone: '',
      relayUrl: '',
      relayApiKey: '',
      comfyuiUrl: ''
    },
    setSetting: (key, value) =>
      set((s) => ({ settings: { ...s.settings, [key]: value } })),
    setSettings: (settings) => set({ settings }),

    // ── Fetch settings from backend ───────────────────────────────────────
    // Secrets are redacted from the server response; never returned in cleartext.
    async fetchSettings() {
      try {
        const res = await safeFetch('/settings')
        const data = res.data
        if (!res.ok) {
          set({ statusMessage: res.error || 'Failed to load settings' })
          return
        }

        // Redact secrets — never surface keys to the frontend
        const masked = (k) => data[k] ? '••••••••' : ''

        set({
          settings: {
            model: data.model || '',
            llm_base_url: data.llm_base_url || '',
            llm_api_key: masked('llm_api_key'),
            temperature: data.temperature ?? 0.7,
            aiName: data.ai_name || '',
            aiTone: data.ai_tone || '',
            relayUrl: data.relay_url || '',
            relayApiKey: masked('relay_api_key'),
            comfyuiUrl: data.comfyui_url || ''
          }
        })

        // Hydrate llmMode from the server's llm_base_url so the provider
        // toggle is correct after a page reload
        const baseUrl = (data.llm_base_url || '').trim()
        if (!baseUrl) {
          set({ llmMode: 'local' })
        } else if (/anthropic/i.test(baseUrl)) {
          set({ llmMode: 'anthropic' })
        } else if (/google|gemini/i.test(baseUrl)) {
          set({ llmMode: 'google' })
        } else if (/openai/i.test(baseUrl)) {
          set({ llmMode: 'openai' })
        } else if (/openrouter/i.test(baseUrl)) {
          set({ llmMode: 'openrouter' })
        } else {
          set({ llmMode: 'local' })
        }
      } catch (e) {
        set({ statusMessage: 'Failed to load settings: ' + e.message })
        console.error('Failed to fetch settings:', e)
      }
    },

    // ── Campaign wizard (multi-step build) ────────────────────────────────

    campaignWizard: {
      name: '',
      description: '',
      theme: '',
      seedIdeas: '',
      scale: '',
      scanWorld: null,
      buildResult: null,
      buildInProgress: false,
      buildError: null,
      currentStep: 1, // 1=info, 2=scan, 3=build, 4=complete
    },
    setWizardField: (field, value) =>
      set((s) => ({
        campaignWizard: { ...s.campaignWizard, [field]: value }
      })),

    // Single, canonical buildCampaign action (no duplicate)
    async buildCampaign() {
      const { campaignWizard } = get()
      const name = campaignWizard.name || 'Unnamed Campaign'
      const description = campaignWizard.description || ''
      const theme = campaignWizard.theme || ''
      const seedIdeas = campaignWizard.seedIdeas || ''
      const scale = campaignWizard.scale || ''

      set((s) => ({
        campaignWizard: { ...s.campaignWizard, buildInProgress: true, buildError: null }
      }))

      try {
        const res = await safeFetch('/campaign/build', {
          method: 'POST',
          body: {
            name,
            description,
            theme,
            seed_ideas: seedIdeas,
            scale,
          }
        })

        if (!res.ok) {
          set((s) => ({
            campaignWizard: {
              ...s.campaignWizard,
              buildError: res.error || 'Build failed',
              buildInProgress: false,
            }
          }))
          return { ok: false, error: res.error || 'Build failed' }
        }

        const data = res.data

        set((s) => ({
          campaignWizard: {
            ...s.campaignWizard,
            buildResult: data,
            buildInProgress: false,
            currentStep: (data.ready_to_start || data.status === 'ok' || data.status === 'complete') ? 4 : 3
          }
        }))

        return { ok: data.status === 'ok' || data.status === 'complete', data }
      } catch (e) {
        set((s) => ({
          campaignWizard: { ...s.campaignWizard, buildError: e.message, buildInProgress: false }
        }))
        return { ok: false, error: e.message }
      }
    },

    setWizardStep: (step) =>
      set((s) => ({ campaignWizard: { ...s.campaignWizard, currentStep: step } })),
    resetWizard: () =>
      set({ campaignWizard: {
        name: '', description: '', theme: '', seedIdeas: '',
        scanWorld: null, buildResult: null,
        buildInProgress: false, buildError: null, currentStep: 1
      }}),

    // ── Campaign session management ───────────────────────────────────────

    campaignSession: {
      campaigns: [],
      selectedCampaign: null,
      activeSession: null,
      loading: false,
      error: null,
    },
    setCampaignSession: (key, value) =>
      set((s) => ({ campaignSession: { ...s.campaignSession, [key]: value } })),
    resetCampaignSession: () =>
      set({ campaignSession: { campaigns: [], selectedCampaign: null, activeSession: null, loading: false, error: null } }),

    // ── Campaign builder form (legacy compatibility) ──────────────────────

    newCampaign: { name: '', vaultFiles: '', description: '' },
    setNewCampaign: (field, value) =>
      set((s) => ({ newCampaign: { ...s.newCampaign, [field]: value } })),
    resetNewCampaign: () =>
      set({ newCampaign: { name: '', vaultFiles: '', description: '' } }),

    // ── Chat test ─────────────────────────────────────────────────────────

    chatTest: { message: '', speaker: '', result: null, loading: false },
    setChatTest: (partial) =>
      set((s) => ({ chatTest: { ...s.chatTest, ...partial } })),

    // ── SRD search ────────────────────────────────────────────────────────

    srdQuery: '',
    srdResults: '',
    setSrdQuery: (q) => set({ srdQuery: q }),
    setSrdResults: (r) => set({ srdResults: r }),

    // ── Manual roll ───────────────────────────────────────────────────────

    rollForm: { formula: '1d20', speaker: 'GM', flavor: '' },
    rollResult: null,
    setRollForm: (field, value) =>
      set((s) => ({ rollForm: { ...s.rollForm, [field]: value } })),

    // ── WebSocket (shared persistent connection) ──────────────────────────
    // Fixed: proper reconnect with backoff, timer tracking, and closeWS teardown.

    ws: null,
    wsConnected: false,
    wsReconnectAttempt: 0,
    wsReconnectTimer: null, // stored timer id for clean cancellation

    connectWS() {
      // Don't connect if already open
      const existing = get().ws
      if (existing && existing.readyState === WebSocket.OPEN) return

      // Build URL — now uses the centralized config
      const url = wsUrl()

      // Compute exponential-backoff delay (base 3s, capped at 30s)
      const delay = Math.min(3000 * Math.pow(1.5, get().wsReconnectAttempt), 30000)

      set({ statusMessage: `Connecting to AI engine…` })

      const ws = new WebSocket(url)

      ws.onopen = () => {
        set({ ws, wsConnected: true, wsReconnectAttempt: 0 }) // reset on success
      }

      ws.onclose = (evt) => {
        const wsState = get().ws
        if (wsState && wsState === ws) {
          set({ ws: null, wsConnected: false })
        }

        // Don't auto-reconnect if the tab is closing (code 1001)
        // or if the server intentionally shut down (code 1000)
        if (evt.code === 1000 || evt.code === 1001) return

        // Schedule reconnect with exponential backoff
        const attempt = (get().wsReconnectAttempt || 0) + 1
        set({ wsReconnectAttempt: attempt })

        // Clear any pending timer (belt-and-suspenders)
        const oldTimer = get().wsReconnectTimer
        if (oldTimer) clearTimeout(oldTimer)

        const timer = setTimeout(() => {
          get().connectWS()
        }, delay)
        set({ wsReconnectTimer: timer })

        if (attempt <= 3) {
          set({ statusMessage: `Reconnecting in ${Math.round(delay / 1000)}s…` })
        }
      }

      ws.onerror = () => {
        // onclose always follows; avoid double-logging
      }

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data)
          if (msg.type === 'ai_paused')        set({ aiRunning: false })
          else if (msg.type === 'ai_resumed')  set({ aiRunning: true })
          else if (msg.type === 'session_started') {
            set((s) => ({
              campaignSession: {
                ...s.campaignSession,
                activeSession: {
                  session_id: msg.session_id,
                  campaign_name: msg.campaign_name,
                  status: 'started',
                },
              },
            }))
          }
          else if (msg.type === 'scene_loaded') {
            set((s) => ({
              gameState: s.gameState ? { ...s.gameState, current_scene: msg.scene_name } : s.gameState
            }))
          } else if (msg.type === 'combat_started') {
            set((s) => ({
              gameState: s.gameState ? { ...s.gameState, mode: 'combat' } : s.gameState
            }))
          }
        } catch (e) {
          console.error('WS parse error:', e)
        }
      }

      set({ ws })
    },

    /** Teardown WebSocket and clear any pending reconnect timer. */
    closeWS() {
      const ws = get().ws
      if (ws) {
        ws.close()
      }
      const timer = get().wsReconnectTimer
      if (timer) {
        clearTimeout(timer)
        set({ wsReconnectTimer: null })
      }
      set({ ws: null, wsConnected: false, wsReconnectAttempt: 0 })
    },

    sendWS(type, data = {}) {
      const ws = get().ws
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type, ...data }))
      }
    },

    // ── Data fetching ─────────────────────────────────────────────────────
    // All fetch calls now use safeFetch which checks res.ok.

    async fetchStatus() {
      try {
        const res = await safeFetch('/status')
        if (!res.ok) {
          set({ statusMessage: res.error || 'Status check failed' })
          return
        }
        set({ engineStatus: res.data, aiRunning: res.data.ai_running || false })
      } catch (e) {
        set({ statusMessage: 'Failed to fetch status: ' + e.message })
        console.error('Failed to fetch status:', e)
      }
    },

    async fetchState() {
      try {
        const res = await safeFetch('/state')
        if (!res.ok) return
        set({ gameState: res.data })
      } catch (e) {
        console.error('Failed to fetch state:', e)
      }
    },

    async fetchEvents(limit = 50) {
      try {
        const res = await safeFetch(`/session/events?limit=${limit}`)
        if (!res.ok) return
        set({ events: res.data })
      } catch (e) {
        console.error('Failed to fetch events:', e)
      }
    },

    async fetchNpcs() {
      try {
        const res = await safeFetch('/npcs')
        if (!res.ok) return
        set({ npcs: res.data.npcs || [] })
      } catch (e) {
        console.error('Failed to fetch NPCs:', e)
      }
    },

    // ── Campaign Wizard Actions ───────────────────────────────────────────

    async scanWorld() {
      const { campaignWizard } = get()
      const name = campaignWizard.name || 'Unnamed World'

      set((s) => ({
        campaignWizard: { ...s.campaignWizard, buildError: null, buildInProgress: true }
      }))

      try {
        const res = await safeFetch('/campaign/scan', {
          method: 'POST',
          body: { world_name: name }
        })

        if (!res.ok) {
          set((s) => ({
            campaignWizard: { ...s.campaignWizard, buildError: res.error, buildInProgress: false }
          }))
          return { ok: false, error: res.error }
        }

        const data = res.data

        if (data.status === 'ok') {
          set((s) => ({
            campaignWizard: {
              ...s.campaignWizard,
              scanWorld: data,
              buildInProgress: false
            }
          }))
          return { ok: true, data }
        } else {
          set((s) => ({
            campaignWizard: { ...s.campaignWizard, buildError: data.error, buildInProgress: false }
          }))
          return { ok: false, error: data.error }
        }
      } catch (e) {
        set((s) => ({
          campaignWizard: { ...s.campaignWizard, buildError: e.message, buildInProgress: false }
        }))
        return { ok: false, error: e.message }
      }
    },

    // ── Campaign Session Actions ──────────────────────────────────────────

    async fetchActiveSession() {
      try {
        const res = await safeFetch('/session/active')
        const data = res.data

        if (!res.ok) {
          set({
            campaignSession: { ...get().campaignSession, activeSession: null },
          })
          return data
        }

        if (data.active && data.session_id) {
          set((s) => ({
            campaignSession: {
              ...s.campaignSession,
              activeSession: {
                session_id: data.session_id,
                campaign_name: data.campaign_name,
                status: 'started',
              },
            },
          }))
        } else {
          set((s) => ({
            campaignSession: { ...s.campaignSession, activeSession: null },
          }))
        }
        return data
      } catch (e) {
        console.error('Failed to fetch active session:', e)
        return null
      }
    },

    async listCampaigns() {
      set((s) => ({ campaignSession: { ...s.campaignSession, loading: true, error: null } }))
      try {
        const res = await safeFetch('/campaign/list')
        if (!res.ok) {
          set({
            campaignSession: { ...get().campaignSession, error: res.error, loading: false }
          })
          return { ok: false, error: res.error }
        }
        set({
          campaignSession: {
            ...get().campaignSession,
            campaigns: res.data.campaigns || [],
            loading: false
          }
        })
        return res.data
      } catch (e) {
        set({
          campaignSession: { ...get().campaignSession, error: e.message, loading: false }
        })
        return { error: e.message }
      }
    },

    async getCampaign(name) {
      set((s) => ({ campaignSession: { ...s.campaignSession, loading: true, error: null } }))
      try {
        const res = await safeFetch(`/campaign/get/${encodeURIComponent(name)}`)
        if (!res.ok) {
          set({
            campaignSession: { ...get().campaignSession, error: res.error, loading: false }
          })
          return { ok: false, error: res.error }
        }
        set({
          campaignSession: {
            ...get().campaignSession,
            selectedCampaign: res.data,
            loading: false
          }
        })
        return res.data
      } catch (e) {
        set({
          campaignSession: { ...get().campaignSession, error: e.message, loading: false }
        })
        return { error: e.message }
      }
    },

    async deleteCampaign(name) {
      set((s) => ({ campaignSession: { ...s.campaignSession, loading: true, error: null } }))
      try {
        const res = await safeFetch('/campaign/delete', {
          method: 'POST',
          body: { name }
        })
        if (!res.ok) {
          set({
            campaignSession: { ...get().campaignSession, error: res.error, loading: false }
          })
          return { ok: false, error: res.error }
        }
        // Refresh list
        await get().listCampaigns()
        return res.data
      } catch (e) {
        set({
          campaignSession: { ...get().campaignSession, error: e.message, loading: false }
        })
        return { error: e.message }
      }
    },

    async deployCampaign(campaignName) {
      try {
        const res = await fetch(`${API_BASE}/campaign/deploy`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ campaign_name: campaignName })
        })
        const data = await res.json()
        return data
      } catch (e) {
        console.error('Deployment failed:', e)
        return { error: e.message }
      }
    },

    async regenerateAssets(campaignName) {
      try {
        const res = await fetch(`${API_BASE}/campaign/regenerate-assets`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ campaign_name: campaignName, attach_to_foundry: true })
        })
        return await res.json()
      } catch (e) {
        console.error('Asset regeneration failed:', e)
        return { status: 'error', error: e.message }
      }
    },

    async startCampaign(campaignName, continueFromLast = false) {
      set((s) => ({ campaignSession: { ...s.campaignSession, loading: true, error: null } }))
      try {
        const res = await safeFetch('/campaign/start', {
          method: 'POST',
          body: {
            campaign_name: campaignName,
            continue_from_last: continueFromLast
          }
        })
        if (!res.ok) {
          set({
            campaignSession: {
              ...get().campaignSession,
              loading: false,
              error: res.error || 'Failed to start campaign',
            }
          })
          return { ok: false, error: res.error }
        }
        const data = res.data
        if (data.status === 'started') {
          set((s) => ({
            campaignSession: {
              ...s.campaignSession,
              activeSession: data,
              loading: false,
              error: null,
            }
          }))
        } else {
          set({
            campaignSession: {
              ...get().campaignSession,
              loading: false,
              error: data.error || data.message || 'Failed to start campaign',
            }
          })
        }
        return data
      } catch (e) {
        set({
          campaignSession: { ...get().campaignSession, error: e.message, loading: false }
        })
        return { error: e.message }
      }
    },

    async endSession(reason = 'GM ended session') {
      set((s) => ({ campaignSession: { ...s.campaignSession, loading: true, error: null } }))
      try {
        const res = await safeFetch('/session/end', {
          method: 'POST',
          body: { reason }
        })
        set((s) => ({
          campaignSession: {
            ...s.campaignSession,
            activeSession: null,
            selectedCampaign: null,
            loading: false,
            error: null,
          }
        }))
        return res.data
      } catch (e) {
        set({
          campaignSession: { ...get().campaignSession, loading: false, error: e.message }
        })
        return { error: e.message }
      }
    },

    // ── Misc actions ──────────────────────────────────────────────────────

    async testChat() {
      const { message, speaker } = get().chatTest
      if (!message) return
      set((s) => ({ chatTest: { ...s.chatTest, loading: true } }))
      try {
        const res = await safeFetch('/chat/test', {
          method: 'POST',
          body: { message, speaker: speaker || 'Player' }
        })
        set((s) => ({
          chatTest: {
            ...s.chatTest,
            result: res.ok ? res.data : { error: res.error },
            loading: false,
            message: ''
          }
        }))
      } catch (e) {
        set((s) => ({
          chatTest: { ...s.chatTest, result: { error: e.message }, loading: false }
        }))
      }
    },

    async searchSrd() {
      const query = get().srdQuery
      if (!query) return
      try {
        const res = await safeFetch(`/srd/search?query=${encodeURIComponent(query)}`)
        set({
          srdResults: res.ok ? (res.data.results || '') : `Error: ${res.error}`
        })
      } catch (e) {
        set({ srdResults: `Error: ${e.message}` })
      }
    },

    async performRoll() {
      const { formula, speaker, flavor } = get().rollForm
      try {
        const res = await safeFetch('/roll', {
          method: 'POST',
          body: { formula, speaker, flavor }
        })
        set({ rollResult: res.ok ? res.data : { error: res.error } })
      } catch (e) {
        set({ rollResult: { error: e.message } })
      }
    },

    async saveSettings() {
      try {
        const res = await safeFetch('/settings', {
          method: 'POST',
          body: get().settings
        })
        if (!res.ok) {
          set({ statusMessage: 'Failed to save settings: ' + res.error })
          return
        }
        await get().fetchStatus()
      } catch (e) {
        set({ statusMessage: 'Failed to save settings: ' + e.message })
        console.error('Failed to save settings:', e)
      }
    },

    async createSession() {
      try {
        await safeFetch('/session/new', {
          method: 'POST',
          body: { campaign: 'Aethelwyrd' }
        })
        await get().fetchStatus()
        await get().fetchEvents()
      } catch (e) {
        console.error('Failed to create session:', e)
      }
    },

    async updateGameState(field, value) {
      try {
        const res = await safeFetch('/state/update', {
          method: 'POST',
          body: { [field]: value }
        })
        if (!res.ok) {
          set({ statusMessage: res.error || 'State update failed' })
          return
        }
        await get().fetchState()
      } catch (e) {
        console.error('Failed to update state:', e)
      }
    },

    pauseAI() {
      get().sendWS('pause')
      set({ aiRunning: false })
    },

    resumeAI() {
      get().sendWS('resume')
      set({ aiRunning: true })
    },

    async relayStart() {
      try {
        const res = await safeFetch('/relay/start', { method: 'POST' })
        if (!res.ok) return { ok: false, error: res.error }
        await get().fetchStatus()
        return res.data
      } catch (e) {
        return { error: e.message }
      }
    },

    async relayStop() {
      try {
        const res = await safeFetch('/relay/stop', { method: 'POST' })
        if (!res.ok) return { ok: false, error: res.error }
        await get().fetchStatus()
        return res.data
      } catch (e) {
        return { error: e.message }
      }
    },

    async relayRestart() {
      try {
        const res = await safeFetch('/relay/restart', { method: 'POST' })
        if (!res.ok) return { ok: false, error: res.error }
        await get().fetchStatus()
        return res.data
      } catch (e) {
        return { error: e.message }
      }
    },

    // ── Direct GM Chat ────────────────────────────────────────────────────

    gmChatMessages: [],

    async sendDirectGMMessage(message) {
      try {
        // Add user message to chat history
        set((s) => ({
          gmChatMessages: [...s.gmChatMessages, { role: 'user', content: message }]
        }))

        // Send to backend
        const res = await safeFetch('/api/chat/gm', {
          method: 'POST',
          body: { message }
        })

        if (!res.ok) {
          set((s) => ({
            gmChatMessages: [...s.gmChatMessages, {
              role: 'assistant',
              content: `Error: ${res.error || 'Failed to get response'}`
            }]
          }))
          return
        }

        // Add assistant response to chat history
        const response = res.data?.response || 'No response received'
        set((s) => ({
          gmChatMessages: [...s.gmChatMessages, { role: 'assistant', content: response }]
        }))
      } catch (e) {
        console.error('Failed to send GM chat message:', e)
        set((s) => ({
          gmChatMessages: [...s.gmChatMessages, {
            role: 'assistant',
            content: `Error: ${e.message}`
          }]
        }))
      }
    },
  }))
)
