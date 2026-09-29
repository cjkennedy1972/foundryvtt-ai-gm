import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SetupWizard from './SetupWizard'

// Each step's Continue used to set the step it was already on, so the LLM and
// Relay steps never advanced.

function reply(body, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) })
}

function mockApi(overrides = {}) {
  const routes = {
    '/api/setup/probe-llm': { healthy: true, models: [{ id: 'qwen3-32b' }] },
    '/api/setup/start-wizard': { status: 'ok', dashboard_url: 'http://relay' },
    '/api/setup/pairing-code': { code: 'ABC123' },
    '/api/setup/write-env': { status: 'ok' },
    '/api/setup/provision-relay-scoped-key': { status: 'ok' },
    ...overrides,
  }
  global.fetch = vi.fn((url) => reply(routes[url] ?? {}))
}

async function toLLMStep() {
  render(<SetupWizard />)
  fireEvent.click(screen.getByText(/Get Started/))
  await screen.findByText('LLM Configuration')
}

async function probeAndContinue() {
  fireEvent.change(screen.getByPlaceholderText('Your LLM API key'), { target: { value: 'sk-test' } })
  fireEvent.click(screen.getByText(/List Available Models/))
  await screen.findByText('qwen3-32b')
  fireEvent.click(screen.getByText(/Continue/))
}

afterEach(() => vi.restoreAllMocks())

describe('SetupWizard', () => {
  it('advances from the LLM step to the relay step', async () => {
    mockApi()
    await toLLMStep()
    await probeAndContinue()
    expect(await screen.findByText('Relay & Pairing')).toBeTruthy()
  })

  it('advances from the relay step to the campaign step', async () => {
    mockApi()
    await toLLMStep()
    await probeAndContinue()
    await screen.findByText('ABC123')
    fireEvent.click(screen.getByText(/Continue/))
    await waitFor(() => expect(screen.queryByText('Relay & Pairing')).toBeNull())
  })

  it('says why when the relay fails to start', async () => {
    mockApi({ '/api/setup/start-wizard': { detail: 'Relay manager not initialized' } })
    await toLLMStep()
    await probeAndContinue()
    expect(await screen.findByText('Relay manager not initialized')).toBeTruthy()
  })
})
