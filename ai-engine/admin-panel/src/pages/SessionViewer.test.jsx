import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore } from '../test/store-harness.jsx'
import SessionViewer from './SessionViewer.jsx'

let fetchEvents
let fetchState
let fetchInteractiveSessions

const SESSION = {
  sessionId: 'sess-1',
  state: 'active',
  clientId: 'foundry-1',
  consumerId: 'engine-1',
  createdAt: '2026-01-02T03:04:05Z',
  lastActivity: '2026-01-02T03:09:05Z',
}

function seed(extra = {}) {
  return renderWithStore(<SessionViewer />, {
    fetchEvents,
    fetchState,
    fetchInteractiveSessions,
    ...extra,
  })
}

/** Play mode on for `name`, with that campaign as the active session. */
function inPlayMode(name) {
  return {
    campaignSession: {
      campaigns: [], selectedCampaign: null, loading: false, error: null,
      activeSession: { session_id: 's1', campaign_name: name },
    },
    playModeSessions: { [name]: true },
  }
}

beforeEach(() => {
  fetchEvents = vi.fn(async () => {})
  fetchState = vi.fn(async () => {})
  fetchInteractiveSessions = vi.fn(async () => {})
})

afterEach(resetStore)

describe('SessionViewer', () => {
  it('loads events, state and sessions on mount', () => {
    seed()

    expect(fetchEvents).toHaveBeenCalledWith(100)
    expect(fetchState).toHaveBeenCalled()
    expect(fetchInteractiveSessions).toHaveBeenCalled()
  })

  it('refreshes events and sessions on demand, but not game state', async () => {
    const user = userEvent.setup()
    seed()
    fetchState.mockClear()

    await user.click(screen.getByRole('button', { name: /refresh/i }))

    expect(fetchEvents).toHaveBeenCalledTimes(2)
    expect(fetchInteractiveSessions).toHaveBeenCalledTimes(2)
    expect(fetchState).not.toHaveBeenCalled()
  })

  it('shows empty states when there is nothing to show', () => {
    seed()

    expect(screen.getByText(/no active interactive sessions/i)).toBeInTheDocument()
    expect(screen.getByText(/no session events yet/i)).toBeInTheDocument()
  })

  it('lists each interactive session with its state and endpoints', () => {
    seed({ interactiveSessions: [SESSION] })

    expect(screen.getByText('sess-1')).toBeInTheDocument()
    expect(screen.getByText('active')).toBeInTheDocument()
    expect(screen.getByText(/Client: foundry-1/)).toBeInTheDocument()
    expect(screen.getByText(/Consumer: engine-1/)).toBeInTheDocument()
    expect(screen.queryByText(/no active interactive sessions/i)).not.toBeInTheDocument()
  })

  it('omits quality and scale when the relay did not report them', () => {
    seed({ interactiveSessions: [SESSION] })

    expect(screen.queryByText(/Quality:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Scale:/)).not.toBeInTheDocument()
  })

  it('shows quality and scale when the relay did report them', () => {
    seed({ interactiveSessions: [{ ...SESSION, quality: 'high', scale: '2x' }] })

    expect(screen.getByText(/Quality: high/)).toBeInTheDocument()
    expect(screen.getByText(/Scale: 2x/)).toBeInTheDocument()
  })

  it('marks a session that is not active as pending', () => {
    seed({ interactiveSessions: [{ ...SESSION, state: 'connecting' }] })

    expect(screen.getByText('connecting')).toHaveClass('badge-pending')
  })

  it('renders the event log newest-first as the store supplies it', () => {
    seed({
      events: [
        { timestamp: '03:04', description: 'Party entered the mill' },
        { timestamp: '03:05', description: 'Rogue picked the lock' },
      ],
    })

    expect(screen.getByText('Party entered the mill')).toBeInTheDocument()
    expect(screen.getByText('Rogue picked the lock')).toBeInTheDocument()
    expect(screen.queryByText(/no session events yet/i)).not.toBeInTheDocument()
  })

  it('renders an event with no timestamp without printing "undefined"', () => {
    seed({ events: [{ description: 'Unstamped event' }] })

    expect(screen.getByText('Unstamped event')).toBeInTheDocument()
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument()
  })

  it('announces that the AI is listening only while it is running', () => {
    const { unmount } = seed({ aiRunning: true })
    expect(screen.getByText(/listening to player messages/i)).toBeInTheDocument()

    unmount()
    seed({ aiRunning: false })
    expect(screen.queryByText(/listening to player messages/i)).not.toBeInTheDocument()
  })

  it('hides live state behind the spoiler wall while play mode is on', () => {
    seed({ ...inPlayMode('Greenrest'), events: [{ description: 'Party entered the mill' }] })

    expect(screen.getByText(/play mode active/i)).toBeInTheDocument()
    expect(screen.queryByText('Party entered the mill')).not.toBeInTheDocument()
  })

  it('reveals the panels once the operator accepts the spoiler', async () => {
    const user = userEvent.setup()
    seed({ ...inPlayMode('Greenrest'), events: [{ description: 'Party entered the mill' }] })

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByText('Party entered the mill')).toBeInTheDocument()
  })

  // The two panels are rendered twice — inside SpoilerWall and outside — so
  // the revealed copy's own conditionals need exercising as well.
  it('renders the revealed copy\'s sessions, events and optional fields', async () => {
    const user = userEvent.setup()
    seed({
      ...inPlayMode('Greenrest'),
      interactiveSessions: [{ ...SESSION, state: 'connecting', quality: 'high', scale: '2x' }],
      events: [{ description: 'Unstamped event' }],
    })

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByText('sess-1')).toBeInTheDocument()
    expect(screen.getByText('connecting')).toHaveClass('badge-pending')
    expect(screen.getByText(/Quality: high/)).toBeInTheDocument()
    expect(screen.getByText(/Scale: 2x/)).toBeInTheDocument()
    expect(screen.getByText('Unstamped event')).toBeInTheDocument()
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument()
  })

  it('shows both empty states in the revealed copy', async () => {
    const user = userEvent.setup()
    seed(inPlayMode('Greenrest'))

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByText(/no active interactive sessions/i)).toBeInTheDocument()
    expect(screen.getByText(/no session events yet/i)).toBeInTheDocument()
  })

  it('does not wall the panels when play mode is off for the active campaign', () => {
    seed({
      ...inPlayMode('Greenrest'),
      playModeSessions: { Greenrest: false },
      events: [{ description: 'Party entered the mill' }],
    })

    expect(screen.queryByText(/play mode active/i)).not.toBeInTheDocument()
    expect(screen.getByText('Party entered the mill')).toBeInTheDocument()
  })

  it('does not wall the panels when play mode is on for a different campaign', () => {
    seed({
      ...inPlayMode('Greenrest'),
      playModeSessions: { Blackmoor: true },
      events: [{ description: 'Party entered the mill' }],
    })

    expect(screen.queryByText(/play mode active/i)).not.toBeInTheDocument()
    expect(screen.getByText('Party entered the mill')).toBeInTheDocument()
  })

  it('does not wall the panels when no campaign session is active', () => {
    seed({ playModeSessions: { Greenrest: true }, events: [{ description: 'Party entered the mill' }] })

    expect(screen.queryByText(/play mode active/i)).not.toBeInTheDocument()
    expect(screen.getByText('Party entered the mill')).toBeInTheDocument()
  })
})
