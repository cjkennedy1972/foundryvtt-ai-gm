import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore, useStore } from '../test/store-harness.jsx'
import Overrides from './Overrides.jsx'

let actions

// The setters (setChatTest, setRollForm, setSrdQuery) are left as the real
// store actions so the controls are checked against the state the page's own
// actions read back.
function seed(extra = {}) {
  return renderWithStore(<Overrides />, { ...actions, ...extra })
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
  actions = {
    testChat: vi.fn(async () => {}),
    performRoll: vi.fn(async () => {}),
    searchSrd: vi.fn(async () => {}),
    pauseAI: vi.fn(async () => {}),
    resumeAI: vi.fn(async () => {}),
  }
})

afterEach(resetStore)

describe('Overrides', () => {
  it('offers only the AI control that would change something', async () => {
    const user = userEvent.setup()
    const { unmount } = seed({ aiRunning: true })

    expect(screen.getByText('Active')).toHaveClass('badge-connected')
    await user.click(screen.getByRole('button', { name: /pause ai/i }))
    expect(actions.pauseAI).toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /resume ai/i })).not.toBeInTheDocument()

    unmount()
    seed({ aiRunning: false })
    expect(screen.getByText('Paused')).toHaveClass('badge-disconnected')
    await user.click(screen.getByRole('button', { name: /resume ai/i }))
    expect(actions.resumeAI).toHaveBeenCalled()
  })

  it('shows the configured AI name, or the default when unset', () => {
    const { unmount } = seed({ settings: { ...useStore.getState().settings, aiName: 'Sage' } })
    expect(screen.getByText('Sage')).toBeInTheDocument()

    unmount()
    seed()
    expect(screen.getByText('Aethelwyrd AI')).toBeInTheDocument()
  })

  it('shows the websocket endpoint the panel would connect to', () => {
    seed()

    expect(screen.getByText(`ws://${location.host}/api/ws`)).toBeInTheDocument()
  })

  it('sends a simulated player message', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText('Player name'), 'Ranger')
    await user.type(screen.getByPlaceholderText(/what the player says/i), 'I search the mill')
    await user.click(screen.getByRole('button', { name: /send to ai/i }))

    expect(useStore.getState().chatTest.speaker).toBe('Ranger')
    expect(useStore.getState().chatTest.message).toBe('I search the mill')
    expect(actions.testChat).toHaveBeenCalled()
  })

  it('sends the simulated message on Enter', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(/what the player says/i), 'I search the mill{Enter}')

    expect(actions.testChat).toHaveBeenCalledTimes(1)
  })

  it('shows progress and blocks a second send while one is in flight', () => {
    seed({ chatTest: { message: 'x', speaker: 'y', result: null, loading: true } })

    expect(screen.getByRole('button', { name: /processing/i })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /send to ai/i })).not.toBeInTheDocument()
  })

  it('shows nothing where the AI preview goes until there is a result', () => {
    seed()

    expect(screen.queryByText(/"narration"/)).not.toBeInTheDocument()
  })

  it('dumps the AI preview verbatim', () => {
    seed({ chatTest: { message: '', speaker: '', loading: false, result: { narration: 'Dust and rat bones.' } } })

    expect(screen.getByText(/"narration": "Dust and rat bones\."/)).toBeInTheDocument()
  })

  it('shows an errored preview rather than swallowing it', () => {
    seed({ chatTest: { message: '', speaker: '', loading: false, result: { error: 'engine offline' } } })

    expect(screen.getByText(/"error": "engine offline"/)).toBeInTheDocument()
  })

  it('defaults the roll form to a d20 rolled by the GM', () => {
    seed()

    expect(screen.getByDisplayValue('1d20')).toBeInTheDocument()
    expect(screen.getByDisplayValue('GM')).toBeInTheDocument()
  })

  // The roll form's two inputs have no placeholder and their <label>s are not
  // associated with them, so there is nothing to query them by but their
  // current value. Seeding distinct values keeps each one findable.
  it('rolls the formula in the form', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByDisplayValue('1d20'), '+3')
    await user.click(screen.getByRole('button', { name: 'Roll' }))

    expect(useStore.getState().rollForm.formula).toBe('1d20+3')
    expect(actions.performRoll).toHaveBeenCalled()
  })

  it('renames the speaker on the roll', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByDisplayValue('GM'), '-2')

    expect(useStore.getState().rollForm.speaker).toBe('GM-2')
  })

  it.each(['1d20', '2d6', '4d8+3', '8d6', '1d4', '1d100', '1d20+5', '2d20 advantage'])(
    'fills the formula from the %s shortcut', async (template) => {
      const user = userEvent.setup()
      seed({ rollForm: { formula: '', speaker: 'GM', flavor: '' } })

      await user.click(screen.getByRole('button', { name: template }))

      expect(useStore.getState().rollForm.formula).toBe(template)
    },
  )

  it('shows nothing where the roll result goes until there is one', () => {
    seed()

    expect(screen.queryByText(/"total"/)).not.toBeInTheDocument()
  })

  it('dumps the roll result verbatim', () => {
    seed({ rollResult: { total: 17, formula: '1d20' } })

    expect(screen.getByText(/"total": 17/)).toBeInTheDocument()
  })

  it('searches the SRD from the button', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(/search rules/i), 'Stealth')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(useStore.getState().srdQuery).toBe('Stealth')
    expect(actions.searchSrd).toHaveBeenCalled()
  })

  it('searches the SRD on Enter', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(/search rules/i), 'Stealth{Enter}')

    expect(actions.searchSrd).toHaveBeenCalledTimes(1)
  })

  it('shows the SRD results as the store left them', () => {
    seed({ srdResults: 'Stealth: Dexterity check.' })

    expect(screen.getByText('Stealth: Dexterity check.')).toBeInTheDocument()
  })

  it('shows an SRD error as results rather than silently', () => {
    seed({ srdResults: 'Error: engine offline' })

    expect(screen.getByText('Error: engine offline')).toBeInTheDocument()
  })

  it('hides the tools behind the spoiler wall while play mode is on', () => {
    seed({ ...inPlayMode('Greenrest'), srdResults: 'Stealth: Dexterity check.' })

    expect(screen.getByText(/play mode active/i)).toBeInTheDocument()
    expect(screen.queryByText('Stealth: Dexterity check.')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /send to ai/i })).not.toBeInTheDocument()
  })

  it('leaves the status cards outside the wall so they stay readable', () => {
    seed({ ...inPlayMode('Greenrest'), aiRunning: true })

    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /pause ai/i })).toBeInTheDocument()
  })

  it('reveals the tools once the operator accepts the spoiler', async () => {
    const user = userEvent.setup()
    seed({ ...inPlayMode('Greenrest'), srdResults: 'Stealth: Dexterity check.' })

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByText('Stealth: Dexterity check.')).toBeInTheDocument()
  })

  // Each of these pages renders its panels twice — once inside SpoilerWall and
  // once outside — so a fix applied to one copy only would go unnoticed. These
  // drive the revealed copy's own controls.
  it('drives the revealed copy\'s controls, not just the unwalled ones', async () => {
    const user = userEvent.setup()
    seed(inPlayMode('Greenrest'))
    await user.click(screen.getByRole('button', { name: /show me/i }))

    await user.type(screen.getByPlaceholderText('Player name'), 'Ranger')
    await user.type(screen.getByPlaceholderText(/what the player says/i), 'I search the mill{Enter}')
    expect(useStore.getState().chatTest).toMatchObject({ speaker: 'Ranger', message: 'I search the mill' })
    expect(actions.testChat).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /send to ai/i }))
    expect(actions.testChat).toHaveBeenCalledTimes(2)

    await user.type(screen.getByDisplayValue('1d20'), '+3')
    await user.type(screen.getByDisplayValue('GM'), '-2')
    await user.click(screen.getByRole('button', { name: '2d6' }))
    await user.click(screen.getByRole('button', { name: 'Roll' }))
    expect(useStore.getState().rollForm).toMatchObject({ formula: '2d6', speaker: 'GM-2' })
    expect(actions.performRoll).toHaveBeenCalledTimes(1)

    await user.type(screen.getByPlaceholderText(/search rules/i), 'Stealth{Enter}')
    expect(useStore.getState().srdQuery).toBe('Stealth')
    expect(actions.searchSrd).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(actions.searchSrd).toHaveBeenCalledTimes(2)
  })

  it('shows progress in the revealed copy too', async () => {
    const user = userEvent.setup()
    seed({ ...inPlayMode('Greenrest'), chatTest: { message: 'x', speaker: 'y', result: { narration: 'n' }, loading: true } })

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByRole('button', { name: /processing/i })).toBeDisabled()
    expect(screen.getByText(/"narration": "n"/)).toBeInTheDocument()
  })

  it('shows a roll result in the revealed copy too', async () => {
    const user = userEvent.setup()
    seed({ ...inPlayMode('Greenrest'), rollResult: { total: 17 } })

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByText(/"total": 17/)).toBeInTheDocument()
  })

  it('does not wall the tools when play mode is on for another campaign', () => {
    seed({ ...inPlayMode('Greenrest'), playModeSessions: { Blackmoor: true } })

    expect(screen.queryByText(/play mode active/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /send to ai/i })).toBeInTheDocument()
  })
})
