import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore } from '../test/store-harness.jsx'
import NPCManager from './NPCManager.jsx'

let fetchNpcs

const NPCS = [
  { name: 'Thaddeus', uuid: 'Actor.abc', type: 'npc', hp: 12, max_hp: 24 },
  { name: 'Mara', uuid: 'Actor.def', type: 'npc', hp: 30, max_hp: 30 },
]

function seed(extra = {}) {
  return renderWithStore(<NPCManager />, {
    fetchNpcs,
    engineStatus: { connected: true },
    npcs: [],
    ...extra,
  })
}

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
  fetchNpcs = vi.fn(async () => {})
})

afterEach(resetStore)

describe('NPCManager', () => {
  it('loads NPCs on mount and again on refresh', async () => {
    const user = userEvent.setup()
    seed()
    expect(fetchNpcs).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /refresh from foundry/i }))

    expect(fetchNpcs).toHaveBeenCalledTimes(2)
  })

  it('warns when the relay is not connected', () => {
    seed({ engineStatus: { connected: false } })

    expect(screen.getByText(/not connected to foundryvtt/i)).toBeInTheDocument()
  })

  it('warns when the engine status is unknown', () => {
    seed({ engineStatus: null })

    expect(screen.getByText(/not connected to foundryvtt/i)).toBeInTheDocument()
  })

  it('does not warn when the relay is connected', () => {
    seed()

    expect(screen.queryByText(/not connected to foundryvtt/i)).not.toBeInTheDocument()
  })

  it('shows an empty state when there are no NPCs', () => {
    seed()

    expect(screen.getByText(/no npcs found/i)).toBeInTheDocument()
  })

  it('lists NPCs and asks for a selection before showing details', () => {
    seed({ npcs: NPCS })

    expect(screen.getByRole('button', { name: 'Thaddeus' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mara' })).toBeInTheDocument()
    expect(screen.getByText(/select an npc to view details/i)).toBeInTheDocument()
  })

  it('labels an NPC with no name rather than rendering a blank row', () => {
    seed({ npcs: [{ uuid: 'Actor.ghi' }] })

    expect(screen.getByRole('button', { name: 'Unnamed NPC' })).toBeInTheDocument()
  })

  it('filters by name, case-insensitively', async () => {
    const user = userEvent.setup()
    seed({ npcs: NPCS })

    await user.type(screen.getByPlaceholderText(/search npcs/i), 'mar')

    expect(screen.getByRole('button', { name: 'Mara' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Thaddeus' })).not.toBeInTheDocument()
  })

  it('falls back to the empty state when the filter matches nothing', async () => {
    const user = userEvent.setup()
    seed({ npcs: NPCS })

    await user.type(screen.getByPlaceholderText(/search npcs/i), 'nobody')

    expect(screen.getByText(/no npcs found/i)).toBeInTheDocument()
  })

  it('shows the selected NPC\'s details', async () => {
    const user = userEvent.setup()
    seed({ npcs: NPCS })

    await user.click(screen.getByRole('button', { name: 'Thaddeus' }))

    expect(screen.getByRole('heading', { name: 'Thaddeus' })).toBeInTheDocument()
    expect(screen.getByText('12 / 24')).toBeInTheDocument()
    expect(screen.getByText('Actor.abc')).toBeInTheDocument()
  })

  it('omits the HP bar when the NPC has no max HP', async () => {
    const user = userEvent.setup()
    seed({ npcs: [{ name: 'Ghost', hp: 5 }] })

    await user.click(screen.getByRole('button', { name: 'Ghost' }))

    expect(screen.queryByText('HP')).not.toBeInTheDocument()
  })

  it('marks a player-owned NPC', async () => {
    const user = userEvent.setup()
    seed({ npcs: [{ name: 'Sidekick', type: 'character', has_player_owner: true }] })

    await user.click(screen.getByRole('button', { name: 'Sidekick' }))

    expect(screen.getByText('Player-owned')).toBeInTheDocument()
    expect(screen.getByText('character')).toBeInTheDocument()
  })

  it('dumps unrecognised Foundry fields rather than dropping them', async () => {
    const user = userEvent.setup()
    seed({ npcs: [{ name: 'Thaddeus', disposition: 'hostile', cr: 4 }] })

    await user.click(screen.getByRole('button', { name: 'Thaddeus' }))

    expect(screen.getByText(/"disposition": "hostile"/)).toBeInTheDocument()
    expect(screen.getByText(/"cr": 4/)).toBeInTheDocument()
    // Pins the locator the "no dump" test below relies on: without this, that
    // test would pass even if the dump were always rendered.
    expect(screen.getByText(/^\{/)).toBeInTheDocument()
  })

  it('shows no JSON dump when every field is a known one', async () => {
    const user = userEvent.setup()
    seed({ npcs: [{ name: 'Mara', uuid: 'Actor.def', type: 'npc', hp: 30, max_hp: 30, has_player_owner: false }] })

    await user.click(screen.getByRole('button', { name: 'Mara' }))

    expect(screen.queryByText(/^\{/)).not.toBeInTheDocument()
  })

  it('clamps an over-full HP bar to 100%', async () => {
    const user = userEvent.setup()
    seed({ npcs: [{ name: 'Buffed', hp: 40, max_hp: 20 }] })

    await user.click(screen.getByRole('button', { name: 'Buffed' }))

    const bar = screen.getByText('40 / 20').closest('div').nextSibling.firstChild
    expect(bar).toHaveStyle({ width: '100%' })
  })

  it('clamps a negative HP bar to 0%', async () => {
    const user = userEvent.setup()
    seed({ npcs: [{ name: 'Downed', hp: -5, max_hp: 20 }] })

    await user.click(screen.getByRole('button', { name: 'Downed' }))

    const bar = screen.getByText('-5 / 20').closest('div').nextSibling.firstChild
    expect(bar).toHaveStyle({ width: '0%' })
  })

  it('hides the roster behind the spoiler wall while play mode is on', () => {
    seed({ npcs: NPCS, ...inPlayMode('Greenrest') })

    expect(screen.getByText(/play mode active/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Thaddeus' })).not.toBeInTheDocument()
  })

  it('disables the search box while play mode is on', () => {
    seed({ npcs: NPCS, ...inPlayMode('Greenrest') })

    expect(screen.getByPlaceholderText(/search npcs/i)).toBeDisabled()
  })

  it('reveals the roster once the operator accepts the spoiler', async () => {
    const user = userEvent.setup()
    seed({ npcs: NPCS, ...inPlayMode('Greenrest') })

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByRole('button', { name: 'Thaddeus' })).toBeInTheDocument()
  })

  it('does not wall the roster when play mode is on for another campaign', () => {
    seed({ npcs: NPCS, ...inPlayMode('Greenrest'), playModeSessions: { Blackmoor: true } })

    expect(screen.queryByText(/play mode active/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Thaddeus' })).toBeInTheDocument()
  })
})
