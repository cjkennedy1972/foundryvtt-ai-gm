import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore, useStore } from './test/store-harness.jsx'
import App from './App.jsx'

// App's job is the setup gate and the routing switch. Standing in for the
// pages keeps their own effects and fetches out of these assertions — each
// page is tested against the real store in its own file.
const { stub, relayAdminUrl } = vi.hoisted(() => ({
  stub: (name) => () => ({ default: () => <div>{name} page</div> }),
  relayAdminUrl: vi.fn(() => ''),
}))

vi.mock('./pages/Dashboard', stub('Dashboard'))
vi.mock('./pages/Settings', stub('Settings'))
vi.mock('./pages/SessionViewer', stub('SessionViewer'))
vi.mock('./pages/GMChat', stub('GMChat'))
vi.mock('./pages/CampaignBuilder', stub('CampaignBuilder'))
vi.mock('./pages/CampaignStart', stub('CampaignStart'))
vi.mock('./pages/NPCManager', stub('NPCManager'))
vi.mock('./pages/CanonReview', stub('CanonReview'))
vi.mock('./pages/Downtime', stub('Downtime'))
vi.mock('./pages/Overrides', stub('Overrides'))
vi.mock('./pages/SetupWizard', stub('SetupWizard'))

vi.mock('./config.js', async (original) => ({ ...(await original()), relayAdminUrl }))

let actions

/** Renders App and waits for the setup check to settle. */
async function seed(extra = {}) {
  const result = renderWithStore(<App />, { ...actions, ...extra })
  await waitFor(() => expect(screen.queryByText(/checking setup/i)).not.toBeInTheDocument())
  return result
}

function setupStatus(body) {
  globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => body }))
}

function activeCampaign(name) {
  return {
    campaignSession: {
      campaigns: [], selectedCampaign: null, loading: false, error: null,
      activeSession: { session_id: 's1', campaign_name: name },
    },
  }
}

beforeEach(() => {
  setupStatus({ complete: true })
  relayAdminUrl.mockReturnValue('')
  actions = {
    fetchStatus: vi.fn(async () => {}),
    fetchState: vi.fn(async () => {}),
    fetchSettings: vi.fn(async () => {}),
  }
})

afterEach(resetStore)

describe('App', () => {
  it('shows a placeholder while the setup check is in flight', () => {
    let resolve
    globalThis.fetch = vi.fn(() => new Promise((r) => { resolve = r }))
    renderWithStore(<App />, actions)

    expect(screen.getByText(/checking setup/i)).toBeInTheDocument()
    expect(screen.queryByText('Dashboard page')).not.toBeInTheDocument()

    // Settle the promise so the test does not leave one dangling.
    act(() => resolve({ ok: true, json: async () => ({ complete: true }) }))
  })

  it('loads status, state and settings on mount', async () => {
    await seed()

    expect(actions.fetchStatus).toHaveBeenCalledTimes(1)
    expect(actions.fetchState).toHaveBeenCalledTimes(1)
    expect(actions.fetchSettings).toHaveBeenCalledTimes(1)
  })

  it('sends the operator to the wizard when setup is incomplete', async () => {
    setupStatus({ complete: false })
    await seed()

    expect(screen.getByText('SetupWizard page')).toBeInTheDocument()
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  })

  it('assumes setup is complete when the check fails, rather than trapping the operator in the wizard', async () => {
    globalThis.fetch = vi.fn(async () => { throw new Error('engine down') })
    vi.spyOn(console, 'error').mockImplementation(() => {})
    await seed()

    expect(screen.getByText('Dashboard page')).toBeInTheDocument()
  })

  it.each([
    ['dashboard', 'Dashboard page'],
    ['settings', 'Settings page'],
    ['gm-chat', 'GMChat page'],
    ['session', 'SessionViewer page'],
    ['campaign-builder', 'CampaignBuilder page'],
    ['campaign-start', 'CampaignStart page'],
    ['npcs', 'NPCManager page'],
    ['canon-review', 'CanonReview page'],
    ['downtime', 'Downtime page'],
    ['overrides', 'Overrides page'],
  ])('routes %s to its page', async (activePage, expected) => {
    await seed({ activePage })

    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('falls back to the dashboard for a page id it does not know', async () => {
    await seed({ activePage: 'no-such-page' })

    expect(screen.getByText('Dashboard page')).toBeInTheDocument()
  })

  it('navigates when a sidebar item is clicked', async () => {
    const user = userEvent.setup()
    await seed()

    await user.click(screen.getByRole('button', { name: /GM Chat/ }))

    expect(useStore.getState().activePage).toBe('gm-chat')
    expect(screen.getByText('GMChat page')).toBeInTheDocument()
  })

  it('marks the current page for assistive tech, not just with a class', async () => {
    await seed({ activePage: 'downtime' })

    expect(screen.getByRole('button', { name: /Downtime/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('button', { name: /Dashboard/ })).not.toHaveAttribute('aria-current')
  })

  it('groups the nav by phase of play', async () => {
    await seed()

    // Matched on the label element, since "Campaigns" and "Live Session" are
    // also nav item labels.
    const labels = [...document.querySelectorAll('.nav-section-label')].map((el) => el.textContent)
    expect(labels).toEqual(['Campaigns', 'Live Session', 'Tools', 'Settings'])
  })

  it('offers no play-mode toggle until a campaign session is active', async () => {
    await seed()

    expect(screen.queryByRole('button', { name: /play mode/i })).not.toBeInTheDocument()
  })

  it('offers the play-mode toggle off once a session is active', async () => {
    await seed(activeCampaign('Greenrest'))

    expect(screen.getByRole('button', { name: /play mode: off/i })).toBeInTheDocument()
    expect(screen.queryByText(/spoiler surfaces are hidden/i)).not.toBeInTheDocument()
  })

  it('turns play mode on for the active campaign only', async () => {
    const user = userEvent.setup()
    await seed(activeCampaign('Greenrest'))

    await user.click(screen.getByRole('button', { name: /play mode: off/i }))

    expect(useStore.getState().playModeSessions).toEqual({ Greenrest: true })
    expect(screen.getByRole('button', { name: /play mode: on/i })).toBeInTheDocument()
    expect(screen.getByText(/spoiler surfaces are hidden/i)).toBeInTheDocument()
  })

  it('turns play mode back off', async () => {
    const user = userEvent.setup()
    await seed({ ...activeCampaign('Greenrest'), playModeSessions: { Greenrest: true } })

    await user.click(screen.getByRole('button', { name: /play mode: on/i }))

    expect(useStore.getState().playModeSessions).toEqual({ Greenrest: false })
  })

  it('de-emphasises the spoiler pages while play mode is on', async () => {
    await seed({ ...activeCampaign('Greenrest'), playModeSessions: { Greenrest: true } })

    for (const name of [/GM Chat/, /Live Session/, /NPC Manager/, /Canon Review/, /Dev Tools/]) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('title', '🛡️ Spoiler content')
    }
    // Pages that cannot spoil anything are left alone.
    for (const name of [/Dashboard/, /Create Campaign/, /Downtime/, /AI Settings/]) {
      expect(screen.getByRole('button', { name })).not.toHaveAttribute('title')
    }
  })

  it('leaves the spoiler pages alone while play mode is off', async () => {
    await seed(activeCampaign('Greenrest'))

    expect(screen.getByRole('button', { name: /GM Chat/ })).not.toHaveAttribute('title')
  })

  it('hides the relay admin link when none is configured', async () => {
    await seed()

    expect(screen.queryByRole('link', { name: /relay admin/i })).not.toBeInTheDocument()
  })

  it('links to the configured relay admin URL', async () => {
    relayAdminUrl.mockReturnValue('http://relay.local/admin')
    await seed()

    expect(screen.getByRole('link', { name: /relay admin/i })).toHaveAttribute('href', 'http://relay.local/admin')
  })
})
