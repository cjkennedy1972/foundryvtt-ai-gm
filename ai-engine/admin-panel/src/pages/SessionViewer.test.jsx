import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore } from '../test/store-harness.jsx'
import SessionViewer from './SessionViewer.jsx'

let fetchEvents
let fetchState

function seed(extra = {}) {
  return renderWithStore(<SessionViewer />, { fetchEvents, fetchState, ...extra })
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
})

afterEach(resetStore)

describe('SessionViewer', () => {
  it('loads events and state on mount', () => {
    seed()

    expect(fetchEvents).toHaveBeenCalledWith(100)
    expect(fetchState).toHaveBeenCalled()
  })

  it('refreshes events on demand, but not game state', async () => {
    const user = userEvent.setup()
    seed()
    fetchState.mockClear()

    await user.click(screen.getByRole('button', { name: /refresh/i }))

    expect(fetchEvents).toHaveBeenCalledTimes(2)
    expect(fetchEvents).toHaveBeenLastCalledWith(100)
    expect(fetchState).not.toHaveBeenCalled()
  })

  it('shows an empty state when there are no events', () => {
    seed()

    expect(screen.getByText(/no session events yet/i)).toBeInTheDocument()
  })

  it('renders the event log in the order the store supplies it', () => {
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

  it('hides the event log behind the spoiler wall while play mode is on', () => {
    seed({ ...inPlayMode('Greenrest'), events: [{ description: 'Party entered the mill' }] })

    expect(screen.getByText(/play mode active/i)).toBeInTheDocument()
    expect(screen.queryByText('Party entered the mill')).not.toBeInTheDocument()
  })

  it('reveals the event log once the operator accepts the spoiler', async () => {
    const user = userEvent.setup()
    seed({ ...inPlayMode('Greenrest'), events: [{ description: 'Party entered the mill' }] })

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByText('Party entered the mill')).toBeInTheDocument()
  })

  it('shows the empty state inside the revealed copy too', async () => {
    const user = userEvent.setup()
    seed(inPlayMode('Greenrest'))

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByText(/no session events yet/i)).toBeInTheDocument()
  })

  it('does not wall the log when play mode is off for the active campaign', () => {
    seed({
      ...inPlayMode('Greenrest'),
      playModeSessions: { Greenrest: false },
      events: [{ description: 'Party entered the mill' }],
    })

    expect(screen.queryByText(/play mode active/i)).not.toBeInTheDocument()
    expect(screen.getByText('Party entered the mill')).toBeInTheDocument()
  })

  it('does not wall the log when play mode is on for a different campaign', () => {
    seed({
      ...inPlayMode('Greenrest'),
      playModeSessions: { Blackmoor: true },
      events: [{ description: 'Party entered the mill' }],
    })

    expect(screen.queryByText(/play mode active/i)).not.toBeInTheDocument()
    expect(screen.getByText('Party entered the mill')).toBeInTheDocument()
  })

  it('does not wall the log when no campaign session is active', () => {
    seed({ playModeSessions: { Greenrest: true }, events: [{ description: 'Party entered the mill' }] })

    expect(screen.queryByText(/play mode active/i)).not.toBeInTheDocument()
    expect(screen.getByText('Party entered the mill')).toBeInTheDocument()
  })
})
