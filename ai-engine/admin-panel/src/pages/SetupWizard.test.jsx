import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore } from '../test/store-harness.jsx'
import SetupWizard from './SetupWizard.jsx'

// Each step's Continue used to set the step it was already on, so the LLM and
// Relay steps never advanced.

const DEFAULT_ROUTES = {
  '/api/setup/probe-llm': { healthy: true, models: [{ id: 'qwen3-32b' }, { id: 'qwen3-8b', name: 'Qwen 3 8B' }] },
  '/api/setup/start-wizard': { status: 'ok', dashboard_url: 'http://relay.local' },
  '/api/setup/pairing-code': { code: 'ABC123' },
  '/api/setup/write-env': { status: 'ok' },
  '/api/setup/provision-relay-scoped-key': { status: 'ok' },
}

let routes
let calls

/** Records every request and answers from `routes`. */
function mockApi(overrides = {}) {
  routes = { ...DEFAULT_ROUTES, ...overrides }
  calls = []
  globalThis.fetch = vi.fn(async (url, init) => {
    calls.push({ url, body: init?.body && JSON.parse(init.body) })
    const answer = routes[url]
    if (answer instanceof Error) throw answer
    return { ok: true, json: async () => answer ?? {} }
  })
}

const bodyFor = (url) => calls.find((c) => c.url === url)?.body

/** Renders the wizard. It reads nothing from the store, but the harness keeps
 * the store pristine between tests all the same. */
function render() {
  return renderWithStore(<SetupWizard />)
}

/** Welcome → LLM. */
async function toLLMStep(user) {
  render()
  await user.click(screen.getByRole('button', { name: /get started/i }))
  await screen.findByRole('heading', { name: 'LLM Configuration' })
}

/** Probe with a key, then Continue. Leaves the wizard on the relay step. */
async function probeAndContinue(user) {
  await user.type(screen.getByLabelText('API Key'), 'sk-test')
  await user.click(screen.getByRole('button', { name: /list available models/i }))
  await screen.findByRole('option', { name: 'qwen3-32b' })
  await user.click(screen.getByRole('button', { name: /continue/i }))
}

/** Welcome → LLM → Relay. */
async function toRelayStep(user) {
  await toLLMStep(user)
  await probeAndContinue(user)
  await screen.findByRole('heading', { name: 'Relay & Pairing' })
}

/** Welcome → … → Campaign. */
async function toCampaignStep(user) {
  await toRelayStep(user)
  await screen.findByText('ABC123')
  await user.click(screen.getByRole('button', { name: /continue/i }))
  await screen.findByRole('heading', { name: 'Campaign Settings' })
}

/** Welcome → … → Complete. */
async function toCompleteStep(user) {
  await toCampaignStep(user)
  await user.click(screen.getByRole('button', { name: /complete setup/i }))
  await screen.findByRole('heading', { name: /setup complete/i })
}

/** The circles across the top, as the operator reads them. */
const indicators = () =>
  [...document.querySelectorAll('span')]
    .filter((el) => el.style.borderRadius === '50%')
    .map((el) => el.textContent)

beforeEach(() => mockApi())

afterEach(resetStore)

describe('SetupWizard', () => {
  it('opens on the welcome step', () => {
    render()

    expect(screen.getByRole('heading', { name: /welcome to ai gamemaster/i })).toBeInTheDocument()
    expect(indicators()).toEqual(['1', '2', '3', '4', '5'])
  })

  it('ticks off the steps already done', async () => {
    const user = userEvent.setup()
    await toRelayStep(user)

    expect(indicators()).toEqual(['✓', '✓', '3', '4', '5'])
  })

  it('will not probe without an API key', async () => {
    const user = userEvent.setup()
    await toLLMStep(user)

    expect(screen.getByRole('button', { name: /list available models/i })).toBeDisabled()
  })

  it('will not probe on whitespace alone', async () => {
    const user = userEvent.setup()
    await toLLMStep(user)

    await user.type(screen.getByLabelText('API Key'), '   ')

    expect(screen.getByRole('button', { name: /list available models/i })).toBeDisabled()
  })

  it('trims the endpoint and key it probes with', async () => {
    const user = userEvent.setup()
    await toLLMStep(user)

    await user.clear(screen.getByLabelText('LLM Base URL'))
    await user.type(screen.getByLabelText('LLM Base URL'), '  http://box:8080/v1  ')
    await user.type(screen.getByLabelText('API Key'), '  sk-test  ')
    await user.click(screen.getByRole('button', { name: /list available models/i }))

    await waitFor(() => expect(bodyFor('/api/setup/probe-llm')).toEqual({
      base_url: 'http://box:8080/v1',
      api_key: 'sk-test',
    }))
  })

  it('shows progress while probing', async () => {
    let release
    const pending = new Promise((r) => { release = r })
    const user = userEvent.setup()
    await toLLMStep(user)
    globalThis.fetch = vi.fn(() => pending)

    await user.type(screen.getByLabelText('API Key'), 'sk-test')
    await user.click(screen.getByRole('button', { name: /list available models/i }))

    expect(screen.getByRole('button', { name: /probing/i })).toBeDisabled()

    await act(async () => release({ ok: true, json: async () => DEFAULT_ROUTES['/api/setup/probe-llm'] }))
    expect(screen.getByRole('button', { name: /list available models/i })).toBeEnabled()
  })

  it('offers the discovered models, preferring a name over an id when given one', async () => {
    const user = userEvent.setup()
    await toLLMStep(user)

    await user.type(screen.getByLabelText('API Key'), 'sk-test')
    await user.click(screen.getByRole('button', { name: /list available models/i }))

    const select = await screen.findByRole('combobox')
    expect([...select.options].map((o) => o.textContent)).toEqual(['qwen3-32b', 'Qwen 3 8B'])
    // The first is preselected so Continue is reachable without another click.
    expect(select).toHaveValue('qwen3-32b')
  })

  it('carries a hand-picked model forward instead of the preselected one', async () => {
    const user = userEvent.setup()
    await toLLMStep(user)

    await user.type(screen.getByLabelText('API Key'), 'sk-test')
    await user.click(screen.getByRole('button', { name: /list available models/i }))
    await user.selectOptions(await screen.findByRole('combobox'), 'qwen3-8b')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await screen.findByText('ABC123')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.click(await screen.findByRole('button', { name: /complete setup/i }))

    await waitFor(() => expect(bodyFor('/api/setup/write-env').model).toBe('qwen3-8b'))
  })

  it('cannot continue past the LLM step before a model is chosen', async () => {
    const user = userEvent.setup()
    await toLLMStep(user)

    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
  })

  it('says why when the endpoint reports itself unhealthy', async () => {
    mockApi({ '/api/setup/probe-llm': { healthy: false, message: 'connection refused' } })
    const user = userEvent.setup()
    await toLLMStep(user)

    await user.type(screen.getByLabelText('API Key'), 'sk-test')
    await user.click(screen.getByRole('button', { name: /list available models/i }))

    expect(await screen.findByText('connection refused')).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('falls back to a generic reason when the endpoint gives none', async () => {
    mockApi({ '/api/setup/probe-llm': { healthy: false } })
    const user = userEvent.setup()
    await toLLMStep(user)

    await user.type(screen.getByLabelText('API Key'), 'sk-test')
    await user.click(screen.getByRole('button', { name: /list available models/i }))

    expect(await screen.findByText(/failed to probe llm endpoint/i)).toBeInTheDocument()
  })

  it('surfaces a transport failure on the probe', async () => {
    mockApi({ '/api/setup/probe-llm': new Error('network down') })
    const user = userEvent.setup()
    await toLLMStep(user)

    await user.type(screen.getByLabelText('API Key'), 'sk-test')
    await user.click(screen.getByRole('button', { name: /list available models/i }))

    expect(await screen.findByText('network down')).toBeInTheDocument()
  })

  it('offers no model list when the endpoint is healthy but empty', async () => {
    mockApi({ '/api/setup/probe-llm': { healthy: true, models: [] } })
    const user = userEvent.setup()
    await toLLMStep(user)

    await user.type(screen.getByLabelText('API Key'), 'sk-test')
    await user.click(screen.getByRole('button', { name: /list available models/i }))

    await waitFor(() => expect(bodyFor('/api/setup/probe-llm')).toBeTruthy())
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
  })

  it('advances from the LLM step to the relay step', async () => {
    const user = userEvent.setup()
    await toLLMStep(user)
    await probeAndContinue(user)

    expect(await screen.findByRole('heading', { name: 'Relay & Pairing' })).toBeInTheDocument()
  })

  it('goes back to the welcome step from the LLM step', async () => {
    const user = userEvent.setup()
    await toLLMStep(user)

    await user.click(screen.getByRole('button', { name: /back/i }))

    expect(screen.getByRole('heading', { name: /welcome to ai gamemaster/i })).toBeInTheDocument()
  })

  it('starts the relay and shows the pairing code and dashboard link', async () => {
    const user = userEvent.setup()
    await toRelayStep(user)

    expect(await screen.findByText('ABC123')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /the relay dashboard/i })).toHaveAttribute('href', 'http://relay.local')
    expect(calls.map((c) => c.url)).toContain('/api/setup/start-wizard')
  })

  it('says why when the relay fails to start', async () => {
    mockApi({ '/api/setup/start-wizard': { detail: 'Relay manager not initialized' } })
    const user = userEvent.setup()
    await toRelayStep(user)

    expect(await screen.findByText('Relay manager not initialized')).toBeInTheDocument()
    expect(screen.queryByText(/pairing code/i)).not.toBeInTheDocument()
  })

  it('falls back to a generic reason when the relay gives none', async () => {
    mockApi({ '/api/setup/start-wizard': { status: 'error' } })
    const user = userEvent.setup()
    await toRelayStep(user)

    expect(await screen.findByText(/the relay did not start/i)).toBeInTheDocument()
  })

  it('surfaces a transport failure on starting the relay', async () => {
    mockApi({ '/api/setup/start-wizard': new Error('socket hang up') })
    const user = userEvent.setup()
    await toRelayStep(user)

    expect(await screen.findByText('socket hang up')).toBeInTheDocument()
  })

  it('advances from the relay step to the campaign step', async () => {
    const user = userEvent.setup()
    await toCampaignStep(user)

    expect(screen.getByRole('heading', { name: 'Campaign Settings' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Relay & Pairing' })).not.toBeInTheDocument()
  })

  it('goes back to the LLM step from the relay step', async () => {
    const user = userEvent.setup()
    await toRelayStep(user)
    await screen.findByText('ABC123')

    await user.click(screen.getByRole('button', { name: /back/i }))

    expect(screen.getByRole('heading', { name: 'LLM Configuration' })).toBeInTheDocument()
  })

  it('offers workable campaign defaults', async () => {
    const user = userEvent.setup()
    await toCampaignStep(user)

    expect(screen.getByLabelText('Campaign Vault Path')).toHaveValue('~/Vaults/MyStuff/Dungeons_and_Dragons')
    expect(screen.getByLabelText('GM Name')).toHaveValue('Sage')
    expect(screen.getByLabelText(/^GM Tone/)).toHaveValue('mysterious, immersive, high fantasy')
  })

  it('does not ask for a campaign name, since nothing on the server would use it', async () => {
    const user = userEvent.setup()
    await toCampaignStep(user)

    // Campaigns are named on the Create Campaign page, which is where the name
    // takes effect. This step used to ask for one and then drop it.
    expect(screen.queryByText(/campaign name/i)).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText('My Campaign')).not.toBeInTheDocument()
  })

  it('writes the LLM and campaign answers together, then provisions the relay key', async () => {
    const user = userEvent.setup()
    await toCampaignStep(user)

    await user.clear(screen.getByLabelText('Campaign Vault Path'))
    await user.type(screen.getByLabelText('Campaign Vault Path'), '/vault/greenrest')
    await user.clear(screen.getByLabelText('GM Name'))
    await user.type(screen.getByLabelText('GM Name'), 'Thaddeus')
    await user.clear(screen.getByLabelText(/^GM Tone/))
    await user.type(screen.getByLabelText(/^GM Tone/), 'dry and wry')
    await user.click(screen.getByRole('button', { name: /complete setup/i }))

    await waitFor(() => expect(bodyFor('/api/setup/write-env')).toEqual({
      llm_api_key: 'sk-test',
      llm_base_url: 'http://localhost:8800/v1',
      model: 'qwen3-32b',
      campaign_vault_path: '/vault/greenrest',
      ai_name: 'Thaddeus',
      ai_tone: 'dry and wry',
    }))
    expect(bodyFor('/api/setup/write-env')).not.toHaveProperty('campaign_name')
    // Only after the write succeeds — a key provisioned against an unwritten
    // config would be orphaned.
    const urls = calls.map((c) => c.url)
    expect(urls.indexOf('/api/setup/provision-relay-scoped-key')).toBeGreaterThan(urls.indexOf('/api/setup/write-env'))
  })

  it('goes back to the relay step from the campaign step', async () => {
    const user = userEvent.setup()
    await toCampaignStep(user)

    await user.click(screen.getByRole('button', { name: /back/i }))

    expect(await screen.findByRole('heading', { name: 'Relay & Pairing' })).toBeInTheDocument()
  })

  it('blocks the wizard behind an overlay while the config is being written', async () => {
    let release
    const user = userEvent.setup()
    await toCampaignStep(user)
    // Only the write is held; the provisioning call that follows it still has
    // to complete, or the overlay would never come down for any reason.
    const answer = globalThis.fetch
    globalThis.fetch = vi.fn((url, init) =>
      url === '/api/setup/write-env'
        ? new Promise((r) => { release = () => r({ ok: true, json: async () => ({ status: 'ok' }) }) })
        : answer(url, init))

    await user.click(screen.getByRole('button', { name: /complete setup/i }))

    expect(screen.getByText(/writing configuration/i)).toBeInTheDocument()

    await act(async () => { release() })
    await waitFor(() => expect(screen.queryByText(/writing configuration/i)).not.toBeInTheDocument())
  })

  it('reaches the completion step', async () => {
    const user = userEvent.setup()
    await toCompleteStep(user)

    expect(screen.getByRole('button', { name: /start building campaign/i })).toBeInTheDocument()
    expect(indicators()).toEqual(['✓', '✓', '✓', '✓', '5'])
  })

  it('keeps the operator on the campaign step and says why when the write fails', async () => {
    mockApi({ '/api/setup/write-env': { detail: 'cannot write .env: read-only filesystem' } })
    const user = userEvent.setup()
    await toCampaignStep(user)

    await user.click(screen.getByRole('button', { name: /complete setup/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('cannot write .env: read-only filesystem')
    expect(screen.getByRole('heading', { name: 'Campaign Settings' })).toBeInTheDocument()
    // And no key is provisioned against a config that was never written.
    expect(calls.map((c) => c.url)).not.toContain('/api/setup/provision-relay-scoped-key')
  })

  it('falls back to a generic reason when the write fails without one', async () => {
    mockApi({ '/api/setup/write-env': { status: 'error' } })
    const user = userEvent.setup()
    await toCampaignStep(user)

    await user.click(screen.getByRole('button', { name: /complete setup/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/saving the configuration failed/i)
  })

  it('surfaces a transport failure on the write', async () => {
    mockApi({ '/api/setup/write-env': new Error('socket hang up') })
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const user = userEvent.setup()
    await toCampaignStep(user)

    await user.click(screen.getByRole('button', { name: /complete setup/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('socket hang up')
  })

  it('reloads the panel two seconds after the operator starts building', async () => {
    const reload = vi.fn()
    const user = userEvent.setup()
    await toCompleteStep(user)
    vi.useFakeTimers()
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, reload })

    await act(async () => { screen.getByRole('button', { name: /start building campaign/i }).click() })
    expect(screen.getByRole('heading', { name: 'Loading...' })).toBeInTheDocument()
    expect(reload).not.toHaveBeenCalled()

    await act(async () => { vi.advanceTimersByTime(2000) })
    expect(reload).toHaveBeenCalledTimes(1)
  })
})
