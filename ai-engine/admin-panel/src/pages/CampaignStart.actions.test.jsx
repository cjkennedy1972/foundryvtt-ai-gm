/**
 * The world-content lifecycle actions on an expanded campaign card: extend
 * arc, enrich lore, analyze & enhance, restart, remove from world. Split from
 * CampaignStart.test.jsx, which covers the list and the session banner.
 */
import { act, fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore } from '../test/store-harness.jsx'
import CampaignStart from './CampaignStart.jsx'

let actions
let confirmSpy

const CAMPAIGN = { name: 'Greenrest', description: 'A drowned duchy' }

/** Renders the page with one campaign and opens its details. */
async function expanded(details = {}, user = userEvent.setup()) {
  actions.getCampaign = vi.fn(async () => details)
  renderWithStore(<CampaignStart />, {
    ...actions,
    campaignSession: {
      campaigns: [CAMPAIGN], selectedCampaign: null, activeSession: null,
      loading: false, error: null,
    },
  })
  await act(async () => { await user.click(screen.getByRole('button', { name: /details/i })) })
  return user
}

const ok = (data) => vi.fn(async () => ({ ok: true, data }))
const fails = (error) => vi.fn(async () => ({ ok: false, error }))

/** Clicks and flushes, since each handler resolves after the click's act(). */
async function press(user, name) {
  await act(async () => { await user.click(screen.getByRole('button', { name })) })
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
    regenerateAssets: vi.fn(async () => ({ status: 'completed' })),
    extendCampaignArc: ok({}),
    enrichCampaign: ok({}),
    teardownCampaign: ok({}),
    restartCampaign: ok({}),
    optimizeCampaign: ok({}),
  }
  // The destructive actions guard themselves with window.confirm.
  confirmSpy = vi.fn(() => true)
  vi.stubGlobal('confirm', confirmSpy)
})

afterEach(() => {
  vi.unstubAllGlobals()
  resetStore()
})

describe('CampaignStart — campaign details', () => {
  it('loads the details once and shows them', async () => {
    await expanded({ npc_count: 14, location_count: 9, quest_count: 5, journal_entries: 31, status: 'synced' })

    expect(actions.getCampaign).toHaveBeenCalledWith('Greenrest')
    expect(screen.getByText('14')).toBeInTheDocument()
    expect(screen.getByText('9')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText('31')).toBeInTheDocument()
    expect(screen.getByText('synced')).toBeInTheDocument()
  })

  it('shows a zero count rather than hiding the stat', async () => {
    await expanded({ npc_count: 0 })

    expect(screen.getByText('NPCs')).toBeInTheDocument()
    expect(screen.getByText('0')).toBeInTheDocument()
  })

  it('omits a stat the vault did not report', async () => {
    await expanded({ npc_count: 2 })

    expect(screen.queryByText('Locations')).not.toBeInTheDocument()
    expect(screen.queryByText('Vault Status')).not.toBeInTheDocument()
  })

  it('collapses and re-expands without refetching', async () => {
    const user = await expanded({ npc_count: 14 })

    await press(user, /less/i)
    expect(screen.queryByText('NPCs')).not.toBeInTheDocument()

    await press(user, /details/i)
    expect(screen.getByText('NPCs')).toBeInTheDocument()
    expect(actions.getCampaign).toHaveBeenCalledTimes(1)
  })

  it('shows progress and blocks a second fetch while the details load', async () => {
    let release
    const user = userEvent.setup()
    actions.getCampaign = vi.fn(() => new Promise((r) => { release = r }))
    renderWithStore(<CampaignStart />, {
      ...actions,
      campaignSession: { campaigns: [CAMPAIGN], selectedCampaign: null, activeSession: null, loading: false, error: null },
    })

    await user.click(screen.getByRole('button', { name: /details/i }))
    expect(screen.getByRole('button', { name: '…' })).toBeDisabled()

    await act(async () => release({ npc_count: 1 }))
    expect(screen.getByRole('button', { name: /less/i })).toBeEnabled()
  })

  describe('extend arc', () => {
    it('starts from the arc the campaign is already on', async () => {
      await expanded({ data: { story_arcs: [{ arc_number: 0 }, { arc_number: 1 }, { arc_number: 2 }] } })

      expect(screen.getByText(/picking up the story where Arc 2 left off/)).toBeInTheDocument()
    })

    it('assumes Arc 1 when the campaign has no numbered arcs', async () => {
      await expanded({ data: { story_arcs: [{ arc_number: 0 }] } })

      expect(screen.getByText(/where Arc 1 left off/)).toBeInTheDocument()
    })

    it('reads the arcs off an un-nested details payload too', async () => {
      await expanded({ story_arcs: [{ arc_number: 1 }, { arc_number: 2 }] })

      expect(screen.getByText(/where Arc 2 left off/)).toBeInTheDocument()
    })

    it('extends at the level in the box', async () => {
      const user = await expanded()

      fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '9' } })
      await press(user, /extend campaign/i)

      expect(actions.extendCampaignArc).toHaveBeenCalledWith('Greenrest', 9)
    })

    it.each([
      ['25', 20, 'above 20'],
      ['0', 1, 'below 1'],
      ['', 1, 'blank'],
    ])('clamps a party level of %s to %i (%s)', async (typed, expected) => {
      const user = await expanded()

      // Changed directly so the whole value arrives at once.
      fireEvent.change(screen.getByRole('spinbutton'), { target: { value: typed } })
      await press(user, /extend campaign/i)

      expect(actions.extendCampaignArc).toHaveBeenCalledWith('Greenrest', expected)
    })

    it('lets the level box be emptied and retyped without the default getting in the way', async () => {
      const user = await expanded()
      const box = screen.getByRole('spinbutton')

      await user.clear(box)
      expect(box).toHaveValue(null)

      // Typing into the cleared box yields exactly what was typed. It used to
      // read 19, because the box refilled itself with 1 on every keystroke, and
      // that 19 went straight into a 2-5 minute arc generation.
      await user.type(box, '9')
      await press(user, /extend campaign/i)

      expect(actions.extendCampaignArc).toHaveBeenCalledWith('Greenrest', 9)
    })

    it('shows the level that will actually be used when the operator leaves the box', async () => {
      const user = await expanded()
      const box = screen.getByRole('spinbutton')

      await user.clear(box)
      await user.type(box, '45')
      expect(box).toHaveValue(45)

      await user.tab()

      expect(box).toHaveValue(20)
    })

    it('reports the arc it generated and what is in it', async () => {
      actions.extendCampaignArc = ok({
        arc_number: 3, arc_title: 'The Drowned Court',
        arc_data: { scenes: [1, 2, 3], encounters: [1, 2], npcs: [1] },
      })
      const user = await expanded()

      await press(user, /extend campaign/i)

      expect(screen.getByText(/Arc 3 — "The Drowned Court" deployed/)).toBeInTheDocument()
      expect(screen.getByText(/3 new scenes · 2 new encounters · 1 new NPCs/)).toBeInTheDocument()
    })

    it('counts an arc that came back with no content as zeroes', async () => {
      actions.extendCampaignArc = ok({ arc_number: 2, arc_title: 'Thin' })
      const user = await expanded()

      await press(user, /extend campaign/i)

      expect(screen.getByText(/0 new scenes · 0 new encounters · 0 new NPCs/)).toBeInTheDocument()
    })

    it('shows progress and warns it is slow while generating', async () => {
      let release
      actions.extendCampaignArc = vi.fn(() => new Promise((r) => { release = r }))
      const user = await expanded()

      await user.click(screen.getByRole('button', { name: /extend campaign/i }))

      expect(screen.getByRole('button', { name: /generating arc/i })).toBeDisabled()
      expect(screen.getByText(/takes 2–5 minutes/)).toBeInTheDocument()

      await act(async () => release({ ok: true, data: { arc_number: 1, arc_title: 'X' } }))
      expect(screen.getByRole('button', { name: /extend campaign/i })).toBeEnabled()
    })

    it('says why the extension failed', async () => {
      actions.extendCampaignArc = fails('the LLM refused')
      const user = await expanded()

      await press(user, /extend campaign/i)

      expect(screen.getByText(/the LLM refused/)).toBeInTheDocument()
    })

    it('falls back to its own reason when the store gives none', async () => {
      actions.extendCampaignArc = vi.fn(async () => ({ ok: false }))
      const user = await expanded()

      await press(user, /extend campaign/i)

      expect(screen.getByText(/Extension failed/)).toBeInTheDocument()
    })
  })

  describe('enrich lore', () => {
    const SOURCE = /sourcebook\.pdf or/

    it('will not enrich without a source', async () => {
      await expanded()

      expect(screen.getByRole('button', { name: /enrich lore/i })).toBeDisabled()
    })

    it('will not enrich on whitespace alone', async () => {
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '   ')

      expect(screen.getByRole('button', { name: /enrich lore/i })).toBeDisabled()
    })

    it('enriches from the trimmed path', async () => {
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '  /books/duchy.pdf  ')
      await press(user, /enrich lore/i)

      expect(actions.enrichCampaign).toHaveBeenCalledWith('Greenrest', { sourcePath: '/books/duchy.pdf' })
    })

    it('reports what each source contributed', async () => {
      actions.enrichCampaign = ok({
        sources: [{
          id: 's1', title: 'Duchy Gazetteer',
          entities: { npcs: { added: 3, enriched: 2 }, locations: { added: 1, enriched: 0 } },
          world_added: true, history_added: true, conflicts: 2,
        }],
      })
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '/books/duchy.pdf')
      await press(user, /enrich lore/i)

      expect(screen.getByText(/Duchy Gazetteer: 4 added · 2 enriched · world lore · history · 2 conflicts for canon review/)).toBeInTheDocument()
    })

    it('says "conflict" in the singular for one', async () => {
      actions.enrichCampaign = ok({ sources: [{ id: 's1', title: 'Pamphlet', conflicts: 1 }] })
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '/books/p.pdf')
      await press(user, /enrich lore/i)

      expect(screen.getByText(/1 conflict for canon review/)).toBeInTheDocument()
    })

    it('leaves out the clauses that do not apply', async () => {
      actions.enrichCampaign = ok({ sources: [{ id: 's1', title: 'Pamphlet', entities: {}, conflicts: 0 }] })
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '/books/p.pdf')
      await press(user, /enrich lore/i)

      expect(screen.getByText('Pamphlet: 0 added · 0 enriched')).toBeInTheDocument()
    })

    it('names the sources it had already folded in', async () => {
      actions.enrichCampaign = ok({ sources: [], skipped: ['duchy.pdf', 'court.md'] })
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '/books')
      await press(user, /enrich lore/i)

      expect(screen.getByText(/Already added, skipped: duchy\.pdf, court\.md/)).toBeInTheDocument()
    })

    it('says when a restart is not needed', async () => {
      actions.enrichCampaign = ok({ sources: [], reloaded: true })
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '/books')
      await press(user, /enrich lore/i)

      expect(screen.getByText(/no restart needed/)).toBeInTheDocument()
    })

    it('reports a partial run that stopped early', async () => {
      actions.enrichCampaign = ok({ sources: [], error: 'ran out of context' })
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '/books')
      await press(user, /enrich lore/i)

      expect(screen.getByText(/Stopped early: ran out of context/)).toBeInTheDocument()
    })

    it('shows progress while reading the source', async () => {
      let release
      actions.enrichCampaign = vi.fn(() => new Promise((r) => { release = r }))
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '/books/duchy.pdf')
      await user.click(screen.getByRole('button', { name: /enrich lore/i }))

      expect(screen.getByRole('button', { name: /enriching/i })).toBeDisabled()
      expect(screen.getByText(/a large PDF can take several minutes/)).toBeInTheDocument()

      await act(async () => release({ ok: true, data: { sources: [] } }))
      expect(screen.getByRole('button', { name: /enrich lore/i })).toBeEnabled()
    })

    it('says why the enrichment failed', async () => {
      actions.enrichCampaign = fails('no such path')
      const user = await expanded()

      await user.type(screen.getByPlaceholderText(SOURCE), '/nope')
      await press(user, /enrich lore/i)

      expect(screen.getByText(/no such path/)).toBeInTheDocument()
    })
  })

  describe('analyze & enhance', () => {
    it('reports what it found and what it changed', async () => {
      actions.optimizeCampaign = ok({
        analysis: { scene_count: 8, encounter_count: 6, npc_count: 14 },
        modules: { enabled: 4 },
        applied: { scenes_enriched: 8 },
      })
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.getByText(/Enhancement Complete/)).toBeInTheDocument()
      expect(screen.getByText('8 scenes')).toBeInTheDocument()
      expect(screen.getByText('6 encounters')).toBeInTheDocument()
      expect(screen.getByText('14 NPCs')).toBeInTheDocument()
      expect(screen.getByText('4 active modules')).toBeInTheDocument()
      expect(screen.getByText('8 scenes enhanced')).toBeInTheDocument()
    })

    it('reads a bare result as all zeroes rather than blanks', async () => {
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.getByText('0 scenes')).toBeInTheDocument()
      expect(screen.getByText('0 active modules')).toBeInTheDocument()
    })

    it('surfaces the first three per-scene errors', async () => {
      actions.optimizeCampaign = ok({ applied: { errors: ['e1', 'e2', 'e3', 'e4'] } })
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.getByText(/e1 · e2 · e3$/)).toBeInTheDocument()
      expect(screen.queryByText(/e4/)).not.toBeInTheDocument()
    })

    it('lists the first eight discovered modules and counts the rest', async () => {
      const modules = Array.from({ length: 10 }, (_, i) => ({ name: `module-${i}`, enabled: i % 2 === 0 }))
      actions.optimizeCampaign = ok({ modules: { modules_list: modules } })
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.getByText('module-7')).toBeInTheDocument()
      expect(screen.queryByText('module-8')).not.toBeInTheDocument()
      expect(screen.getByText('+2 more')).toBeInTheDocument()
    })

    it('truncates a very long module name', async () => {
      actions.optimizeCampaign = ok({ modules: { modules_list: [{ name: 'a'.repeat(40), enabled: true }] } })
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.getByText('a'.repeat(20))).toBeInTheDocument()
    })

    it('lists no modules when the analysis found none', async () => {
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.queryByText(/Discovered Modules/)).not.toBeInTheDocument()
    })

    it('opens the recommendations on a successful run and lets them be hidden', async () => {
      actions.optimizeCampaign = ok({
        enhancements: {},
        recommendations: [{ priority: 'high', category: 'Lighting', action: 'Light the mill' }],
      })
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.getByText('[HIGH] Lighting')).toBeInTheDocument()
      expect(screen.getByText('Light the mill')).toBeInTheDocument()

      await press(user, /show details|hide details/i)
      expect(screen.queryByText('[HIGH] Lighting')).not.toBeInTheDocument()
    })

    it('shows the top three recommendations only', async () => {
      actions.optimizeCampaign = ok({
        enhancements: {},
        recommendations: Array.from({ length: 5 }, (_, i) => ({ priority: 'low', category: `c${i}`, action: `a${i}` })),
      })
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.getByText('[LOW] c2')).toBeInTheDocument()
      expect(screen.queryByText('[LOW] c3')).not.toBeInTheDocument()
    })

    it('shows no details pane when the run produced no enhancements', async () => {
      actions.optimizeCampaign = ok({ recommendations: [{ priority: 'high', category: 'Lighting', action: 'x' }] })
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.queryByText('[HIGH] Lighting')).not.toBeInTheDocument()
    })

    it('keeps the recommendations closed after a failed run', async () => {
      actions.optimizeCampaign = fails('relay down')
      const user = await expanded()

      await press(user, /analyze & enhance/i)

      expect(screen.getByText(/relay down/)).toBeInTheDocument()
      expect(screen.queryByText(/Show Details/)).not.toBeInTheDocument()
    })

    it('shows progress while enhancing', async () => {
      let release
      actions.optimizeCampaign = vi.fn(() => new Promise((r) => { release = r }))
      const user = await expanded()

      await user.click(screen.getByRole('button', { name: /analyze & enhance/i }))

      expect(screen.getByRole('button', { name: /enhancing scenes/i })).toBeDisabled()
      expect(screen.getByText(/Placing walls, lights, and sounds/)).toBeInTheDocument()

      await act(async () => release({ ok: true, data: {} }))
      expect(screen.getByRole('button', { name: /analyze & enhance/i })).toBeEnabled()
    })
  })

  describe('restart', () => {
    it('warns what will be lost and does nothing if the operator declines', async () => {
      confirmSpy.mockReturnValue(false)
      const user = await expanded()

      await press(user, /restart from beginning/i)

      expect(confirmSpy).toHaveBeenCalledWith(expect.stringMatching(/erases ALL session history/))
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringMatching(/Greenrest/))
      expect(actions.restartCampaign).not.toHaveBeenCalled()
    })

    it('restarts once confirmed and reports the damage', async () => {
      actions.restartCampaign = ok({
        sessions_deleted: 4, scenes_deployed: 8, npcs_deployed: 14,
        enrichment: { enriched: 8 },
      })
      const user = await expanded()

      await press(user, /restart from beginning/i)

      expect(actions.restartCampaign).toHaveBeenCalledWith('Greenrest')
      expect(screen.getByText(/4 sessions of history erased · 8 scenes redeployed · 14 NPCs redeployed · 8 scenes enriched/)).toBeInTheDocument()
      expect(screen.getByText(/Use Start GM to begin from the opening scene/)).toBeInTheDocument()
    })

    it('says "session" in the singular for one', async () => {
      actions.restartCampaign = ok({ sessions_deleted: 1 })
      const user = await expanded()

      await press(user, /restart from beginning/i)

      expect(screen.getByText(/1 session of history erased/)).toBeInTheDocument()
    })

    it('reads a bare restart result as all zeroes', async () => {
      const user = await expanded()

      await press(user, /restart from beginning/i)

      expect(screen.getByText(/0 sessions of history erased · 0 scenes redeployed · 0 NPCs redeployed · 0 scenes enriched/)).toBeInTheDocument()
    })

    it('says why the restart failed', async () => {
      actions.restartCampaign = fails('vault locked')
      const user = await expanded()

      await press(user, /restart from beginning/i)

      expect(screen.getByText(/vault locked/)).toBeInTheDocument()
    })

    it('shows progress while restarting', async () => {
      let release
      actions.restartCampaign = vi.fn(() => new Promise((r) => { release = r }))
      const user = await expanded()

      await user.click(screen.getByRole('button', { name: /restart from beginning/i }))

      expect(screen.getByRole('button', { name: /restarting/i })).toBeDisabled()

      await act(async () => release({ ok: true, data: {} }))
      expect(screen.getByRole('button', { name: /restart from beginning/i })).toBeEnabled()
    })
  })

  describe('remove from world', () => {
    it('warns what will be deleted and does nothing if the operator declines', async () => {
      confirmSpy.mockReturnValue(false)
      const user = await expanded()

      await press(user, /remove campaign/i)

      expect(confirmSpy).toHaveBeenCalledWith(expect.stringMatching(/scenes, actors, journals, loot tables, and playlists/))
      expect(actions.teardownCampaign).not.toHaveBeenCalled()
    })

    // The endpoint labels the two passes differently: the flag pass by
    // collection (scenes, actors, journal, tables, playlists) and the UUID
    // fallback by Foundry document type (Scene, Actor, JournalEntry, RollTable,
    // Playlist). These payloads use that real vocabulary.
    it('adds what the UUID fallback deleted to what the flag pass deleted, kind by kind', async () => {
      actions.teardownCampaign = ok({
        deleted: {
          flag_pass: { actors: 14, journal: 3, tables: 2, playlists: 1, scenes: 8 },
          uuid_pass: { Scene: 2, Actor: 1 },
        },
      })
      const user = await expanded()

      await press(user, /remove campaign/i)

      // 28 from the flag pass plus 3 from the fallback. The breakdown used to
      // read only the flag pass, so it said 8 scenes and 14 actors and its
      // parts summed to 28 beside a total of 31.
      expect(screen.getByText('31 documents deleted · 10 scenes · 15 actors · 3 journals · 2 tables · 1 playlist')).toBeInTheDocument()
    })

    it('reports kinds only the UUID fallback found', async () => {
      actions.teardownCampaign = ok({
        deleted: {
          flag_pass: {},
          uuid_pass: { JournalEntry: 4, RollTable: 1, Playlist: 2 },
        },
      })
      const user = await expanded()

      await press(user, /remove campaign/i)

      expect(screen.getByText('7 documents deleted · 4 journals · 1 table · 2 playlists')).toBeInTheDocument()
    })

    it('sums the totals rather than letting one pass replace the other', async () => {
      // Not what the endpoint sends today — the fallback says `Scene` where the
      // flag pass says `scenes` — but the total used to depend on the labels
      // never colliding: spreading the passes into one object would have let
      // the second overwrite the first. Only the total is asserted; a label
      // outside the known vocabulary has no place in the breakdown.
      actions.teardownCampaign = ok({
        deleted: { flag_pass: { scenes: 8 }, uuid_pass: { scenes: 2 } },
      })
      const user = await expanded()

      await press(user, /remove campaign/i)

      expect(screen.getByText(/^10 documents deleted/)).toBeInTheDocument()
    })

    it('still counts a kind it has no label for in the total', async () => {
      actions.teardownCampaign = ok({
        deleted: { flag_pass: { scenes: 2, macros: 3 } },
      })
      const user = await expanded()

      await press(user, /remove campaign/i)

      expect(screen.getByText('5 documents deleted · 2 scenes')).toBeInTheDocument()
    })

    it('uses the singular for a single document and a single item of a kind', async () => {
      actions.teardownCampaign = ok({ deleted: { flag_pass: { scenes: 1 } } })
      const user = await expanded()

      await press(user, /remove campaign/i)

      expect(screen.getByText(/1 document deleted · 1 scene$/)).toBeInTheDocument()
    })

    it('ignores non-numeric entries in the delete counts', async () => {
      actions.teardownCampaign = ok({ deleted: { flag_pass: { scenes: 2, note: 'partial' } } })
      const user = await expanded()

      await press(user, /remove campaign/i)

      expect(screen.getByText(/2 documents deleted · 2 scenes$/)).toBeInTheDocument()
    })

    it('reports nothing deleted rather than a blank', async () => {
      const user = await expanded()

      await press(user, /remove campaign/i)

      expect(screen.getByText(/^0 documents deleted$/)).toBeInTheDocument()
    })

    it('surfaces per-document errors alongside the total', async () => {
      actions.teardownCampaign = ok({ deleted: { flag_pass: { scenes: 1 } }, errors: ['scene 3 locked', 'actor 7 in use'] })
      const user = await expanded()

      await press(user, /remove campaign/i)

      expect(screen.getByText(/scene 3 locked · actor 7 in use/)).toBeInTheDocument()
    })

    it('says why the teardown failed', async () => {
      actions.teardownCampaign = fails('relay refused execute-js')
      const user = await expanded()

      await press(user, /remove campaign/i)

      expect(screen.getByText(/relay refused execute-js/)).toBeInTheDocument()
    })

    it('shows progress while removing', async () => {
      let release
      actions.teardownCampaign = vi.fn(() => new Promise((r) => { release = r }))
      const user = await expanded()

      await user.click(screen.getByRole('button', { name: /remove campaign/i }))

      expect(screen.getByRole('button', { name: /removing/i })).toBeDisabled()

      await act(async () => release({ ok: true, data: {} }))
      expect(screen.getByRole('button', { name: /remove campaign/i })).toBeEnabled()
    })
  })
})
