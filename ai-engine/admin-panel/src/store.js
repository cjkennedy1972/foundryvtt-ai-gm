import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'

const API_BASE = '/api'

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/api/ws`
}

export const useStore = create(
  subscribeWithSelector((set, get) => ({
    // UI state
    activePage: 'dashboard',
    setActivePage: (page) => set({ activePage: page }),

    // Engine status
    engineStatus: null,

    // Game state
    gameState: null,

    // Session events
    events: [],

    // NPC list
    npcs: [],

    // AI running state
    aiRunning: false,

    // Settings form
    settings: {
      model: 'anthropic/claude-sonnet-4-0721',
      temperature: 0.7,
      aiName: 'Aethelwyrd AI',
      aiTone: 'Descriptive, atmospheric, and player-centric',
      relayUrl: 'http://localhost:3010',
      relayApiKey: ''
    },
    setSetting: (key, value) =>
      set((s) => ({ settings: { ...s.settings, [key]: value } })),
    setSettings: (settings) => set({ settings }),

    // Campaign builder form (renamed from newCampaign for clarity)
    newCampaign: { name: '', vaultFiles: '', description: '' },
    setNewCampaign: (field, value) =>
      set((s) => ({ newCampaign: { ...s.newCampaign, [field]: value } })),
    resetNewCampaign: () =>
      set({ newCampaign: { name: '', vaultFiles: '', description: '' } }),

    // Chat test
    chatTest: { message: '', speaker: '', result: null, loading: false },
    setChatTest: (partial) =>
      set((s) => ({ chatTest: { ...s.chatTest, ...partial } })),

    // SRD search
    srdQuery: '',
    srdResults: '',
    setSrdQuery: (q) => set({ srdQuery: q }),
    setSrdResults: (r) => set({ srdResults: r }),

    // Manual roll (renamed state key to rollForm to avoid conflict with performRoll action)
    rollForm: { formula: '1d20', speaker: 'GM', flavor: '' },
    rollResult: null,
    setRollForm: (field, value) =>
      set((s) => ({ rollForm: { ...s.rollForm, [field]: value } })),

    // --- WebSocket (shared persistent connection) ---
    ws: null,
    wsConnected: false,

    connectWS() {
      const existing = get().ws
      if (existing && existing.readyState === WebSocket.OPEN) return

      const ws = new WebSocket(wsUrl())

      ws.onopen = () => set({ ws, wsConnected: true })

      ws.onclose = (evt) => {
        set({ ws: null, wsConnected: false })
        // Reconnect unless the tab is closing
        if (evt.code !== 1001) setTimeout(() => get().connectWS(), 3000)
      }

      ws.onerror = () => {} // onclose always follows; avoid double-logging

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data)
          if (msg.type === 'ai_paused')        set({ aiRunning: false })
          else if (msg.type === 'ai_resumed')  set({ aiRunning: true })
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

    sendWS(type, data = {}) {
      const ws = get().ws
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type, ...data }))
      }
    },

    // --- Data fetching ---

    async fetchStatus() {
      try {
        const res = await fetch(`${API_BASE}/status`)
        const data = await res.json()
        set({ engineStatus: data, aiRunning: data.ai_running || false })
      } catch (e) {
        console.error('Failed to fetch status:', e)
      }
    },

    async fetchState() {
      try {
        const res = await fetch(`${API_BASE}/state`)
        const data = await res.json()
        set({ gameState: data })
      } catch (e) {
        console.error('Failed to fetch state:', e)
      }
    },

    async fetchEvents(limit = 50) {
      try {
        const res = await fetch(`${API_BASE}/session/events?limit=${limit}`)
        const data = await res.json()
        set({ events: data })
      } catch (e) {
        console.error('Failed to fetch events:', e)
      }
    },

    async fetchNpcs() {
      try {
        const res = await fetch(`${API_BASE}/npcs`)
        const data = await res.json()
        set({ npcs: data.npcs || [] })
      } catch (e) {
        console.error('Failed to fetch NPCs:', e)
      }
    },

    async testChat() {
      const { message, speaker } = get().chatTest
      if (!message) return
      set((s) => ({ chatTest: { ...s.chatTest, loading: true } }))
      try {
        const res = await fetch(`${API_BASE}/chat/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message, speaker: speaker || 'Player' })
        })
        const data = await res.json()
        set((s) => ({ chatTest: { ...s.chatTest, result: data, loading: false, message: '' } }))
      } catch (e) {
        set((s) => ({ chatTest: { ...s.chatTest, result: { error: e.message }, loading: false } }))
      }
    },

    async searchSrd() {
      const query = get().srdQuery
      if (!query) return
      try {
        const res = await fetch(`${API_BASE}/srd/search?query=${encodeURIComponent(query)}`)
        const data = await res.json()
        set({ srdResults: data.results || '' })
      } catch (e) {
        set({ srdResults: `Error: ${e.message}` })
      }
    },

    async performRoll() {
      const { formula, speaker, flavor } = get().rollForm
      try {
        const res = await fetch(`${API_BASE}/roll`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ formula, speaker, flavor })
        })
        const data = await res.json()
        set({ rollResult: data })
      } catch (e) {
        set({ rollResult: { error: e.message } })
      }
    },

    async saveSettings() {
      try {
        await fetch(`${API_BASE}/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(get().settings)
        })
        await get().fetchStatus()
      } catch (e) {
        console.error('Failed to save settings:', e)
      }
    },

    async createSession() {
      try {
        await fetch(`${API_BASE}/session/new`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ campaign: 'Aethelwyrd' })
        })
        await get().fetchStatus()
        await get().fetchEvents()
      } catch (e) {
        console.error('Failed to create session:', e)
      }
    },

    async updateGameState(field, value) {
      try {
        await fetch(`${API_BASE}/state/update`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ [field]: value })
        })
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
  }))
)
