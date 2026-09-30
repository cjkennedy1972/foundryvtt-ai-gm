import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore } from '../test/store-harness.jsx'
import CampaignStart from './CampaignStart.jsx'

let actions

const CAMPAIGN = { name: 'Greenrest', description: 'A drowned duchy' }

function session(extra = {}) {
  return { campaigns: [], selectedCampaign: null, activeSession: null, loading: false, error: null, ...extra }
}

function seed({ campaignSession: cs = {}, ...extra } = {}) {
  return renderWithStore(<CampaignStart />, {
    ...actions,
    campaignSession: session(cs),
    ...extra,
  })
}

beforeEach(() => {
  actions = {
    fetchActiveSession: vi.fn(async () => {}),
    listCampaigns: vi.fn(async () => {}),
    getCampaign: vi.fn(async () => ({})),
    deployCampaign: vi.fn(async () => ({ ok: true })),
    startCampaign: vi.fn(async () => ({ status: 'started' })),
    endSession: vi.fn(async () => ({ ok: true })),
    deleteCampaign: vi.fn(async () => ({ ok: true })),
    fetchStatus: vi.fn(async () => {}),
    fetchEvents: vi.fn(async () => {}),
    regenerateAssets: vi.fn(async () => ({ status: 'completed', maps_generated: 0, portraits_generated: 0 })),
    extendCampaignArc: vi.fn(async () => ({ ok: true, data: {} })),
    enrichCampaign: vi.fn(async () => ({ ok: true, data: {} })),
    teardownCampaign: vi.fn(async () => ({ ok: true, data: {} })),
    restartCampaign: vi.fn(async () => ({ ok: true, data: {} })),
    optimizeCampaign: vi.fn(async () => ({ ok: true, data: {} })),
  }
})

afterEach(resetStore)

describe('CampaignStart', () => {
  it('loads the active session and the campaign list on mount', () => {
    seed()

    expect(actions.fetchActiveSession).toHaveBeenCalledTimes(1)
    expect(actions.listCampaigns).toHaveBeenCalledTimes(1)
  })

  it('reloads both on refresh', async () => {
    const user = userEvent.setup()
    seed()

    await user.click(screen.getByRole('button', { name: /refresh/i }))

    expect(actions.fetchActiveSession).toHaveBeenCalledTimes(2)
    expect(actions.listCampaigns).toHaveBeenCalledTimes(2)
  })

  it('shows an empty state with a route out of it', () => {
    seed()

    expect(screen.getByText(/no campaigns found/i)).toBeInTheDocument()
    expect(screen.getByText(/use create campaign to build/i)).toBeInTheDocument()
  })

  it('shows a spinner rather than the empty state on first load', () => {
    seed({ campaignSession: { loading: true } })

    expect(screen.getByText(/loading campaigns/i)).toBeInTheDocument()
    expect(screen.queryByText(/no campaigns found/i)).not.toBeInTheDocument()
  })

  it('keeps showing the list while it reloads', () => {
    seed({ campaignSession: { loading: true, campaigns: [CAMPAIGN] } })

    expect(screen.queryByText(/loading campaigns/i)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Greenrest' })).toBeInTheDocument()
  })

  it('surfaces a list error', () => {
    seed({ campaignSession: { error: 'vault unreachable' } })

    expect(screen.getByText(/vault unreachable/)).toBeInTheDocument()
  })

  it('offers no active-session banner when nothing is running', () => {
    seed()

    expect(screen.queryByText(/active session/i)).not.toBeInTheDocument()
    expect(screen.getByText(/deploy, launch, and manage/i)).toBeInTheDocument()
  })

  it('names the campaign in the active-session banner', () => {
    seed({ campaignSession: { activeSession: { session_id: 's1', campaign_name: 'Greenrest' } } })

    expect(screen.getByText('Campaign: Greenrest')).toBeInTheDocument()
    expect(screen.getByText(/will end the current session/i)).toBeInTheDocument()
  })

  it('says a session is running even when it cannot name the campaign', () => {
    seed({ campaignSession: { activeSession: { session_id: 's1' } } })

    expect(screen.getByText('Session in progress')).toBeInTheDocument()
  })

  it('resumes the running session and refreshes status and events', async () => {
    const user = userEvent.setup()
    seed({ campaignSession: { activeSession: { session_id: 's1', campaign_name: 'Greenrest' } } })

    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(actions.startCampaign).toHaveBeenCalledWith('Greenrest', true)
    expect(actions.fetchStatus).toHaveBeenCalled()
    expect(actions.fetchEvents).toHaveBeenCalled()
  })

  it('does not refresh status and events when the start did not take', async () => {
    actions.startCampaign = vi.fn(async () => ({ status: 'error', error: 'relay down' }))
    const user = userEvent.setup()
    seed({ campaignSession: { activeSession: { session_id: 's1', campaign_name: 'Greenrest' } } })

    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(actions.fetchStatus).not.toHaveBeenCalled()
    expect(actions.fetchEvents).not.toHaveBeenCalled()
  })

  it('ends the session and re-reads what is running', async () => {
    const user = userEvent.setup()
    seed({ campaignSession: { activeSession: { session_id: 's1', campaign_name: 'Greenrest' } } })

    await user.click(screen.getByRole('button', { name: /end/i }))

    expect(actions.endSession).toHaveBeenCalled()
    expect(actions.fetchStatus).toHaveBeenCalled()
    expect(actions.fetchActiveSession).toHaveBeenCalledTimes(2)
  })

  it('does not claim the session ended when it did not', async () => {
    actions.endSession = vi.fn(async () => ({ error: 'no session to end' }))
    const user = userEvent.setup()
    seed({ campaignSession: { activeSession: { session_id: 's1', campaign_name: 'Greenrest' } } })

    await user.click(screen.getByRole('button', { name: /end/i }))

    expect(actions.fetchStatus).not.toHaveBeenCalled()
    expect(actions.fetchActiveSession).toHaveBeenCalledTimes(1)
  })

  it('locks the session controls while a request is in flight', () => {
    seed({ campaignSession: { loading: true, activeSession: { session_id: 's1', campaign_name: 'Greenrest' } } })

    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: '⏳' })).toBeDisabled()
  })

  describe('campaign card', () => {
    it('shows the name, description and content counts', () => {
      seed({ campaignSession: { campaigns: [{ ...CAMPAIGN, theme: 'dark fantasy', total_scenes: 8, total_npcs: 14, total_quests: 5 }] } })

      expect(screen.getByRole('heading', { name: 'Greenrest' })).toBeInTheDocument()
      expect(screen.getByText('A drowned duchy')).toBeInTheDocument()
      expect(screen.getByText('🎭 dark fantasy')).toBeInTheDocument()
      expect(screen.getByText('🗺️ 8 scenes')).toBeInTheDocument()
      expect(screen.getByText('👥 14 NPCs')).toBeInTheDocument()
      expect(screen.getByText('⚔️ 5 quests')).toBeInTheDocument()
    })

    it('shows zero counts rather than hiding them', () => {
      seed({ campaignSession: { campaigns: [{ ...CAMPAIGN, total_scenes: 0, total_npcs: 0, total_quests: 0 }] } })

      expect(screen.getByText('🗺️ 0 scenes')).toBeInTheDocument()
      expect(screen.getByText('👥 0 NPCs')).toBeInTheDocument()
      expect(screen.getByText('⚔️ 0 quests')).toBeInTheDocument()
    })

    it('omits counts the vault did not report', () => {
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      expect(screen.queryByText(/scenes$/)).not.toBeInTheDocument()
      expect(screen.queryByText(/🎭/)).not.toBeInTheDocument()
    })

    it('falls back to campaign_name when there is no name', () => {
      seed({ campaignSession: { campaigns: [{ campaign_name: 'Blackmoor' }] } })

      expect(screen.getByRole('heading', { name: 'Blackmoor' })).toBeInTheDocument()
    })

    it('labels a campaign with no name at all', () => {
      seed({ campaignSession: { campaigns: [{}] } })

      expect(screen.getByRole('heading', { name: 'Unnamed' })).toBeInTheDocument()
    })

    it('falls back to the summary, then to a placeholder, for the description', () => {
      seed({ campaignSession: { campaigns: [{ name: 'A', summary: 'A summary' }, { name: 'B' }] } })

      expect(screen.getByText('A summary')).toBeInTheDocument()
      expect(screen.getByText('No description available')).toBeInTheDocument()
    })

    it('deploys without starting a session', async () => {
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await user.click(screen.getByRole('button', { name: /deploy/i }))

      expect(actions.deployCampaign).toHaveBeenCalledWith('Greenrest')
      expect(actions.startCampaign).not.toHaveBeenCalled()
    })

    it('starts a fresh session', async () => {
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await user.click(screen.getByRole('button', { name: /start gm/i }))

      expect(actions.startCampaign).toHaveBeenCalledWith('Greenrest', false)
    })

    it('resumes from the last session', async () => {
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await user.click(screen.getByRole('button', { name: /continue/i }))

      expect(actions.startCampaign).toHaveBeenCalledWith('Greenrest', true)
    })

    it('asks before deleting, and does not delete on its own', async () => {
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await user.click(screen.getByRole('button', { name: '🗑️' }))

      expect(screen.getByText(/delete "greenrest"\? this cannot be undone/i)).toBeInTheDocument()
      expect(actions.deleteCampaign).not.toHaveBeenCalled()
    })

    it('deletes once confirmed, and drops the prompt', async () => {
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await user.click(screen.getByRole('button', { name: '🗑️' }))
      await user.click(screen.getByRole('button', { name: 'Delete' }))

      expect(actions.deleteCampaign).toHaveBeenCalledWith('Greenrest')
      expect(screen.queryByText(/cannot be undone/i)).not.toBeInTheDocument()
    })

    it('abandons the delete on cancel', async () => {
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await user.click(screen.getByRole('button', { name: '🗑️' }))
      await user.click(screen.getByRole('button', { name: 'Cancel' }))

      expect(actions.deleteCampaign).not.toHaveBeenCalled()
      expect(screen.queryByText(/cannot be undone/i)).not.toBeInTheDocument()
    })

    it('prompts for only the campaign whose bin was clicked', async () => {
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [{ name: 'Greenrest' }, { name: 'Blackmoor' }] } })

      await user.click(screen.getAllByRole('button', { name: '🗑️' })[1])

      expect(screen.getByText(/delete "blackmoor"\?/i)).toBeInTheDocument()
      expect(screen.queryByText(/delete "greenrest"\?/i)).not.toBeInTheDocument()
    })

    it('regenerates assets and reports what it made', async () => {
      actions.regenerateAssets = vi.fn(async () => ({
        status: 'completed', maps_generated: 3, portraits_generated: 7,
        scenes_attached: 3, portraits_attached: 7,
      }))
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await act(async () => { await user.click(screen.getByRole('button', { name: /regenerate/i })) })

      expect(actions.regenerateAssets).toHaveBeenCalledWith('Greenrest')
      expect(await screen.findByText(/Regenerated 3 map\(s\), 7 portrait\(s\), attached 3 scenes and 7 NPCs\./)).toBeInTheDocument()
    })

    it('leaves out the attachment clause when nothing was attached', async () => {
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await act(async () => { await user.click(screen.getByRole('button', { name: /regenerate/i })) })

      expect(await screen.findByText('✅ Regenerated 0 map(s), 0 portrait(s).')).toBeInTheDocument()
    })

    it('shows progress and blocks a second regeneration while one runs', async () => {
      let release
      actions.regenerateAssets = vi.fn(() => new Promise((r) => { release = r }))
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await act(async () => { await user.click(screen.getByRole('button', { name: /regenerate/i })) })

      expect(screen.getByRole('button', { name: /maps…/i })).toBeDisabled()

      release({ status: 'completed', maps_generated: 1, portraits_generated: 1 })
      expect(await screen.findByRole('button', { name: /regenerate/i })).toBeEnabled()
    })

    it('says why regeneration failed', async () => {
      actions.regenerateAssets = vi.fn(async () => ({ status: 'error', error: 'ComfyUI unreachable' }))
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await act(async () => { await user.click(screen.getByRole('button', { name: /regenerate/i })) })

      expect(await screen.findByText(/⚠️ ComfyUI unreachable/)).toBeInTheDocument()
    })

    it('falls back to a generic reason when regeneration gives none', async () => {
      actions.regenerateAssets = vi.fn(async () => ({ status: 'error' }))
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await act(async () => { await user.click(screen.getByRole('button', { name: /regenerate/i })) })

      expect(await screen.findByText(/⚠️ Regeneration failed/)).toBeInTheDocument()
    })

    it('lists the first four per-asset errors', async () => {
      actions.regenerateAssets = vi.fn(async () => ({
        status: 'completed', maps_generated: 1, portraits_generated: 0,
        errors: ['e1', 'e2', 'e3', 'e4', 'e5'],
      }))
      const user = userEvent.setup()
      seed({ campaignSession: { campaigns: [CAMPAIGN] } })

      await act(async () => { await user.click(screen.getByRole('button', { name: /regenerate/i })) })

      expect(await screen.findByText('e4')).toBeInTheDocument()
      expect(screen.queryByText('e5')).not.toBeInTheDocument()
    })
  })
})
