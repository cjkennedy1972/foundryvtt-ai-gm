import { beforeEach, describe, expect, it, vi } from 'vitest'

import { safeFetch } from './fetch.js'
import { useStore } from './store.js'

vi.mock('./fetch.js', () => ({
  safeFetch: vi.fn(),
  apiFetch: vi.fn(),
}))

const pristine = { ...useStore.getState() }
const store = () => useStore.getState()

/** safeFetch's success shape. */
const ok = (data = {}) => ({ ok: true, data })
/** safeFetch's caught-error shape. */
const fail = (error = 'boom') => ({ ok: false, error })

/** The request body safeFetch was called with on its nth call (0-indexed). */
const bodyOf = (n = 0) => safeFetch.mock.calls[n][1].body
const pathOf = (n = 0) => safeFetch.mock.calls[n][0]

beforeEach(() => {
  useStore.setState(pristine, true)
  safeFetch.mockReset()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

describe('plain setters', () => {
  it('sets the active page', () => {
    store().setActivePage('settings')
    expect(store().activePage).toBe('settings')
  })

  it('sets the status message', () => {
    store().setStatusMessage('hello')
    expect(store().statusMessage).toBe('hello')
  })

  it('sets the LLM mode', () => {
    store().setLlmMode('anthropic')
    expect(store().llmMode).toBe('anthropic')
  })

  it('merges a single setting without dropping the others', () => {
    store().setSetting('model', 'claude-opus-5')

    expect(store().settings.model).toBe('claude-opus-5')
    expect(store().settings.temperature).toBe(0.7)
  })

  it('replaces the whole settings object', () => {
    store().setSettings({ model: 'only-this' })
    expect(store().settings).toEqual({ model: 'only-this' })
  })

  it('merges chat-test fields', () => {
    store().setChatTest({ message: 'hi' })
    store().setChatTest({ speaker: 'Ranger' })

    expect(store().chatTest).toEqual({
      message: 'hi', speaker: 'Ranger', result: null, loading: false,
    })
  })

  it('sets the SRD query and results', () => {
    store().setSrdQuery('fireball')
    store().setSrdResults('3rd-level evocation')

    expect(store().srdQuery).toBe('fireball')
    expect(store().srdResults).toBe('3rd-level evocation')
  })

  it('sets a roll-form field', () => {
    store().setRollForm('formula', '2d6+3')

    expect(store().rollForm).toEqual({ formula: '2d6+3', speaker: 'GM', flavor: '' })
  })

  it('sets and resets the legacy new-campaign form', () => {
    store().setNewCampaign('name', 'Greenrest')
    expect(store().newCampaign.name).toBe('Greenrest')

    store().resetNewCampaign()
    expect(store().newCampaign).toEqual({ name: '', vaultFiles: '', description: '' })
  })

  it('sets and resets the campaign session slice', () => {
    store().setCampaignSession('selectedCampaign', { name: 'Greenrest' })
    expect(store().campaignSession.selectedCampaign).toEqual({ name: 'Greenrest' })

    store().resetCampaignSession()
    expect(store().campaignSession).toEqual({
      campaigns: [], selectedCampaign: null, activeSession: null, loading: false, error: null,
    })
  })
})

describe('play mode', () => {
  it('tracks the spoiler guard per campaign', () => {
    store().setPlayMode('Greenrest', true)
    store().setPlayMode('Blackmoor', false)

    expect(store().playModeSessions).toEqual({ Greenrest: true, Blackmoor: false })
  })

  it('flips one campaign without disturbing another', () => {
    store().setPlayMode('Greenrest', true)
    store().setPlayMode('Greenrest', false)

    expect(store().playModeSessions.Greenrest).toBe(false)
  })
})

describe('fetchSettings', () => {
  it('shows the mask for a key the server says is set', async () => {
    // This is the shape GET /api/settings actually returns: never the key,
    // only whether one is configured.
    safeFetch.mockResolvedValue(ok({
      llm_api_key: '', relay_api_key: '',
      llm_api_key_set: true, relay_api_key_set: true,
    }))

    await store().fetchSettings()

    expect(store().settings.llm_api_key).toBe('••••••••')
    expect(store().settings.relay_api_key).toBe('••••••••')
  })

  it('never lets a real key reach the store, even if a server were to send one', async () => {
    // Defence in depth: the server withholds keys today, but the store should
    // not be the thing that would leak one if that ever changed.
    safeFetch.mockResolvedValue(ok({
      llm_api_key: 'sk-super-secret', relay_api_key: 'relay-secret',
      llm_api_key_set: true, relay_api_key_set: true,
    }))

    await store().fetchSettings()

    expect(JSON.stringify(store().settings)).not.toContain('sk-super-secret')
    expect(JSON.stringify(store().settings)).not.toContain('relay-secret')
  })

  it('leaves an unset secret as an empty string, not a mask', async () => {
    safeFetch.mockResolvedValue(ok({
      llm_api_key: '', relay_api_key: '',
      llm_api_key_set: false, relay_api_key_set: false,
    }))

    await store().fetchSettings()

    // An empty field must look empty, so the operator can tell "not
    // configured" from "configured and hidden".
    expect(store().settings.llm_api_key).toBe('')
    expect(store().settings.relay_api_key).toBe('')
  })

  it('masks each key on its own flag', async () => {
    safeFetch.mockResolvedValue(ok({ llm_api_key_set: true, relay_api_key_set: false }))

    await store().fetchSettings()

    expect(store().settings.llm_api_key).toBe('••••••••')
    expect(store().settings.relay_api_key).toBe('')
  })

  it('treats a server that predates the flags as having no keys set', async () => {
    safeFetch.mockResolvedValue(ok({ model: 'gpt-4' }))

    await store().fetchSettings()

    expect(store().settings.llm_api_key).toBe('')
    expect(store().settings.relay_api_key).toBe('')
  })

  it('maps the server field names onto the form', async () => {
    safeFetch.mockResolvedValue(ok({
      model: 'gpt-4',
      llm_base_url: 'http://localhost:1234',
      temperature: 0.2,
      ai_name: 'Sage',
      ai_tone: 'wry',
      relay_url: 'http://localhost:13010',
      comfyui_url: 'http://localhost:18188',
      llm_token_budget: 5000,
    }))

    await store().fetchSettings()

    expect(store().settings).toEqual({
      model: 'gpt-4',
      llm_base_url: 'http://localhost:1234',
      llm_api_key: '',
      temperature: 0.2,
      ai_name: 'Sage',
      ai_tone: 'wry',
      relay_url: 'http://localhost:13010',
      relay_api_key: '',
      comfyui_url: 'http://localhost:18188',
      llm_token_budget: 5000,
    })
  })

  it('keeps a zero temperature instead of defaulting it', async () => {
    safeFetch.mockResolvedValue(ok({ temperature: 0, llm_token_budget: 0 }))

    await store().fetchSettings()

    // ?? not || — a deliberate 0 is a valid setting, and || would silently
    // rewrite it to 0.7.
    expect(store().settings.temperature).toBe(0)
    expect(store().settings.llm_token_budget).toBe(0)
  })

  it('defaults a missing temperature to 0.7', async () => {
    safeFetch.mockResolvedValue(ok({}))

    await store().fetchSettings()

    expect(store().settings.temperature).toBe(0.7)
  })

  it.each([
    ['', 'local'],
    ['   ', 'local'],
    ['https://api.anthropic.com', 'anthropic'],
    ['https://generativelanguage.googleapis.com', 'google'],
    ['https://gemini.example.com', 'google'],
    ['https://api.openai.com/v1', 'openai'],
    ['https://openrouter.ai/api/v1', 'openrouter'],
    ['http://localhost:11434/v1', 'local'],
  ])('hydrates llmMode from base URL %s', async (llm_base_url, expected) => {
    safeFetch.mockResolvedValue(ok({ llm_base_url }))

    await store().fetchSettings()

    expect(store().llmMode).toBe(expected)
  })

  it('reports a failed load without clobbering the form', async () => {
    safeFetch.mockResolvedValue(fail('Engine offline'))

    await store().fetchSettings()

    expect(store().statusMessage).toBe('Engine offline')
    expect(store().settings.model).toBe('')
  })

  it('falls back to a generic message when the failure carries none', async () => {
    safeFetch.mockResolvedValue({ ok: false })

    await store().fetchSettings()

    expect(store().statusMessage).toBe('Failed to load settings')
  })

  it('survives a thrown error', async () => {
    safeFetch.mockRejectedValue(new Error('network down'))

    await store().fetchSettings()

    expect(store().statusMessage).toBe('Failed to load settings: network down')
  })
})

describe('simple fetchers', () => {
  it('fetchStatus stores the engine status and AI flag', async () => {
    safeFetch.mockResolvedValue(ok({ ai_running: true, version: '1.0' }))

    await store().fetchStatus()

    expect(store().engineStatus).toEqual({ ai_running: true, version: '1.0' })
    expect(store().aiRunning).toBe(true)
  })

  it('fetchStatus defaults aiRunning to false when absent', async () => {
    safeFetch.mockResolvedValue(ok({ version: '1.0' }))

    await store().fetchStatus()

    expect(store().aiRunning).toBe(false)
  })

  it('fetchStatus surfaces a failure as a status message', async () => {
    safeFetch.mockResolvedValue(fail('unreachable'))

    await store().fetchStatus()

    expect(store().statusMessage).toBe('unreachable')
    expect(store().engineStatus).toBeNull()
  })

  it('fetchStatus falls back to a generic failure message', async () => {
    safeFetch.mockResolvedValue({ ok: false })

    await store().fetchStatus()

    expect(store().statusMessage).toBe('Status check failed')
  })

  it('fetchStatus survives a thrown error', async () => {
    safeFetch.mockRejectedValue(new Error('kaboom'))

    await store().fetchStatus()

    expect(store().statusMessage).toBe('Failed to fetch status: kaboom')
  })

  it('fetchState stores game state', async () => {
    safeFetch.mockResolvedValue(ok({ mode: 'combat' }))

    await store().fetchState()

    expect(store().gameState).toEqual({ mode: 'combat' })
  })

  it('fetchState leaves state alone on failure', async () => {
    safeFetch.mockResolvedValue(fail())

    await store().fetchState()

    expect(store().gameState).toBeNull()
  })

  it('fetchState survives a thrown error', async () => {
    safeFetch.mockRejectedValue(new Error('x'))

    await store().fetchState()

    expect(store().gameState).toBeNull()
  })

  it('fetchEvents passes the limit through', async () => {
    safeFetch.mockResolvedValue(ok([{ description: 'rolled' }]))

    await store().fetchEvents(10)

    expect(pathOf()).toBe('/session/events?limit=10')
    expect(store().events).toEqual([{ description: 'rolled' }])
  })

  it('fetchEvents defaults the limit to 50', async () => {
    safeFetch.mockResolvedValue(ok([]))

    await store().fetchEvents()

    expect(pathOf()).toBe('/session/events?limit=50')
  })

  it('fetchEvents leaves events alone on failure', async () => {
    safeFetch.mockResolvedValue(fail())

    await store().fetchEvents()

    expect(store().events).toEqual([])
  })

  it('fetchEvents survives a thrown error', async () => {
    safeFetch.mockRejectedValue(new Error('x'))
    await store().fetchEvents()
    expect(store().events).toEqual([])
  })

  it('fetchInteractiveSessions tolerates a payload with no sessions key', async () => {
    safeFetch.mockResolvedValue(ok({}))

    await store().fetchInteractiveSessions()

    expect(store().interactiveSessions).toEqual([])
  })

  it('fetchInteractiveSessions stores the sessions', async () => {
    safeFetch.mockResolvedValue(ok({ sessions: [{ id: 'a' }] }))

    await store().fetchInteractiveSessions()

    expect(store().interactiveSessions).toEqual([{ id: 'a' }])
  })

  it('fetchInteractiveSessions ignores a failure', async () => {
    safeFetch.mockResolvedValue(fail())
    await store().fetchInteractiveSessions()
    expect(store().interactiveSessions).toEqual([])
  })

  it('fetchInteractiveSessions survives a thrown error', async () => {
    safeFetch.mockRejectedValue(new Error('x'))
    await store().fetchInteractiveSessions()
    expect(store().interactiveSessions).toEqual([])
  })

  it('fetchNpcs stores the roster', async () => {
    safeFetch.mockResolvedValue(ok({ npcs: [{ npc_id: 'Mira' }] }))

    await store().fetchNpcs()

    expect(store().npcs).toEqual([{ npc_id: 'Mira' }])
  })

  it('fetchNpcs tolerates a missing npcs key', async () => {
    safeFetch.mockResolvedValue(ok({}))
    await store().fetchNpcs()
    expect(store().npcs).toEqual([])
  })

  it('fetchNpcs ignores a failure', async () => {
    safeFetch.mockResolvedValue(fail())
    await store().fetchNpcs()
    expect(store().npcs).toEqual([])
  })

  it('fetchNpcs survives a thrown error', async () => {
    safeFetch.mockRejectedValue(new Error('x'))
    await store().fetchNpcs()
    expect(store().npcs).toEqual([])
  })
})

describe('canon proposals', () => {
  it('fetches the pending list', async () => {
    safeFetch.mockResolvedValue(ok({ proposals: [{ id: 1 }] }))

    await store().fetchCanonProposals()

    expect(pathOf()).toBe('/canon/pending')
    expect(store().canonProposals).toEqual([{ id: 1 }])
  })

  it('tolerates a payload with no proposals key', async () => {
    safeFetch.mockResolvedValue(ok({}))
    await store().fetchCanonProposals()
    expect(store().canonProposals).toEqual([])
  })

  it('ignores a failed fetch', async () => {
    safeFetch.mockResolvedValue(fail())
    await store().fetchCanonProposals()
    expect(store().canonProposals).toEqual([])
  })

  it('survives a thrown error', async () => {
    safeFetch.mockRejectedValue(new Error('x'))
    await store().fetchCanonProposals()
    expect(store().canonProposals).toEqual([])
  })

  it('approve posts the edited text and refreshes the list', async () => {
    safeFetch
      .mockResolvedValueOnce(ok({ status: 'approved' }))
      .mockResolvedValueOnce(ok({ proposals: [] }))

    const res = await store().approveCanonProposal(7, 'the mill burned down')

    expect(pathOf(0)).toBe('/canon/7/approve')
    expect(bodyOf(0)).toEqual({ final_text: 'the mill burned down' })
    expect(pathOf(1)).toBe('/canon/pending')
    expect(res).toEqual(ok({ status: 'approved' }))
  })

  it('approve sends null when the text was not edited', async () => {
    safeFetch.mockResolvedValue(ok({}))

    await store().approveCanonProposal(7, '')

    expect(bodyOf(0)).toEqual({ final_text: null })
  })

  it('approve does not refresh after a failure', async () => {
    safeFetch.mockResolvedValue(fail('nope'))

    const res = await store().approveCanonProposal(7)

    expect(safeFetch).toHaveBeenCalledTimes(1)
    expect(res).toEqual(fail('nope'))
  })

  it('approve survives a thrown error', async () => {
    safeFetch.mockRejectedValue(new Error('x'))

    await expect(store().approveCanonProposal(7)).resolves.toEqual({ ok: false, error: 'x' })
  })

  it('reject posts and refreshes', async () => {
    safeFetch
      .mockResolvedValueOnce(ok({ status: 'rejected' }))
      .mockResolvedValueOnce(ok({ proposals: [] }))

    await store().rejectCanonProposal(9)

    expect(pathOf(0)).toBe('/canon/9/reject')
    expect(safeFetch).toHaveBeenCalledTimes(2)
  })

  it('reject does not refresh after a failure', async () => {
    safeFetch.mockResolvedValue(fail())

    await store().rejectCanonProposal(9)

    expect(safeFetch).toHaveBeenCalledTimes(1)
  })

  it('reject survives a thrown error', async () => {
    safeFetch.mockRejectedValue(new Error('x'))
    await expect(store().rejectCanonProposal(9)).resolves.toEqual({ ok: false, error: 'x' })
  })
})

describe('downtime', () => {
  it('URL-encodes the campaign name in the pending query', async () => {
    safeFetch.mockResolvedValue(ok({ pending: [] }))

    await store().fetchPendingDowntime('Curse of Strahd & Co')

    expect(pathOf()).toBe('/downtime/pending?campaign=Curse%20of%20Strahd%20%26%20Co')
  })

  it('omits the query entirely when no campaign is given', async () => {
    safeFetch.mockResolvedValue(ok({ pending: [] }))

    await store().fetchPendingDowntime('')

    expect(pathOf()).toBe('/downtime/pending')
  })

  it('stores the pending turns', async () => {
    safeFetch.mockResolvedValue(ok({ pending: [{ player: 'Ranger', action: 'tracks' }] }))

    await store().fetchPendingDowntime('Greenrest')

    expect(store().pendingDowntime).toEqual([{ player: 'Ranger', action: 'tracks' }])
  })

  it('tolerates a payload with no pending key', async () => {
    safeFetch.mockResolvedValue(ok({}))
    await store().fetchPendingDowntime('x')
    expect(store().pendingDowntime).toEqual([])
  })

  it('ignores a failed pending fetch', async () => {
    safeFetch.mockResolvedValue(fail())
    await store().fetchPendingDowntime('x')
    expect(store().pendingDowntime).toEqual([])
  })

  it('survives a thrown error on the pending fetch', async () => {
    safeFetch.mockRejectedValue(new Error('x'))
    await store().fetchPendingDowntime('x')
    expect(store().pendingDowntime).toEqual([])
  })

  it('submits a turn and refreshes the pending list', async () => {
    safeFetch
      .mockResolvedValueOnce(ok({ resolved: true }))
      .mockResolvedValueOnce(ok({ pending: [] }))

    const res = await store().submitDowntimeTurn('Ranger', 'tracks the cult', 'Greenrest')

    expect(pathOf(0)).toBe('/downtime')
    expect(bodyOf(0)).toEqual({
      player: 'Ranger', action: 'tracks the cult', campaign: 'Greenrest',
    })
    expect(pathOf(1)).toBe('/downtime/pending?campaign=Greenrest')
    expect(res).toEqual(ok({ resolved: true }))
  })

  it('normalises a missing campaign to an empty string', async () => {
    safeFetch.mockResolvedValue(ok({}))

    await store().submitDowntimeTurn('Ranger', 'tracks', undefined)

    expect(bodyOf(0).campaign).toBe('')
  })

  it('does not refresh after a failed submit', async () => {
    safeFetch.mockResolvedValue(fail('no session history'))

    const res = await store().submitDowntimeTurn('Ranger', 'tracks', 'Greenrest')

    expect(safeFetch).toHaveBeenCalledTimes(1)
    expect(res).toEqual(fail('no session history'))
  })

  it('survives a thrown error on submit', async () => {
    safeFetch.mockRejectedValue(new Error('x'))
    await expect(store().submitDowntimeTurn('a', 'b', 'c'))
      .resolves.toEqual({ ok: false, error: 'x' })
  })
})

describe('campaign wizard', () => {
  it('sets a field', () => {
    store().setWizardField('name', 'Greenrest')
    expect(store().campaignWizard.name).toBe('Greenrest')
  })

  it('sets the step', () => {
    store().setWizardStep(3)
    expect(store().campaignWizard.currentStep).toBe(3)
  })

  it('resets back to step 1', () => {
    store().setWizardField('name', 'Greenrest')
    store().setWizardStep(4)

    store().resetWizard()

    expect(store().campaignWizard.name).toBe('')
    expect(store().campaignWizard.currentStep).toBe(1)
    expect(store().campaignWizard.levelRange).toBe('1-5')
  })

  it('resets to exactly the shape it started with', () => {
    const initial = { ...store().campaignWizard }
    // Dirty every field, including the ones reset used to forget.
    for (const key of Object.keys(initial)) store().setWizardField(key, 'dirty')

    store().resetWizard()

    // Same keys, same values. resetWizard used to be a shorter literal than the
    // initial state, which left six of these undefined instead of ''.
    expect(store().campaignWizard).toEqual(initial)
    expect(Object.keys(store().campaignWizard).sort()).toEqual(Object.keys(initial).sort())
  })

  it('hands out a fresh object each reset rather than one that can be mutated later', () => {
    store().resetWizard()
    const first = store().campaignWizard
    store().setWizardField('name', 'Greenrest')
    store().resetWizard()

    expect(store().campaignWizard).not.toBe(first)
    expect(store().campaignWizard.name).toBe('')
  })

  describe('buildCampaign', () => {
    it('applies defaults for every unset field', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))

      await store().buildCampaign()

      expect(bodyOf()).toEqual({
        name: 'Unnamed Campaign',
        description: '',
        theme: '',
        seed_ideas: '',
        scale: '',
        level_range: '1-5',
        generate_prologue: true,
        foundry_world_name: 'Unnamed Campaign',
        character_concept: '',
        character_name: '',
        character_user_id: null,
      })
    })

    it('defaults the Foundry world name to the campaign name', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))
      store().setWizardField('name', 'Greenrest')

      await store().buildCampaign()

      expect(bodyOf().foundry_world_name).toBe('Greenrest')
    })

    it('honours an explicit Foundry world name', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))
      store().setWizardField('name', 'Greenrest')
      store().setWizardField('foundryWorldName', 'my-world')

      await store().buildCampaign()

      expect(bodyOf().foundry_world_name).toBe('my-world')
    })

    it('restores the default level range if the field was cleared', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))
      store().setWizardField('levelRange', '')

      await store().buildCampaign()

      expect(bodyOf().level_range).toBe('1-5')
    })

    it('treats generatePrologue as opt-out, not opt-in', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))
      store().setWizardField('generatePrologue', false)

      await store().buildCampaign()

      expect(bodyOf().generate_prologue).toBe(false)
    })

    it.each(['ok', 'complete'])('advances to step 4 on status %s', async (status) => {
      safeFetch.mockResolvedValue(ok({ status }))

      const res = await store().buildCampaign()

      expect(store().campaignWizard.currentStep).toBe(4)
      expect(store().campaignWizard.buildInProgress).toBe(false)
      expect(res.ok).toBe(true)
    })

    it('advances to step 4 on ready_to_start even without an ok status', async () => {
      safeFetch.mockResolvedValue(ok({ ready_to_start: true, status: 'partial' }))

      const res = await store().buildCampaign()

      expect(store().campaignWizard.currentStep).toBe(4)
      // ready_to_start moves the wizard on, but the result is not reported
      // as a clean success.
      expect(res.ok).toBe(false)
    })

    it('stays on step 3 when the build did not complete', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'partial' }))

      await store().buildCampaign()

      expect(store().campaignWizard.currentStep).toBe(3)
    })

    it('records a transport failure', async () => {
      safeFetch.mockResolvedValue(fail('relay down'))

      const res = await store().buildCampaign()

      expect(store().campaignWizard.buildError).toBe('relay down')
      expect(store().campaignWizard.buildInProgress).toBe(false)
      expect(res).toEqual({ ok: false, error: 'relay down' })
    })

    it('falls back to a generic build error', async () => {
      safeFetch.mockResolvedValue({ ok: false })

      await store().buildCampaign()

      expect(store().campaignWizard.buildError).toBe('Build failed')
    })

    it('clears buildInProgress after a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      const res = await store().buildCampaign()

      expect(store().campaignWizard.buildError).toBe('exploded')
      expect(store().campaignWizard.buildInProgress).toBe(false)
      expect(res).toEqual({ ok: false, error: 'exploded' })
    })
  })

  describe('importCampaign', () => {
    it('posts the import fields with defaults', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))

      await store().importCampaign()

      expect(pathOf()).toBe('/campaign/import')
      expect(bodyOf()).toEqual({
        source_path: '',
        campaign_name: 'Imported Campaign',
        foundry_world_name: 'Imported Campaign',
        level_range: '1-5',
        journal_pack: null,
      })
    })

    it('carries the source path and journal pack through', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))
      store().setWizardField('importSourcePath', '/vault/lmop')
      store().setWizardField('importJournalPack', 'lmop.journals')

      await store().importCampaign()

      expect(bodyOf().source_path).toBe('/vault/lmop')
      expect(bodyOf().journal_pack).toBe('lmop.journals')
    })

    it('restores the default level range if the field was cleared', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))
      store().setWizardField('levelRange', '')

      await store().importCampaign()

      expect(bodyOf().level_range).toBe('1-5')
    })

    it('surfaces a body-level error even on a 200', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'error', error: 'bad module' }))

      const res = await store().importCampaign()

      expect(store().campaignWizard.buildError).toBe('bad module')
      expect(res.ok).toBe(false)
    })

    it('falls back to a generic message for a body-level error', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'error' }))

      await store().importCampaign()

      expect(store().campaignWizard.buildError).toBe('Import failed')
    })

    it('clears the error on a clean import', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))

      await store().importCampaign()

      expect(store().campaignWizard.buildError).toBeNull()
      expect(store().campaignWizard.currentStep).toBe(4)
    })

    it('records a transport failure', async () => {
      safeFetch.mockResolvedValue(fail('gone'))

      const res = await store().importCampaign()

      expect(store().campaignWizard.buildError).toBe('gone')
      expect(res).toEqual({ ok: false, error: 'gone' })
    })

    it('falls back to a generic transport message', async () => {
      safeFetch.mockResolvedValue({ ok: false })
      await store().importCampaign()
      expect(store().campaignWizard.buildError).toBe('Import failed')
    })

    it('clears buildInProgress after a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      await store().importCampaign()

      expect(store().campaignWizard.buildInProgress).toBe(false)
      expect(store().campaignWizard.buildError).toBe('exploded')
    })
  })

  describe('scanWorld', () => {
    it('scans under the wizard name', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok', scenes: [] }))
      store().setWizardField('name', 'Greenrest')

      const res = await store().scanWorld()

      expect(pathOf()).toBe('/campaign/scan')
      expect(bodyOf()).toEqual({ world_name: 'Greenrest' })
      expect(store().campaignWizard.scanWorld).toEqual({ status: 'ok', scenes: [] })
      expect(res.ok).toBe(true)
    })

    it('defaults the world name', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))

      await store().scanWorld()

      expect(bodyOf()).toEqual({ world_name: 'Unnamed World' })
    })

    it('records a body-level scan error', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'error', error: 'no world open' }))

      const res = await store().scanWorld()

      expect(store().campaignWizard.buildError).toBe('no world open')
      expect(store().campaignWizard.scanWorld).toBeNull()
      expect(res).toEqual({ ok: false, error: 'no world open' })
    })

    it('records a transport failure', async () => {
      safeFetch.mockResolvedValue(fail('relay down'))

      const res = await store().scanWorld()

      expect(store().campaignWizard.buildError).toBe('relay down')
      expect(res).toEqual({ ok: false, error: 'relay down' })
    })

    it('clears buildInProgress after a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      const res = await store().scanWorld()

      expect(store().campaignWizard.buildInProgress).toBe(false)
      expect(res).toEqual({ ok: false, error: 'exploded' })
    })
  })
})

describe('campaign lifecycle actions', () => {
  describe.each([
    ['extendCampaignArc', '/campaign/extend', 'Extension failed'],
    ['teardownCampaign', '/campaign/teardown', 'Teardown failed'],
    ['optimizeCampaign', '/campaign/analyze-and-optimize', 'Optimization failed'],
    ['enrichCampaign', '/campaign/enrich', 'Enrichment failed'],
  ])('%s', (action, path, fallback) => {
    it('posts to the right endpoint and reports success', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))

      const res = await store()[action]('Greenrest', 3)

      expect(pathOf()).toBe(path)
      expect(res).toEqual({ ok: true, data: { status: 'ok' } })
    })

    it('treats a body-level {status: error} on a 200 as a failure', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'error', error: 'still deployed' }))

      const res = await store()[action]('Greenrest', 3)

      expect(res.ok).toBe(false)
      expect(res.error).toBe('still deployed')
    })

    it('reports a transport failure', async () => {
      safeFetch.mockResolvedValue(fail('offline'))

      const res = await store()[action]('Greenrest', 3)

      expect(res).toEqual({ ok: false, error: 'offline' })
    })

    it('falls back to a specific default message', async () => {
      safeFetch.mockResolvedValue({ ok: false })

      const res = await store()[action]('Greenrest', 3)

      expect(res.error).toBe(fallback)
    })

    it('catches a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      const res = await store()[action]('Greenrest', 3)

      expect(res).toEqual({ ok: false, error: 'exploded' })
    })
  })

  describe('enrichCampaign options', () => {
    it('nulls every unsupplied option rather than omitting it', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))

      await store().enrichCampaign('Greenrest')

      expect(bodyOf()).toEqual({
        campaign_name: 'Greenrest',
        source_path: null,
        journal_pack: null,
        journal_folder: null,
        force: false,
      })
    })

    it('passes the supplied options through', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))

      await store().enrichCampaign('Greenrest', {
        sourcePath: '/vault/lmop',
        journalPack: 'lmop.journals',
        journalFolder: 'Lore',
        force: true,
      })

      expect(bodyOf()).toEqual({
        campaign_name: 'Greenrest',
        source_path: '/vault/lmop',
        journal_pack: 'lmop.journals',
        journal_folder: 'Lore',
        force: true,
      })
    })

    it('coerces a truthy non-boolean force to a real boolean', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))

      await store().enrichCampaign('Greenrest', { force: 'yes' })

      expect(bodyOf().force).toBe(true)
    })
  })

  it('extendCampaignArc sends the current level', async () => {
    safeFetch.mockResolvedValue(ok({ status: 'ok' }))

    await store().extendCampaignArc('Greenrest', 5)

    expect(bodyOf()).toEqual({ campaign_name: 'Greenrest', current_level: 5 })
  })

  it('enrichCampaign posts the source and treats a body-level error as failure', async () => {
    safeFetch.mockResolvedValue(ok({ status: 'ok', sources: [] }))
    const res = await store().enrichCampaign('Greenrest', { sourcePath: '/src/atlas.pdf' })

    expect(pathOf()).toBe('/campaign/enrich')
    expect(bodyOf()).toEqual({
      campaign_name: 'Greenrest', source_path: '/src/atlas.pdf', journal_pack: null, journal_folder: null, force: false,
    })
    expect(res).toEqual({ ok: true, data: { status: 'ok', sources: [] } })

    safeFetch.mockResolvedValue(ok({ status: 'error', error: 'No readable sources found' }))
    expect(await store().enrichCampaign('Greenrest', { sourcePath: '/empty' }))
      .toEqual({ ok: false, error: 'No readable sources found' })
  })

  it('teardownCampaign prefers the first entry of an errors array', async () => {
    safeFetch.mockResolvedValue(ok({ status: 'error', errors: ['scene locked', 'and more'] }))

    const res = await store().teardownCampaign('Greenrest')

    expect(res.error).toBe('scene locked')
  })

  describe('restartCampaign', () => {
    it('reports success', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'ok' }))

      const res = await store().restartCampaign('Greenrest')

      expect(pathOf()).toBe('/campaign/restart')
      expect(bodyOf()).toEqual({ campaign_name: 'Greenrest' })
      expect(res).toEqual({ ok: true, data: { status: 'ok' } })
    })

    it('does NOT inspect a body-level error, unlike its siblings', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'error', error: 'nope' }))

      const res = await store().restartCampaign('Greenrest')

      // Pinned as-is: restart is the one action of the four that reports a
      // body-level {status:'error'} as success.
      expect(res.ok).toBe(true)
    })

    it('reports a transport failure with a fallback', async () => {
      safeFetch.mockResolvedValue({ ok: false })

      const res = await store().restartCampaign('Greenrest')

      expect(res).toEqual({ ok: false, error: 'Restart failed' })
    })

    it('catches a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))
      await expect(store().restartCampaign('x'))
        .resolves.toEqual({ ok: false, error: 'exploded' })
    })
  })

  describe('deployCampaign / regenerateAssets', () => {
    it('deploy returns the payload', async () => {
      safeFetch.mockResolvedValue(ok({ deployed: 4 }))

      await expect(store().deployCampaign('Greenrest')).resolves.toEqual({ deployed: 4 })
      expect(pathOf()).toBe('/campaign/deploy')
    })

    it('deploy reports an error object', async () => {
      safeFetch.mockResolvedValue(fail('401'))

      await expect(store().deployCampaign('Greenrest')).resolves.toEqual({ error: '401' })
    })

    it('regenerate asks for Foundry attachment', async () => {
      safeFetch.mockResolvedValue(ok({ maps: 2 }))

      await store().regenerateAssets('Greenrest')

      expect(bodyOf()).toEqual({ campaign_name: 'Greenrest', attach_to_foundry: true })
    })

    it('regenerate reports a status-shaped error', async () => {
      safeFetch.mockResolvedValue(fail('comfy down'))

      await expect(store().regenerateAssets('Greenrest'))
        .resolves.toEqual({ status: 'error', error: 'comfy down' })
    })
  })
})

describe('campaign session', () => {
  describe('fetchActiveSession', () => {
    it('records an active session', async () => {
      safeFetch.mockResolvedValue(ok({
        active: true, session_id: 'abc', campaign_name: 'Greenrest',
      }))

      await store().fetchActiveSession()

      expect(store().campaignSession.activeSession).toEqual({
        session_id: 'abc', campaign_name: 'Greenrest', status: 'started',
      })
    })

    it('clears the session when none is active', async () => {
      useStore.setState({
        campaignSession: { ...pristine.campaignSession, activeSession: { session_id: 'old' } },
      })
      safeFetch.mockResolvedValue(ok({ active: false }))

      await store().fetchActiveSession()

      expect(store().campaignSession.activeSession).toBeNull()
    })

    it('clears the session when active is true but the id is missing', async () => {
      safeFetch.mockResolvedValue(ok({ active: true }))

      await store().fetchActiveSession()

      expect(store().campaignSession.activeSession).toBeNull()
    })

    it('clears the session on a failed lookup', async () => {
      useStore.setState({
        campaignSession: { ...pristine.campaignSession, activeSession: { session_id: 'old' } },
      })
      safeFetch.mockResolvedValue(fail())

      await store().fetchActiveSession()

      expect(store().campaignSession.activeSession).toBeNull()
    })

    it('returns null after a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('x'))

      await expect(store().fetchActiveSession()).resolves.toBeNull()
    })
  })

  describe('listCampaigns', () => {
    it('stores the list and clears loading', async () => {
      safeFetch.mockResolvedValue(ok({ campaigns: [{ name: 'Greenrest' }] }))

      await store().listCampaigns()

      expect(store().campaignSession.campaigns).toEqual([{ name: 'Greenrest' }])
      expect(store().campaignSession.loading).toBe(false)
    })

    it('tolerates a payload with no campaigns key', async () => {
      safeFetch.mockResolvedValue(ok({}))
      await store().listCampaigns()
      expect(store().campaignSession.campaigns).toEqual([])
    })

    it('records an error and clears loading', async () => {
      safeFetch.mockResolvedValue(fail('offline'))

      const res = await store().listCampaigns()

      expect(store().campaignSession.error).toBe('offline')
      expect(store().campaignSession.loading).toBe(false)
      expect(res).toEqual({ ok: false, error: 'offline' })
    })

    it('clears loading after a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      const res = await store().listCampaigns()

      expect(store().campaignSession.loading).toBe(false)
      expect(res).toEqual({ error: 'exploded' })
    })
  })

  describe('getCampaign', () => {
    it('URL-encodes the name', async () => {
      safeFetch.mockResolvedValue(ok({ name: 'A/B' }))

      await store().getCampaign('A/B')

      expect(pathOf()).toBe('/campaign/get/A%2FB')
      expect(store().campaignSession.selectedCampaign).toEqual({ name: 'A/B' })
    })

    it('records an error', async () => {
      safeFetch.mockResolvedValue(fail('missing'))

      const res = await store().getCampaign('x')

      expect(store().campaignSession.error).toBe('missing')
      expect(res).toEqual({ ok: false, error: 'missing' })
    })

    it('clears loading after a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      const res = await store().getCampaign('x')

      expect(store().campaignSession.loading).toBe(false)
      expect(res).toEqual({ error: 'exploded' })
    })
  })

  describe('deleteCampaign', () => {
    it('deletes then refreshes the list', async () => {
      safeFetch
        .mockResolvedValueOnce(ok({ deleted: true }))
        .mockResolvedValueOnce(ok({ campaigns: [] }))

      const res = await store().deleteCampaign('Greenrest')

      expect(pathOf(0)).toBe('/campaign/delete')
      expect(bodyOf(0)).toEqual({ name: 'Greenrest' })
      expect(pathOf(1)).toBe('/campaign/list')
      expect(res).toEqual({ deleted: true })
    })

    it('does not refresh after a failure', async () => {
      safeFetch.mockResolvedValue(fail('in use'))

      const res = await store().deleteCampaign('Greenrest')

      expect(safeFetch).toHaveBeenCalledTimes(1)
      expect(store().campaignSession.error).toBe('in use')
      expect(res).toEqual({ ok: false, error: 'in use' })
    })

    it('clears loading after a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      const res = await store().deleteCampaign('x')

      expect(store().campaignSession.loading).toBe(false)
      expect(res).toEqual({ error: 'exploded' })
    })
  })

  describe('startCampaign', () => {
    it('records the started session', async () => {
      const data = { status: 'started', session_id: 'abc', campaign_name: 'Greenrest' }
      safeFetch.mockResolvedValue(ok(data))

      const res = await store().startCampaign('Greenrest')

      expect(bodyOf()).toEqual({ campaign_name: 'Greenrest', continue_from_last: false })
      expect(store().campaignSession.activeSession).toEqual(data)
      expect(store().campaignSession.loading).toBe(false)
      expect(res).toEqual(data)
    })

    it('passes continue_from_last through', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'started' }))

      await store().startCampaign('Greenrest', true)

      expect(bodyOf().continue_from_last).toBe(true)
    })

    it('records a transport failure', async () => {
      safeFetch.mockResolvedValue(fail('relay down'))

      const res = await store().startCampaign('Greenrest')

      expect(store().campaignSession.error).toBe('relay down')
      expect(store().campaignSession.loading).toBe(false)
      expect(res).toEqual({ ok: false, error: 'relay down' })
    })

    it('falls back to a generic transport message', async () => {
      safeFetch.mockResolvedValue({ ok: false })
      await store().startCampaign('Greenrest')
      expect(store().campaignSession.error).toBe('Failed to start campaign')
    })

    it('records a body-level error on a 200', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'blocked', error: 'world mismatch' }))

      await store().startCampaign('Greenrest')

      expect(store().campaignSession.error).toBe('world mismatch')
      expect(store().campaignSession.activeSession).toBeNull()
    })

    it('falls back to the body message field', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'blocked', message: 'not paired' }))

      await store().startCampaign('Greenrest')

      expect(store().campaignSession.error).toBe('not paired')
    })

    it('falls back to a generic body message', async () => {
      safeFetch.mockResolvedValue(ok({ status: 'blocked' }))

      await store().startCampaign('Greenrest')

      expect(store().campaignSession.error).toBe('Failed to start campaign')
    })

    it('clears loading after a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      const res = await store().startCampaign('x')

      expect(store().campaignSession.loading).toBe(false)
      expect(store().campaignSession.error).toBe('exploded')
      expect(res).toEqual({ error: 'exploded' })
    })
  })

  describe('endSession', () => {
    it('clears the active session', async () => {
      useStore.setState({
        campaignSession: {
          ...pristine.campaignSession,
          activeSession: { session_id: 'abc' },
          selectedCampaign: { name: 'Greenrest' },
        },
      })
      safeFetch.mockResolvedValue(ok({ status: 'ended' }))

      const res = await store().endSession('wrapping up')

      expect(bodyOf()).toEqual({ reason: 'wrapping up' })
      expect(store().campaignSession.activeSession).toBeNull()
      expect(store().campaignSession.selectedCampaign).toBeNull()
      expect(res).toEqual({ status: 'ended' })
    })

    it('defaults the reason', async () => {
      safeFetch.mockResolvedValue(ok({}))

      await store().endSession()

      expect(bodyOf()).toEqual({ reason: 'GM ended session' })
    })

    it('clears the session locally even when the server rejects the request', async () => {
      useStore.setState({
        campaignSession: { ...pristine.campaignSession, activeSession: { session_id: 'abc' } },
      })
      safeFetch.mockResolvedValue(fail('no active session'))

      await store().endSession()

      // endSession never checks res.ok, so the panel shows the session ended
      // regardless of what the server said. Pinned because it is a real
      // divergence between UI and server state, not an intended behaviour.
      expect(store().campaignSession.activeSession).toBeNull()
      expect(store().campaignSession.error).toBeNull()
    })

    it('clears loading after a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      const res = await store().endSession()

      expect(store().campaignSession.loading).toBe(false)
      expect(store().campaignSession.error).toBe('exploded')
      expect(res).toEqual({ error: 'exploded' })
    })
  })
})

describe('misc actions', () => {
  describe('testChat', () => {
    it('does nothing without a message', async () => {
      await store().testChat()
      expect(safeFetch).not.toHaveBeenCalled()
    })

    it('posts the message and clears the input', async () => {
      store().setChatTest({ message: 'hello', speaker: 'Ranger' })
      safeFetch.mockResolvedValue(ok({ reply: 'hi' }))

      await store().testChat()

      expect(bodyOf()).toEqual({ message: 'hello', speaker: 'Ranger' })
      expect(store().chatTest.result).toEqual({ reply: 'hi' })
      expect(store().chatTest.message).toBe('')
      expect(store().chatTest.loading).toBe(false)
    })

    it('defaults the speaker', async () => {
      store().setChatTest({ message: 'hello' })
      safeFetch.mockResolvedValue(ok({}))

      await store().testChat()

      expect(bodyOf().speaker).toBe('Player')
    })

    it('stores an error result', async () => {
      store().setChatTest({ message: 'hello' })
      safeFetch.mockResolvedValue(fail('no LLM'))

      await store().testChat()

      expect(store().chatTest.result).toEqual({ error: 'no LLM' })
    })

    it('stores a thrown error as a result', async () => {
      store().setChatTest({ message: 'hello' })
      safeFetch.mockRejectedValue(new Error('exploded'))

      await store().testChat()

      expect(store().chatTest.result).toEqual({ error: 'exploded' })
      expect(store().chatTest.loading).toBe(false)
    })
  })

  describe('searchSrd', () => {
    it('does nothing without a query', async () => {
      await store().searchSrd()
      expect(safeFetch).not.toHaveBeenCalled()
    })

    it('URL-encodes the query', async () => {
      store().setSrdQuery('magic missile')
      safeFetch.mockResolvedValue(ok({ results: 'a dart of force' }))

      await store().searchSrd()

      expect(pathOf()).toBe('/srd/search?query=magic%20missile')
      expect(store().srdResults).toBe('a dart of force')
    })

    it('tolerates a payload with no results key', async () => {
      store().setSrdQuery('x')
      safeFetch.mockResolvedValue(ok({}))

      await store().searchSrd()

      expect(store().srdResults).toBe('')
    })

    it('renders a failure into the results pane', async () => {
      store().setSrdQuery('x')
      safeFetch.mockResolvedValue(fail('index missing'))

      await store().searchSrd()

      expect(store().srdResults).toBe('Error: index missing')
    })

    it('renders a thrown error into the results pane', async () => {
      store().setSrdQuery('x')
      safeFetch.mockRejectedValue(new Error('exploded'))

      await store().searchSrd()

      expect(store().srdResults).toBe('Error: exploded')
    })
  })

  describe('performRoll', () => {
    it('posts the roll form', async () => {
      safeFetch.mockResolvedValue(ok({ total: 17 }))

      await store().performRoll()

      expect(pathOf()).toBe('/roll')
      expect(bodyOf()).toEqual({ formula: '1d20', speaker: 'GM', flavor: '' })
      expect(store().rollResult).toEqual({ total: 17 })
    })

    it('stores a failure as an error result', async () => {
      safeFetch.mockResolvedValue(fail('not connected'))

      await store().performRoll()

      expect(store().rollResult).toEqual({ error: 'not connected' })
    })

    it('stores a thrown error as an error result', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      await store().performRoll()

      expect(store().rollResult).toEqual({ error: 'exploded' })
    })
  })

  describe('saveSettings', () => {
    it('posts the settings then refreshes status', async () => {
      safeFetch.mockResolvedValue(ok({ ai_running: false }))

      await expect(store().saveSettings()).resolves.toBe(true)

      expect(pathOf(0)).toBe('/settings')
      expect(bodyOf(0)).toBe(store().settings)
      expect(pathOf(1)).toBe('/status')
    })

    it('reports a failure without refreshing', async () => {
      safeFetch.mockResolvedValue(fail('read-only config'))

      await expect(store().saveSettings()).resolves.toBe(false)

      expect(safeFetch).toHaveBeenCalledTimes(1)
      expect(store().statusMessage).toBe('Failed to save settings: read-only config')
    })

    it('reports a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      await expect(store().saveSettings()).resolves.toBe(false)

      expect(store().statusMessage).toBe('Failed to save settings: exploded')
    })
  })

  describe('createSession', () => {
    it('creates then refreshes status and events', async () => {
      safeFetch.mockResolvedValue(ok({ ai_running: false }))

      await store().createSession()

      expect(pathOf(0)).toBe('/session/new')
      expect(pathOf(1)).toBe('/status')
      expect(pathOf(2)).toBe('/session/events?limit=50')
    })

    it('survives a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      await expect(store().createSession()).resolves.toBeUndefined()
    })
  })

  describe('updateGameState', () => {
    it('posts the single changed field then refetches state', async () => {
      safeFetch
        .mockResolvedValueOnce(ok({}))
        .mockResolvedValueOnce(ok({ mode: 'combat' }))

      await store().updateGameState('mode', 'combat')

      expect(pathOf(0)).toBe('/state/update')
      expect(bodyOf(0)).toEqual({ mode: 'combat' })
      expect(store().gameState).toEqual({ mode: 'combat' })
    })

    it('reports a failure without refetching', async () => {
      safeFetch.mockResolvedValue(fail('invalid mode'))

      await store().updateGameState('mode', 'nonsense')

      expect(safeFetch).toHaveBeenCalledTimes(1)
      expect(store().statusMessage).toBe('invalid mode')
    })

    it('falls back to a generic message', async () => {
      safeFetch.mockResolvedValue({ ok: false })
      await store().updateGameState('mode', 'x')
      expect(store().statusMessage).toBe('State update failed')
    })

    it('survives a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))
      await expect(store().updateGameState('mode', 'x')).resolves.toBeUndefined()
    })
  })

  describe.each([
    ['relayStart', '/relay/start'],
    ['relayStop', '/relay/stop'],
    ['headlessStart', '/relay/headless/start'],
    ['relayRestart', '/relay/restart'],
  ])('%s', (action, path) => {
    it('posts and refreshes status on success', async () => {
      safeFetch.mockResolvedValue(ok({ running: true }))

      const res = await store()[action]()

      expect(pathOf(0)).toBe(path)
      expect(pathOf(1)).toBe('/status')
      expect(res).toEqual({ running: true })
    })

    it('reports a failure without refreshing', async () => {
      safeFetch.mockResolvedValue(fail('port in use'))

      const res = await store()[action]()

      expect(safeFetch).toHaveBeenCalledTimes(1)
      expect(res).toEqual({ ok: false, error: 'port in use' })
    })

    it('catches a thrown error', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      await expect(store()[action]()).resolves.toEqual({ error: 'exploded' })
    })
  })

  describe('sendDirectGMMessage', () => {
    it('appends the operator message then the reply', async () => {
      safeFetch.mockResolvedValue(ok({ response: 'The mill is quiet.' }))

      await store().sendDirectGMMessage('what is at the mill?')

      expect(store().gmChatMessages).toEqual([
        { role: 'user', content: 'what is at the mill?' },
        { role: 'assistant', content: 'The mill is quiet.' },
      ])
    })

    it('keeps earlier turns in the transcript', async () => {
      safeFetch.mockResolvedValue(ok({ response: 'second' }))
      useStore.setState({ gmChatMessages: [{ role: 'assistant', content: 'first' }] })

      await store().sendDirectGMMessage('again')

      expect(store().gmChatMessages).toHaveLength(3)
      expect(store().gmChatMessages[0]).toEqual({ role: 'assistant', content: 'first' })
    })

    it('substitutes a placeholder when the reply is empty', async () => {
      safeFetch.mockResolvedValue(ok({}))

      await store().sendDirectGMMessage('hi')

      expect(store().gmChatMessages[1]).toEqual({
        role: 'assistant', content: 'No response received',
      })
    })

    it('shows a failure as an assistant turn, keeping the question visible', async () => {
      safeFetch.mockResolvedValue(fail('budget exhausted'))

      await store().sendDirectGMMessage('hi')

      expect(store().gmChatMessages).toEqual([
        { role: 'user', content: 'hi' },
        { role: 'assistant', content: 'Error: budget exhausted' },
      ])
    })

    it('falls back to a generic error message', async () => {
      safeFetch.mockResolvedValue({ ok: false })

      await store().sendDirectGMMessage('hi')

      expect(store().gmChatMessages[1].content).toBe('Error: Failed to get response')
    })

    it('shows a thrown error as an assistant turn', async () => {
      safeFetch.mockRejectedValue(new Error('exploded'))

      await store().sendDirectGMMessage('hi')

      expect(store().gmChatMessages[1]).toEqual({
        role: 'assistant', content: 'Error: exploded',
      })
    })
  })
})
