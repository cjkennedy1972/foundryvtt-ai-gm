import { act, fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore } from '../test/store-harness.jsx'
import Dashboard from './Dashboard.jsx'

let actions

const STATUS = {
  connected: true,
  model: 'qwen3-32b',
  conversation_length: 7,
  relay: { running: true },
  modules: {},
}

function seed(extra = {}) {
  return renderWithStore(<Dashboard />, { ...actions, engineStatus: STATUS, ...extra })
}

/**
 * The relay control with exactly this label. Matching loosely will not do:
 * "Restart" contains "Start", so /start/i finds two buttons.
 */
function relayButton(label) {
  return screen.getByRole('button', { name: new RegExp(`^\\S+ ${label}$`) })
}

/** A status whose relay sub-object is STATUS's with `relay` merged over it. */
function withRelay(relay) {
  return { engineStatus: { ...STATUS, relay: { ...STATUS.relay, ...relay } } }
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
    relayStart: vi.fn(async () => ({})),
    relayStop: vi.fn(async () => ({})),
    relayRestart: vi.fn(async () => ({})),
    headlessStart: vi.fn(async () => ({ client_id: 'foundry-7' })),
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
    [{ running: true, crashed: false }, 'Relay Up'],
    [{ running: false }, 'Relay Down'],
    // Crashed wins over running: a crashed relay that still reports running is
    // the case the operator most needs to see.
    [{ running: true, crashed: true }, 'Relay Crashed'],
  ])('reports the relay state as %o', (relay, label) => {
    seed(withRelay(relay))

    expect(screen.getByText(label)).toBeInTheDocument()
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

  it('links to the relay dashboard when one is managed', () => {
    seed(withRelay({ dashboard_url: 'http://relay.local' }))

    expect(screen.getByRole('link', { name: /open relay dashboard/i })).toHaveAttribute('href', 'http://relay.local')
  })

  it('says the relay is unmanaged when there is no dashboard URL', () => {
    seed()

    expect(screen.getByText('Not managed')).toBeInTheDocument()
  })

  it('counts relay restarts only once there have been some', () => {
    const { unmount } = seed(withRelay({ restarts: 3 }))
    expect(screen.getByText(/\(3 restarts\)/)).toBeInTheDocument()

    unmount()
    seed(withRelay({ restarts: 0 }))
    expect(screen.queryByText(/restarts\)/)).not.toBeInTheDocument()
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

  it('pauses and resumes the AI', async () => {
    const user = userEvent.setup()
    seed({ aiRunning: true })

    await user.click(screen.getByRole('button', { name: /pause/i }))
    expect(actions.pauseAI).toHaveBeenCalled()
  })

  it('offers only the relay control that would change something', () => {
    const { unmount } = seed(withRelay({ running: true }))
    expect(relayButton('Start')).toBeDisabled()
    expect(relayButton('Stop')).toBeEnabled()

    unmount()
    seed(withRelay({ running: false }))
    expect(relayButton('Start')).toBeEnabled()
    expect(relayButton('Stop')).toBeDisabled()
  })

  it('disables every relay control when the relay was adopted from outside', () => {
    seed(withRelay({ adopted: true }))

    expect(screen.getByText(/external — controls disabled/i)).toBeInTheDocument()
    for (const label of ['Start', 'Stop', 'Restart']) {
      expect(relayButton(label)).toBeDisabled()
    }
  })

  it.each([
    ['Start', 'relayStart', /relay started/i],
    ['Stop', 'relayStop', /relay stopped/i],
    ['Restart', 'relayRestart', /relay restarted/i],
  ])('runs the %s action and confirms it', async (label, action, confirmation) => {
    const user = userEvent.setup()
    seed(withRelay({ running: label !== 'Start' }))

    await user.click(relayButton(label))

    expect(actions[action]).toHaveBeenCalled()
    expect(await screen.findByText(confirmation)).toBeInTheDocument()
  })

  it('names the Foundry client when a headless session comes up', async () => {
    const user = userEvent.setup()
    seed()

    await user.click(screen.getByRole('button', { name: /launch foundry session/i }))

    expect(actions.headlessStart).toHaveBeenCalled()
    expect(await screen.findByText(/foundry session up \(foundry-7\)/i)).toBeInTheDocument()
  })

  it('falls back to "connected" when the headless start reports no client', async () => {
    actions.headlessStart = vi.fn(async () => ({}))
    const user = userEvent.setup()
    seed()

    await user.click(screen.getByRole('button', { name: /launch foundry session/i }))

    expect(await screen.findByText(/foundry session up \(connected\)/i)).toBeInTheDocument()
  })

  it('reports that a Foundry session is already up', () => {
    seed(withRelay({ headless_client_id: 'foundry-7' }))

    expect(screen.getByRole('button', { name: /foundry session up/i })).toBeInTheDocument()
  })

  it('cannot launch a Foundry session while the relay is down', () => {
    seed(withRelay({ running: false }))

    expect(screen.getByRole('button', { name: /launch foundry session/i })).toBeDisabled()
  })

  it('surfaces an error the relay action returned', async () => {
    actions.relayRestart = vi.fn(async () => ({ error: 'relay binary missing' }))
    const user = userEvent.setup()
    seed()

    await user.click(relayButton('Restart'))

    expect(await screen.findByText('relay binary missing')).toBeInTheDocument()
  })

  it('surfaces an error the relay action threw', async () => {
    actions.relayRestart = vi.fn(async () => { throw new Error('socket hang up') })
    const user = userEvent.setup()
    seed()

    await user.click(relayButton('Restart'))

    expect(await screen.findByText('socket hang up')).toBeInTheDocument()
  })

  it('clears the relay message after four seconds', async () => {
    vi.useFakeTimers()
    seed()

    await act(async () => { fireEvent.click(relayButton('Restart')) })
    expect(screen.getByText(/relay restarted/i)).toBeInTheDocument()

    await act(async () => { vi.advanceTimersByTime(4000) })

    expect(screen.queryByText(/relay restarted/i)).not.toBeInTheDocument()
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
