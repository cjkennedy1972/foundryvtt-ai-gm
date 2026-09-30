import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore, useStore } from '../test/store-harness.jsx'
import Settings from './Settings.jsx'

let fetchSettings
let saveSettings
let alertSpy

const MASK = '••••••••'

// The real setSetting/setLlmMode are left in place: the point of these tests
// is that the controls are wired to the store the page actually saves from.
function seed(extra = {}) {
  const { settings, ...rest } = extra
  return renderWithStore(<Settings />, {
    fetchSettings,
    saveSettings,
    settings: { ...useStore.getState().settings, ...settings },
    ...rest,
  })
}

beforeEach(() => {
  fetchSettings = vi.fn(async () => {})
  saveSettings = vi.fn(async () => true)
  // jsdom's alert throws "not implemented"; the page uses it for the save receipt.
  alertSpy = vi.fn()
  vi.stubGlobal('alert', alertSpy)
})

afterEach(() => {
  vi.unstubAllGlobals()
  resetStore()
})

describe('Settings', () => {
  it('loads the saved settings on mount', () => {
    seed()

    expect(fetchSettings).toHaveBeenCalledTimes(1)
  })

  it('offers every supported provider', () => {
    seed()

    for (const name of [
      'Anthropic (Claude)', 'Google (Gemini)', 'OpenAI', 'OpenRouter',
      'Custom / Local (OpenAI-compatible)',
    ]) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
    }
  })

  it('marks the current provider', () => {
    seed({ llmMode: 'openai' })

    expect(screen.getByRole('button', { name: 'OpenAI' })).toHaveClass('btn-primary')
    expect(screen.getByRole('button', { name: 'OpenAI' })).not.toHaveClass('btn-sm')
  })

  it.each([
    ['Anthropic (Claude)', 'anthropic', 'https://api.anthropic.com/v1'],
    ['Google (Gemini)', 'google', 'https://generativelanguage.googleapis.com/v1beta/openai/'],
    ['OpenAI', 'openai', 'https://api.openai.com/v1'],
    ['OpenRouter', 'openrouter', 'https://openrouter.ai/api/v1'],
  ])('picking %s fills in its base URL', async (name, mode, baseUrl) => {
    const user = userEvent.setup()
    seed()

    await user.click(screen.getByRole('button', { name }))

    expect(useStore.getState().llmMode).toBe(mode)
    expect(useStore.getState().settings.llm_base_url).toBe(baseUrl)
  })

  it('leaves the base URL alone when switching to a custom endpoint', async () => {
    const user = userEvent.setup()
    seed({ llmMode: 'openai', settings: { llm_base_url: 'http://localhost:8800/v1' } })

    await user.click(screen.getByRole('button', { name: 'Custom / Local (OpenAI-compatible)' }))

    expect(useStore.getState().llmMode).toBe('local')
    expect(useStore.getState().settings.llm_base_url).toBe('http://localhost:8800/v1')
  })

  it('offers a model list for a provider that has one', () => {
    seed({ llmMode: 'openai', settings: { model: 'gpt-4o-mini' } })

    const select = screen.getByRole('combobox')
    expect(select).toHaveValue('gpt-4o-mini')
    expect([...select.options].map((o) => o.value)).toEqual(['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'])
    expect(screen.getByText(/select a known model/i)).toBeInTheDocument()
  })

  it('takes a free-text model for a custom endpoint', async () => {
    const user = userEvent.setup()
    seed({ llmMode: 'local' })

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.getByText(/type your model name/i)).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText(/your custom model name/i), 'Qwen3-32B')
    expect(useStore.getState().settings.model).toBe('Qwen3-32B')
  })

  it('falls back to the first provider when the mode is one it does not know', () => {
    seed({ llmMode: 'nonesuch' })

    // Anthropic is providers[0], so its model list is what gets offered.
    expect([...screen.getByRole('combobox').options].map((o) => o.value)).toContain('claude-sonnet-4')
  })

  it('selects a model from the list', async () => {
    const user = userEvent.setup()
    seed({ llmMode: 'openai', settings: { model: 'gpt-4o' } })

    await user.selectOptions(screen.getByRole('combobox'), 'gpt-4-turbo')

    expect(useStore.getState().settings.model).toBe('gpt-4-turbo')
  })

  it('edits the base URL', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(/localhost:8800/), 'http://box:8080/v1')

    expect(useStore.getState().settings.llm_base_url).toBe('http://box:8080/v1')
  })

  it('asks for an API key when none is set on the server', () => {
    seed()

    expect(screen.getByPlaceholderText(/your api key \(leave empty/i)).toBeInTheDocument()
    expect(screen.getByText(/required for cloud providers/i)).toBeInTheDocument()
  })

  it('says a key is already set rather than showing the mask as a value to keep', () => {
    seed({ settings: { llm_api_key: MASK } })

    expect(screen.getByPlaceholderText(/key is set on server/i)).toBeInTheDocument()
    expect(screen.getByText(/leave blank to keep it unchanged/i)).toBeInTheDocument()
  })

  it('treats a real key as unmasked', () => {
    seed({ settings: { llm_api_key: 'sk-real' } })

    expect(screen.getByPlaceholderText(/your api key \(leave empty/i)).toBeInTheDocument()
  })

  it('does not recognise the relay key as a secret, so its "already set" hint never shows', () => {
    seed({ settings: { relayApiKey: MASK } })

    // BUG: config.js lists the secret as `relay_api_key`, but the store and
    // this page call it `relayApiKey`, so isSecretMasked('relayApiKey') is
    // always false and the mask leaks into the field as a literal value.
    // Asserted as-is; fixing the key mismatch is outside this change.
    expect(screen.getByPlaceholderText(/auto-provisioned when relay_managed/i)).toBeInTheDocument()
    expect(screen.getByDisplayValue(MASK)).toBeInTheDocument()
  })

  it('shows the temperature next to its slider and updates both together', () => {
    seed({ settings: { temperature: 0.7 } })

    expect(screen.getByText('Temperature: 0.7')).toBeInTheDocument()

    // A range input is driven by its value, so change it directly.
    fireEvent.change(screen.getByRole('slider'), { target: { value: '0.25' } })

    expect(useStore.getState().settings.temperature).toBe(0.25)
    expect(screen.getByText('Temperature: 0.25')).toBeInTheDocument()
  })

  it('stores the temperature as a number, not the slider\'s string', async () => {
    seed()

    await userEvent.setup().type(screen.getByRole('slider'), '{arrowright}')

    expect(typeof useStore.getState().settings.temperature).toBe('number')
  })

  it('clamps a negative token budget to zero rather than saving it', () => {
    seed()

    // Typed through, a number input drops the minus sign; changed directly it
    // reaches the handler, which is the clamp this asserts.
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '-5' } })

    expect(useStore.getState().settings.llm_token_budget).toBe(0)
  })

  it('reads a non-numeric token budget as zero', async () => {
    const user = userEvent.setup()
    seed({ settings: { llm_token_budget: 500 } })

    await user.clear(screen.getByRole('spinbutton'))

    expect(useStore.getState().settings.llm_token_budget).toBe(0)
  })

  it('keeps a positive token budget as an integer', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByRole('spinbutton'), '4096')

    expect(useStore.getState().settings.llm_token_budget).toBe(4096)
  })

  it.each([
    [/Aethelwyrd GM/, 'aiName', 'Sage'],
    [/mysterious, immersive/, 'aiTone', 'dry and wry'],
    [/localhost:3010/, 'relayUrl', 'http://relay:3010'],
    [/127.0.0.1:18188/, 'comfyuiUrl', 'http://comfy:18188'],
  ])('edits the %s field', async (placeholder, key, value) => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(placeholder), value)

    expect(useStore.getState().settings[key]).toBe(value)
  })

  it('confirms a successful save and warns which changes need a restart', async () => {
    const user = userEvent.setup()
    seed()

    await user.click(screen.getByRole('button', { name: /save settings/i }))

    expect(saveSettings).toHaveBeenCalledTimes(1)
    expect(alertSpy).toHaveBeenCalledWith(expect.stringMatching(/require a server restart/i))
  })

  it('reports the store\'s own reason when the save fails', async () => {
    saveSettings = vi.fn(async () => {
      useStore.setState({ statusMessage: 'Failed to save settings: engine offline' })
      return false
    })
    const user = userEvent.setup()
    seed()

    await user.click(screen.getByRole('button', { name: /save settings/i }))

    expect(alertSpy).toHaveBeenCalledWith('Failed to save settings: engine offline')
  })
})
