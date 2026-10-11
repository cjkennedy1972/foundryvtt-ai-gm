import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore } from '../test/store-harness.jsx'
import Dashboard from './Dashboard.jsx'

let actions

const STATUS = {
  connected: true,
  model: 'qwen3-32b',
  conversation_length: 7,
  modules: {},
}

function seed(extra = {}) {
  return renderWithStore(<Dashboard />, { ...actions, engineStatus: STATUS, ...extra })
}

beforeEach(() => {
  actions = {
    fetchStatus: vi.fn(async () => {}),
    fetchState: vi.fn(async () => {}),
    fetchEvents: vi.fn(async () => {}),
    connectWS: vi.fn(() => {}),
    closeWS: vi.fn(() => {}),
    pauseAI: vi.fn(async () => {}),
    resumeAI: vi.fn(async () => {}),
  }
})

afterEach(resetStore)

describe('Dashboard', () => {
  it('waits rather than rendering a dashboard of blanks before the first status', () => {
    seed({ engineStatus: null })

    expect(screen.getByText(/connecting to engine/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Dashboard' })).not.toBeInTheDocument()
  })

  it('loads status, state and events and opens the socket on mount', () => {
    seed()

    expect(actions.fetchStatus).toHaveBeenCalledTimes(1)
    expect(actions.fetchState).toHaveBeenCalledTimes(1)
    expect(actions.fetchEvents).toHaveBeenCalledTimes(1)
    expect(actions.connectWS).toHaveBeenCalledTimes(1)
  })

  it('polls status and state every ten seconds', () => {
    vi.useFakeTimers()
    seed()

    act(() => vi.advanceTimersByTime(30_000))

    expect(actions.fetchStatus).toHaveBeenCalledTimes(4)
    expect(actions.fetchState).toHaveBeenCalledTimes(4)
    // Events and the socket are mount-only; polling them would be churn.
    expect(actions.fetchEvents).toHaveBeenCalledTimes(1)
    expect(actions.connectWS).toHaveBeenCalledTimes(1)
  })

  it('stops polling and closes the socket on unmount', () => {
    vi.useFakeTimers()
    const { unmount } = seed()

    unmount()
    act(() => vi.advanceTimersByTime(30_000))

    expect(actions.closeWS).toHaveBeenCalledTimes(1)
    expect(actions.fetchStatus).toHaveBeenCalledTimes(1)
  })

  it.each([
    [{ connected: true }, 'Connected to Foundry'],
    [{ connected: false }, 'Disconnected'],
  ])('reports the Foundry connection', (patch, label) => {
    seed({ engineStatus: { ...STATUS, ...patch } })

    expect(screen.getByText(label)).toHaveClass(patch.connected ? 'badge-connected' : 'badge-disconnected')
  })

  it.each([
    [true, 'WS Live'],
    [false, 'WS Offline'],
  ])('reports the socket state', (wsConnected, label) => {
    seed({ wsConnected })

    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it.each([
    [true, 'AI Active'],
    [false, 'AI Paused'],
  ])('reports whether the AI is running', (aiRunning, label) => {
    seed({ aiRunning })

    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('shows the configured model, context size and campaign stats', () => {
    seed({ gameState: { campaign: 'Greenrest', session_number: 4, mode: 'combat', current_scene: 'The Mill' } })

    expect(screen.getByText('qwen3-32b')).toBeInTheDocument()
    expect(screen.getByText('7 messages')).toBeInTheDocument()
    expect(screen.getByText('Greenrest')).toBeInTheDocument()
    expect(screen.getByText('#4')).toBeInTheDocument()
    expect(screen.getByText('combat')).toBeInTheDocument()
    expect(screen.getByText('The Mill')).toBeInTheDocument()
  })

  it('falls back to placeholders before the game state arrives', () => {
    seed({ engineStatus: { ...STATUS, model: null }, gameState: null })

    expect(screen.getByText('Not configured')).toBeInTheDocument()
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    expect(screen.getByText('#0')).toBeInTheDocument()
    expect(screen.getByText('exploration')).toBeInTheDocument()
    expect(screen.getByText('None')).toBeInTheDocument()
  })

  it('lists detected modules with their titles and versions', () => {
    seed({ engineStatus: { ...STATUS, modules: { 'sage-gm': { title: 'SAGE GM', version: '0.4.1' } } } })

    expect(screen.getByText('sage-gm')).toBeInTheDocument()
    expect(screen.getByText('SAGE GM')).toBeInTheDocument()
    expect(screen.getByText('v0.4.1')).toBeInTheDocument()
  })

  it('says so when no modules are detected', () => {
    seed()

    expect(screen.getByText(/no modules detected/i)).toBeInTheDocument()
  })

  it('offers only the AI control that would change something', () => {
    const { unmount } = seed({ aiRunning: true })
    expect(screen.getByRole('button', { name: /pause/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /resume/i })).toBeDisabled()

    unmount()
    seed({ aiRunning: false })
    expect(screen.getByRole('button', { name: /pause/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /resume/i })).toBeEnabled()
  })

  it('pauses the AI', async () => {
    const user = userEvent.setup()
    seed({ aiRunning: true })

    await user.click(screen.getByRole('button', { name: /pause/i }))

    expect(actions.pauseAI).toHaveBeenCalled()
  })

  it('resumes the AI', async () => {
    const user = userEvent.setup()
    seed({ aiRunning: false })

    await user.click(screen.getByRole('button', { name: /resume/i }))

    expect(actions.resumeAI).toHaveBeenCalled()
  })

  it('shows the newest twenty events, newest first', () => {
    const events = Array.from({ length: 25 }, (_, i) => ({ timestamp: `t${i}`, description: `event ${i}` }))
    seed({ events })

    const messages = screen.getAllByText(/^event \d+$/).map((el) => el.textContent)
    expect(messages).toHaveLength(20)
    expect(messages[0]).toBe('event 24')
    expect(messages.at(-1)).toBe('event 5')
  })

  it('says so when there are no events', () => {
    seed({ events: [] })

    expect(screen.getByText(/no events recorded yet/i)).toBeInTheDocument()
  })
})
