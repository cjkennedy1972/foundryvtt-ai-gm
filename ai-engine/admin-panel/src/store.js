/**
 * Zustand store for admin panel state management.
 * Keeps track of engine status, game state, session events, and active page.
 */
import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'

const API_BASE = '/api'

export const useStore = create(
  subscribeWithSelector((set, get) => ({
    // UI state
    activePage: 'dashboard',
    setActivePage: (page) => set({ activePage: page }),

    // Engine status
    engineStatus: null,
    setEngineStatus: (status) => set({ engineStatus: status }),

    // Game state
    gameState: null,
    setGameState: (state) => set({ gameState: state }),

    // Session events
    events: [],
    setEvents: (events) => set({ events }),

    // NPC list
    npcs: [],
    setNpcs: (npcs) => set({ npcs }),

    // AI running state
    aiRunning: false,
    setAiRunning: (running) => set({ aiRunning: running }),

    // Settings form
    settings: {
      model: 'anthropic/claude-sonnet-4-0721',
      temperature: 0.7,
      aiName: 'Aethelwyrd AI',
      aiTone: 'Descriptive, atmospheric, and player-centric',
      relayUrl: 'http://localhost:3010',
      relayApiKey: 'dev-user-dev-only'
    },
    setSetting: (key, value) =>
      set((state) => ({
        settings: { ...state.settings, [key]: value }
      })),
    setSettings: (settings) => set({ settings }),

    // Session builder
    newCampaign: {
      name: '',
      vaultFiles: '',
      description: ''
    },
    setNewCampaign: (field, value) =>
      set((state) => ({
        newCampaign: { ...state.newCampaign, [field]: value }
      })),
    resetNewCampaign: () =>
      set({
        newCampaign: { name: '', vaultFiles: '', description: '' }
      }),

    // Chat test
    chatTest: {
      message: '',
      speaker: '',
      result: null,
      loading: false
    },
    setChatTest: (partial) =>
      set((state) => ({ chatTest: { ...state.chatTest, ...partial } })),

    // SRD search
    srdQuery: '',
    srdResults: '',
    setSrdQuery: (q) => set({ srdQuery: q }),
    setSrdResults: (r) => set({ srdResults: r }),

    // Manual roll
    manualRoll: { formula: '1d20', speaker: 'GM', flavor: '' },
    rollResult: null,
    setManualRoll: (field, value) =>
      set((state) => {
        const updated = { ...state.manualRoll, [field]: value }
        return { manualRoll: updated }
      }),

    // --- Data fetching ---

    async fetchStatus() {
      try {
        const res = await fetch(`${API_BASE}/status`)
        const data = await res.json()
        set({
          engineStatus: data,
          aiRunning: data.ai_running || false
        })
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
      set({ 'chatTest.loading': true })
      try {
        const res = await fetch(`${API_BASE}/chat/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message, speaker: speaker || 'Player' })
        })
        const data = await res.json()
        set({
          'chatTest.result': data,
          'chatTest.loading': false,
          'chatTest.message': ''
        })
      } catch (e) {
        set({
          'chatTest.result': { error: e.message },
          'chatTest.loading': false
        })
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

    async manualRoll() {
      try {
        const { formula, speaker, flavor } = get().manualRoll
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
        // Refresh status to confirm
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

    async pauseAI() {
      try {
        const ws = new WebSocket('ws://localhost:8000/admin/ws')
        ws.onopen = () => {
          ws.send(JSON.stringify({ type: 'pause' }))
          ws.close()
        }
        set({ aiRunning: false })
      } catch (e) {
        console.error('Failed to pause AI:', e)
      }
    },

    async resumeAI() {
      try {
        const ws = new WebSocket('ws://localhost:8000/admin/ws')
        ws.onopen = () => {
          ws.send(JSON.stringify({ type: 'resume' }))
          ws.close()
        }
        set({ aiRunning: true })
      } catch (e) {
        console.error('Failed to resume AI:', e)
      }
    }
  }))
)
