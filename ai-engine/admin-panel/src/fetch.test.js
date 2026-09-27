import { beforeEach, describe, expect, it, vi } from 'vitest'

import { API_BASE } from './config.js'
import { apiFetch, safeFetch } from './fetch.js'

/**
 * Builds a Response-alike. `json` is a thunk so a test can make parsing throw,
 * which is the branch that separates "empty 204" from "HTML error page".
 */
function response({ ok = true, status = 200, statusText = 'OK', json }) {
  return {
    ok,
    status,
    statusText,
    json: json ?? (async () => ({})),
  }
}

function mockFetch(res) {
  const spy = vi.fn(async () => res)
  globalThis.fetch = spy
  return spy
}

describe('apiFetch', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('prefixes the path with API_BASE', async () => {
    const spy = mockFetch(response({ json: async () => ({ hello: 'world' }) }))

    await apiFetch('/status')

    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy.mock.calls[0][0]).toBe(`${API_BASE}/status`)
  })

  it('returns the parsed body on success', async () => {
    mockFetch(response({ json: async () => ({ hello: 'world' }) }))

    await expect(apiFetch('/status')).resolves.toEqual({
      ok: true,
      data: { hello: 'world' },
    })
  })

  it('sends JSON content-type by default', async () => {
    const spy = mockFetch(response({}))

    await apiFetch('/status')

    expect(spy.mock.calls[0][1].headers['Content-Type']).toBe('application/json')
  })

  it('lets a caller override a default header', async () => {
    const spy = mockFetch(response({}))

    await apiFetch('/status', { headers: { 'Content-Type': 'text/plain' } })

    expect(spy.mock.calls[0][1].headers['Content-Type']).toBe('text/plain')
  })

  it('serialises a plain-object body to JSON', async () => {
    const spy = mockFetch(response({}))

    await apiFetch('/downtime', { method: 'POST', body: { player: 'Ranger' } })

    expect(spy.mock.calls[0][1].body).toBe('{"player":"Ranger"}')
  })

  it('passes FormData through untouched so the browser sets the boundary', async () => {
    const spy = mockFetch(response({}))
    const form = new FormData()
    form.append('file', 'contents')

    await apiFetch('/upload', { method: 'POST', body: form })

    expect(spy.mock.calls[0][1].body).toBe(form)
  })

  it('leaves a string body alone', async () => {
    const spy = mockFetch(response({}))

    await apiFetch('/raw', { method: 'POST', body: 'already-encoded' })

    expect(spy.mock.calls[0][1].body).toBe('already-encoded')
  })

  describe('admin token', () => {
    it('is omitted when none is stored', async () => {
      const spy = mockFetch(response({}))

      await apiFetch('/status')

      expect(spy.mock.calls[0][1].headers.Authorization).toBeUndefined()
    })

    it('is sent as a bearer token when stored', async () => {
      localStorage.setItem('aigm_admin_token', 'sekrit')
      const spy = mockFetch(response({}))

      await apiFetch('/status')

      expect(spy.mock.calls[0][1].headers.Authorization).toBe('Bearer sekrit')
    })

    it('does not clobber an Authorization header the caller already set', async () => {
      localStorage.setItem('aigm_admin_token', 'sekrit')
      const spy = mockFetch(response({}))

      await apiFetch('/status', { headers: { Authorization: 'Bearer caller-supplied' } })

      expect(spy.mock.calls[0][1].headers.Authorization).toBe('Bearer caller-supplied')
    })
  })

  describe('error handling', () => {
    it("throws the server's `error` field", async () => {
      mockFetch(response({
        ok: false,
        status: 400,
        json: async () => ({ error: 'Campaign not found' }),
      }))

      await expect(apiFetch('/campaign/nope')).rejects.toThrow('Campaign not found')
    })

    it('falls back to the `message` field', async () => {
      mockFetch(response({
        ok: false,
        status: 400,
        json: async () => ({ message: 'Bad request' }),
      }))

      await expect(apiFetch('/x')).rejects.toThrow('Bad request')
    })

    it('prefers `error` over `message` when both are present', async () => {
      mockFetch(response({
        ok: false,
        status: 400,
        json: async () => ({ error: 'from error', message: 'from message' }),
      }))

      await expect(apiFetch('/x')).rejects.toThrow('from error')
    })

    it('falls back to the status code when the body carries neither', async () => {
      mockFetch(response({ ok: false, status: 500, json: async () => ({}) }))

      await expect(apiFetch('/x')).rejects.toThrow('Server error (500)')
    })

    it('reports status and statusText when a failure body is not JSON', async () => {
      mockFetch(response({
        ok: false,
        status: 502,
        statusText: 'Bad Gateway',
        json: async () => { throw new SyntaxError('Unexpected token <') },
      }))

      await expect(apiFetch('/x')).rejects.toThrow('Server error (502 Bad Gateway)')
    })

    it('treats an unparseable *successful* body as a null payload', async () => {
      mockFetch(response({
        ok: true,
        status: 204,
        json: async () => { throw new SyntaxError('Unexpected end of JSON input') },
      }))

      await expect(apiFetch('/x')).resolves.toEqual({ ok: true, data: null })
    })

    it('propagates a transport-level rejection', async () => {
      globalThis.fetch = vi.fn(async () => { throw new TypeError('Failed to fetch') })

      await expect(apiFetch('/x')).rejects.toThrow('Failed to fetch')
    })
  })
})

describe('safeFetch', () => {
  it('passes a success through unchanged', async () => {
    mockFetch(response({ json: async () => ({ hello: 'world' }) }))

    await expect(safeFetch('/status')).resolves.toEqual({
      ok: true,
      data: { hello: 'world' },
    })
  })

  it('converts a thrown error into {ok: false, error}', async () => {
    mockFetch(response({
      ok: false,
      status: 400,
      json: async () => ({ error: 'Campaign not found' }),
    }))

    await expect(safeFetch('/x')).resolves.toEqual({
      ok: false,
      error: 'Campaign not found',
    })
  })

  it('catches a transport failure rather than rejecting', async () => {
    globalThis.fetch = vi.fn(async () => { throw new TypeError('Failed to fetch') })

    await expect(safeFetch('/x')).resolves.toEqual({
      ok: false,
      error: 'Failed to fetch',
    })
  })
})
