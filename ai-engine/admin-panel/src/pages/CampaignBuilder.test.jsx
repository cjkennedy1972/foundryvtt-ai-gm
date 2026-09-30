import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore, useStore } from '../test/store-harness.jsx'
import CampaignBuilder from './CampaignBuilder.jsx'

let buildCampaign
let importCampaign

const DEFAULTS = useStore.getState().campaignWizard

// setWizardField and resetWizard stay real, so the form is checked against
// the state buildCampaign/importCampaign would actually read.
function seed(wizard = {}, extra = {}) {
  return renderWithStore(<CampaignBuilder />, {
    buildCampaign,
    importCampaign,
    campaignWizard: { ...DEFAULTS, ...wizard },
    ...extra,
  })
}

const wizard = () => useStore.getState().campaignWizard
const levelRangeBox = () => screen.getByPlaceholderText('e.g. 3-15')

beforeEach(() => {
  buildCampaign = vi.fn(async () => {})
  importCampaign = vi.fn(async () => {})
})

afterEach(resetStore)

describe('CampaignBuilder', () => {
  it.each([
    [/My New Campaign/, 'name', 'Greenrest'],
    [/Describe the world, tone/, 'description', 'A drowned duchy'],
    [/dark fantasy, steampunk/, 'theme', 'dark fantasy'],
    [/one-shot, short arc/, 'scale', 'short arc'],
    [/Key NPCs to include/, 'seedIdeas', 'the miller'],
    [/Describe your hero/, 'characterConcept', 'a quiet wizard'],
    [/Character name \(optional/, 'characterName', 'Mira'],
    [/published-campaign-folder/, 'importSourcePath', '/adventures/krynn'],
    [/ddb-krynn-ddb-journals/, 'importJournalPack', 'ddb-journals'],
  ])('edits the %s field', async (placeholder, key, value) => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(placeholder), value)

    expect(wizard()[key]).toBe(value)
  })

  it('edits the Foundry world name, offering the campaign name as the hint', async () => {
    const user = userEvent.setup()
    seed({ name: 'Greenrest' })

    const box = screen.getByPlaceholderText('Greenrest')
    await user.type(box, 'greenrest-world')

    expect(wizard().foundryWorldName).toBe('greenrest-world')
  })

  it('hints at the paired world when there is no campaign name yet', () => {
    seed()

    expect(screen.getByPlaceholderText('Paired world')).toBeInTheDocument()
  })

  it('generates a prologue unless the operator opts out', async () => {
    const user = userEvent.setup()
    seed()
    const box = screen.getByRole('checkbox')

    expect(box).toBeChecked()

    await user.click(box)
    expect(wizard().generatePrologue).toBe(false)
    expect(box).not.toBeChecked()
  })

  it('treats a prologue flag the server never set as on', () => {
    seed({ generatePrologue: undefined })

    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('offers the level range presets and marks the current one', () => {
    seed({ levelRange: '3-12' })

    expect(screen.getByRole('button', { name: 'Long (3–12)' })).toHaveClass('btn-primary')
    expect(screen.getByRole('button', { name: 'Epic (3–15)' })).not.toHaveClass('btn-primary')
  })

  it.each([
    ['Short Arc (1–5)', '1-5'],
    ['Medium (1–10)', '1-10'],
    ['Long (3–12)', '3-12'],
    ['Epic (3–15)', '3-15'],
    ['Full Campaign (1–20)', '1-20'],
  ])('picking %s sets the range to %s', async (label, value) => {
    const user = userEvent.setup()
    seed({ levelRange: '' })

    await user.click(screen.getByRole('button', { name: label }))

    expect(wizard().levelRange).toBe(value)
    expect(levelRangeBox()).toHaveValue(value)
  })

  it('takes a hand-typed range', async () => {
    const user = userEvent.setup()
    seed({ levelRange: '4' })

    await user.type(levelRangeBox(), '-9')

    expect(wizard().levelRange).toBe('4-9')
  })

  it('lets the range box be emptied and retyped without the default getting in the way', async () => {
    const user = userEvent.setup()
    seed()

    await user.clear(levelRangeBox())
    expect(levelRangeBox()).toHaveValue('')

    // Typing into the cleared box yields exactly what was typed. It used to
    // read '1-54-9', because the box refilled itself with '1-5' on every key.
    await user.type(levelRangeBox(), '4-9')
    expect(wizard().levelRange).toBe('4-9')
    expect(levelRangeBox()).toHaveValue('4-9')
  })

  it('treats an empty range as the 1-5 default everywhere but the box itself', () => {
    seed({ levelRange: '' })

    // The box is left empty (showing its placeholder) so it can be retyped;
    // the highlight and the generation hint still read the default, and the
    // store applies the same fallback when it builds the request.
    expect(levelRangeBox()).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Short Arc (1–5)' })).toHaveClass('btn-primary')
    expect(screen.getByText(/one tier, Arc 1 covers it all/)).toBeInTheDocument()
  })

  it.each([
    ['1-5', /one tier, Arc 1 covers it all/],
    ['1-10', /two tiers/],
    ['3-12', /two tiers/],
    ['3-15', /three tiers/],
    ['1-20', /full epic/],
  ])('scales the generation hint for %s', (levelRange, expected) => {
    seed({ levelRange })

    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('reads an en-dash range the same as a hyphenated one', () => {
    seed({ levelRange: '3–15' })

    expect(screen.getByText(/three tiers/)).toBeInTheDocument()
  })

  it('offers no hint for a range it cannot read', () => {
    seed({ levelRange: '7' })

    expect(screen.queryByText(/Extend Arc<\/strong> in Saved Campaigns/)).not.toBeInTheDocument()
    expect(screen.queryByText(/scenes ·/)).not.toBeInTheDocument()
  })

  it('refuses to build without a campaign name, and says why', async () => {
    const user = userEvent.setup()
    seed()

    await user.click(screen.getByRole('button', { name: /build campaign/i }))

    expect(buildCampaign).not.toHaveBeenCalled()
    expect(await screen.findByText(/please enter a campaign name\./i)).toBeInTheDocument()
  })

  it('treats a whitespace-only name as no name', async () => {
    const user = userEvent.setup()
    seed({ name: '   ' })

    await user.click(screen.getByRole('button', { name: /build campaign/i }))

    expect(buildCampaign).not.toHaveBeenCalled()
  })

  it('builds once there is a name, clearing any earlier complaint', async () => {
    const user = userEvent.setup()
    seed({ name: 'Greenrest', buildError: 'Please enter a campaign name.' })

    await user.click(screen.getByRole('button', { name: /build campaign/i }))

    expect(buildCampaign).toHaveBeenCalledTimes(1)
    expect(wizard().buildError).toBeNull()
  })

  it('shows progress and blocks a second build while one is running', () => {
    seed({ name: 'Greenrest', buildInProgress: true })

    expect(screen.getByRole('button', { name: /building/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /importing/i })).toBeDisabled()
  })

  it('clears every field on the form', async () => {
    const user = userEvent.setup()
    seed({
      name: 'Greenrest', description: 'A drowned duchy', theme: 'dark fantasy',
      scale: 'short arc', seedIdeas: 'the miller', levelRange: '3-15',
      foundryWorldName: 'greenrest-world', generatePrologue: false,
      characterConcept: 'a quiet wizard', characterName: 'Mira',
      importSourcePath: '/adventures/krynn', importJournalPack: 'ddb-journals',
      buildError: 'stale', buildResult: { prompt_id: 'p-1' },
    })

    await user.click(screen.getByRole('button', { name: 'Clear' }))

    // Asserted through the form, because resetWizard rebuilds campaignWizard
    // from a shorter literal than the initial state: foundryWorldName,
    // characterConcept, characterName, characterUserId, importSourcePath and
    // importJournalPack come back undefined rather than ''. The page reads
    // each of those as `x || ''`, so the operator sees an empty field either
    // way; the shape drift is noted here rather than fixed.
    expect(screen.getByPlaceholderText(/My New Campaign/)).toHaveValue('')
    expect(screen.getByPlaceholderText(/Describe the world, tone/)).toHaveValue('')
    expect(screen.getByPlaceholderText(/dark fantasy, steampunk/)).toHaveValue('')
    expect(screen.getByPlaceholderText(/one-shot, short arc/)).toHaveValue('')
    expect(screen.getByPlaceholderText(/Key NPCs to include/)).toHaveValue('')
    expect(screen.getByPlaceholderText(/Describe your hero/)).toHaveValue('')
    expect(screen.getByPlaceholderText(/Character name \(optional/)).toHaveValue('')
    expect(screen.getByPlaceholderText(/published-campaign-folder/)).toHaveValue('')
    expect(screen.getByPlaceholderText(/ddb-krynn-ddb-journals/)).toHaveValue('')
    expect(levelRangeBox()).toHaveValue('1-5')
    expect(screen.getByRole('checkbox')).toBeChecked()
    expect(screen.queryByText(/campaign build initiated/i)).not.toBeInTheDocument()
    expect(screen.queryByText('❌ stale')).not.toBeInTheDocument()
  })

  it('cannot import without a source path', () => {
    seed({ name: 'Greenrest' })

    expect(screen.getByRole('button', { name: /import & deploy/i })).toBeDisabled()
  })

  it('treats a whitespace-only source path as none', () => {
    seed({ name: 'Greenrest', importSourcePath: '   ' })

    expect(screen.getByRole('button', { name: /import & deploy/i })).toBeDisabled()
  })

  it('refuses to import without a campaign name, and says why', async () => {
    const user = userEvent.setup()
    seed({ importSourcePath: '/adventures/krynn' })

    await user.click(screen.getByRole('button', { name: /import & deploy/i }))

    expect(importCampaign).not.toHaveBeenCalled()
    expect(await screen.findByText(/before importing\./i)).toBeInTheDocument()
  })

  it('imports once there is both a name and a path', async () => {
    const user = userEvent.setup()
    seed({ name: 'Krynn', importSourcePath: '/adventures/krynn' })

    await user.click(screen.getByRole('button', { name: /import & deploy/i }))

    expect(importCampaign).toHaveBeenCalledTimes(1)
    expect(wizard().buildError).toBeNull()
  })

  it('shows no result panel before a build', () => {
    seed()

    expect(screen.queryByText(/campaign build initiated/i)).not.toBeInTheDocument()
  })

  it('names the prompt the build was queued under', () => {
    seed({ buildResult: { prompt_id: 'p-42' } })

    expect(screen.getByText(/campaign build initiated \(p-42\)/i)).toBeInTheDocument()
  })

  it('says "processing" when the build has no prompt id yet', () => {
    seed({ buildResult: {} })

    expect(screen.getByText(/campaign build initiated \(processing\)/i)).toBeInTheDocument()
  })

  it('counts the generated steps on success', () => {
    seed({ buildResult: { status: 'ok', steps: [1, 2, 3] } })

    expect(screen.getByText(/generated with 3 steps/i)).toBeInTheDocument()
  })

  it('reports zero steps rather than a blank when the server sent none', () => {
    seed({ buildResult: { status: 'ok' } })

    expect(screen.getByText(/generated with 0 steps/i)).toBeInTheDocument()
  })

  it('does not claim generation succeeded when the status is not ok', () => {
    seed({ buildResult: { status: 'failed', steps: [1] } })

    expect(screen.queryByText(/generated with/i)).not.toBeInTheDocument()
  })

  it('confirms a deployed player character by name', () => {
    seed({ buildResult: { player_character: { ok: true, name: 'Mira' } } })

    expect(screen.getByText(/player character deployed: Mira/i)).toBeInTheDocument()
  })

  it('says why a player character was not deployed', () => {
    seed({ buildResult: { player_character: { ok: false, error: 'no free player slot' } } })

    expect(screen.getByText(/not deployed: no free player slot/i)).toBeInTheDocument()
  })

  it('admits it does not know why when the server gave no reason', () => {
    seed({ buildResult: { player_character: { ok: false } } })

    expect(screen.getByText(/not deployed: unknown error/i)).toBeInTheDocument()
  })

  it('reports a clean import without warning colours', () => {
    seed({
      buildResult: {
        import_summary: {
          chapters_processed: 4, pages_extracted: 120,
          maps_matched: ['a', 'b'], tokens_matched: ['c'],
          maps_unmatched: [], tokens_unmatched: [], warnings: [],
        },
      },
    })

    expect(screen.getByText(/✅ Read 4 chapter\(s\) from world journals \(120 pages\)/)).toBeInTheDocument()
    expect(screen.getByText('Maps: 2 matched')).toBeInTheDocument()
    expect(screen.getByText('NPCs: 1 matched')).toBeInTheDocument()
    expect(screen.queryByText(/warning\(s\)/)).not.toBeInTheDocument()
  })

  it('flags an import that matched nothing for some assets', () => {
    seed({
      buildResult: {
        import_summary: {
          chapters_processed: 2, maps_matched: ['a'], maps_unmatched: ['x', 'y'],
          tokens_matched: [], tokens_unmatched: ['z'], warnings: [],
        },
      },
    })

    expect(screen.getByText(/⚠️ Read 2 chapter/)).toBeInTheDocument()
    expect(screen.getByText('Maps: 1 matched, 2 unmatched')).toBeInTheDocument()
    expect(screen.getByText('NPCs: 0 matched, 1 unmatched')).toBeInTheDocument()
  })

  it('flags an import that read no chapters at all', () => {
    seed({ buildResult: { import_summary: { chapters_processed: 0 } } })

    expect(screen.getByText(/⚠️ Read 0 chapter\(s\) from world journals \(0 pages\)/)).toBeInTheDocument()
  })

  it('counts import warnings and says to check the logs', () => {
    seed({ buildResult: { import_summary: { chapters_processed: 3, warnings: ['w1', 'w2'] } } })

    expect(screen.getByText(/2 warning\(s\) — check logs before the session/)).toBeInTheDocument()
    expect(screen.getByText(/⚠️ Read 3 chapter/)).toBeInTheDocument()
  })

  it('shows no import summary when the build was not an import', () => {
    seed({ buildResult: { status: 'ok', steps: [] } })

    expect(screen.queryByText(/from world journals/)).not.toBeInTheDocument()
  })
})
