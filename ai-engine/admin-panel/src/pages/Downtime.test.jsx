import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore, useStore } from '../test/store-harness.jsx'
import Downtime from './Downtime.jsx'

const OUTCOME = 'You find their sign twice and lose it twice.'

let fetchPendingDowntime
let submitDowntimeTurn

function seed(extra = {}) {
  return renderWithStore(<Downtime />, {
    fetchPendingDowntime,
    submitDowntimeTurn,
    pendingDowntime: [],
    campaignSession: { campaigns: [], selectedCampaign: null, activeSession: null, loading: false, error: null },
    ...extra,
  })
}

const activeCampaign = (name) => ({
  campaigns: [], selectedCampaign: null, loading: false, error: null,
  activeSession: { session_id: 's1', campaign_name: name },
})

beforeEach(() => {
  fetchPendingDowntime = vi.fn(async () => {})
  submitDowntimeTurn = vi.fn(async () => ({ ok: true, data: { resolved: true, player: 'Ranger' } }))
})

afterEach(resetStore)

describe('Downtime', () => {
  it('fetches the pending list for the active campaign on mount', () => {
    seed({ campaignSession: activeCampaign('Greenrest') })

    expect(fetchPendingDowntime).toHaveBeenCalledWith('Greenrest')
  })

  it('falls back to an empty campaign when no session is active', () => {
    seed()

    expect(fetchPendingDowntime).toHaveBeenCalledWith('')
  })

  it('keeps the submit button disabled until both fields are filled', async () => {
    const user = userEvent.setup()
    seed()
    const submit = screen.getByRole('button', { name: /submit downtime turn/i })

    expect(submit).toBeDisabled()

    await user.type(screen.getByPlaceholderText('Ranger'), 'Ranger')
    expect(submit).toBeDisabled()

    await user.type(screen.getByPlaceholderText(/tracking the cult/i), 'tracks the cult')
    expect(submit).toBeEnabled()
  })

  it('treats whitespace as empty', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText('Ranger'), '   ')
    await user.type(screen.getByPlaceholderText(/tracking the cult/i), '   ')

    expect(screen.getByRole('button', { name: /submit downtime turn/i })).toBeDisabled()
  })

  it('submits the trimmed player and action with the campaign', async () => {
    const user = userEvent.setup()
    seed({ campaignSession: activeCampaign('Greenrest') })

    await user.type(screen.getByPlaceholderText('Ranger'), '  Ranger  ')
    await user.type(screen.getByPlaceholderText(/tracking the cult/i), '  tracks the cult  ')
    await user.click(screen.getByRole('button', { name: /submit downtime turn/i }))

    expect(submitDowntimeTurn).toHaveBeenCalledWith('Ranger', 'tracks the cult', 'Greenrest')
  })

  it('confirms without revealing what happened', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText('Ranger'), 'Ranger')
    await user.type(screen.getByPlaceholderText(/tracking the cult/i), 'tracks the cult')
    await user.click(screen.getByRole('button', { name: /submit downtime turn/i }))

    // The whole point of the feature: the operator is also a player, so the
    // receipt says it happened and never what came of it.
    expect(await screen.findByText(/you will hear how it went/i)).toBeInTheDocument()
    expect(screen.queryByText(OUTCOME)).not.toBeInTheDocument()
  })

  it('clears the action but keeps the character after a successful turn', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText('Ranger'), 'Ranger')
    await user.type(screen.getByPlaceholderText(/tracking the cult/i), 'tracks the cult')
    await user.click(screen.getByRole('button', { name: /submit downtime turn/i }))

    await screen.findByText(/you will hear how it went/i)
    expect(screen.getByPlaceholderText(/tracking the cult/i)).toHaveValue('')
    expect(screen.getByPlaceholderText('Ranger')).toHaveValue('Ranger')
  })

  it.each([
    ['empty_action', /give both a character and what they do/i],
    ['no_session', /no session history yet/i],
    ['llm_error', /could not be resolved/i],
    ['no_outcome', /produced nothing to narrate/i],
  ])('explains the %s stop reason in the operator\'s words', async (reason, expected) => {
    submitDowntimeTurn = vi.fn(async () => ({ ok: true, data: { resolved: false, stopped_reason: reason } }))
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText('Ranger'), 'Ranger')
    await user.type(screen.getByPlaceholderText(/tracking the cult/i), 'tracks the cult')
    await user.click(screen.getByRole('button', { name: /submit downtime turn/i }))

    expect(await screen.findByText(expected)).toBeInTheDocument()
  })

  it('surfaces a transport error when there is no stop reason', async () => {
    submitDowntimeTurn = vi.fn(async () => ({ ok: false, error: 'engine offline' }))
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText('Ranger'), 'Ranger')
    await user.type(screen.getByPlaceholderText(/tracking the cult/i), 'tracks the cult')
    await user.click(screen.getByRole('button', { name: /submit downtime turn/i }))

    expect(await screen.findByText('engine offline')).toBeInTheDocument()
  })

  it('shows an empty state when nothing is pending', () => {
    seed()

    expect(screen.getByText(/nothing pending/i)).toBeInTheDocument()
  })

  it('lists pending turns without their outcomes', () => {
    seed({
      pendingDowntime: [
        { player: 'Ranger', action: 'tracks the cult' },
        { player: 'Rogue', action: 'cases the mill' },
      ],
    })

    expect(screen.getByText('Ranger')).toBeInTheDocument()
    expect(screen.getByText('tracks the cult')).toBeInTheDocument()
    expect(screen.getByText('Rogue')).toBeInTheDocument()
    expect(screen.queryByText(/nothing pending/i)).not.toBeInTheDocument()
  })

  it('refetches when the active campaign changes', async () => {
    seed({ campaignSession: activeCampaign('Greenrest') })
    expect(fetchPendingDowntime).toHaveBeenLastCalledWith('Greenrest')

    // Inside act(), so the store update and the effect it triggers are both
    // flushed before the assertion.
    await act(async () => {
      useStore.setState({ campaignSession: activeCampaign('Blackmoor') })
    })

    expect(fetchPendingDowntime).toHaveBeenLastCalledWith('Blackmoor')
  })
})
