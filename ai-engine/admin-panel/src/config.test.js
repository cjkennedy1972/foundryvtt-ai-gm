import { describe, expect, it, vi } from 'vitest'

import { API_BASE, SECRET_KEYS, WS_PATH, relayAdminUrl, wsUrl } from './config.js'

describe('defaults', () => {
  it('serves the API from a same-origin /api prefix', () => {
    expect(API_BASE).toBe('/api')
  })

  it('serves the socket from /api/ws', () => {
    expect(WS_PATH).toBe('/api/ws')
  })

  it('has no relay admin URL unless one is configured at build time', () => {
    expect(relayAdminUrl()).toBe('')
  })
})

describe('wsUrl', () => {
  it('uses ws:// on a plain-HTTP page', () => {
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:18080' })

    expect(wsUrl()).toBe('ws://localhost:18080/api/ws')
  })

  it('upgrades to wss:// on an HTTPS page', () => {
    vi.stubGlobal('location', { protocol: 'https:', host: 'gm.example.com' })

    expect(wsUrl()).toBe('wss://gm.example.com/api/ws')
  })

  it('keeps a non-default port, so a reverse-proxied deploy resolves', () => {
    vi.stubGlobal('location', { protocol: 'https:', host: 'gm.example.com:8443' })

    expect(wsUrl()).toBe('wss://gm.example.com:8443/api/ws')
  })
})

describe('SECRET_KEYS', () => {
  it('lists exactly the settings fields that must never round-trip in cleartext', () => {
    // Pinned rather than spot-checked: a new secret-bearing setting added to
    // the settings form without being listed here would be rendered to the
    // operator unmasked, so the omission needs to fail a test.
    expect(SECRET_KEYS).toEqual(['llm_api_key', 'relay_api_key'])
  })
})
