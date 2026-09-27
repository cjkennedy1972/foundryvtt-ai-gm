import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useAction } from './useAction.js'

const state = (r) => r.result.current[0]
const run = (r) => r.result.current[1]
const reset = (r) => r.result.current[2]
const patch = (r) => r.result.current[3]

describe('useAction', () => {
  it('starts idle', () => {
    const r = renderHook(() => useAction())

    expect(state(r)).toEqual({ loading: false, result: null, error: '' })
  })

  it('merges initialExtra into the starting state', () => {
    const r = renderHook(() => useAction({ confirming: false }))

    expect(state(r)).toEqual({ loading: false, result: null, error: '', confirming: false })
  })

  it('is loading while the action is in flight', async () => {
    let release
    const pending = new Promise((resolve) => { release = resolve })
    const r = renderHook(() => useAction())

    let call
    act(() => { call = run(r)(() => pending) })

    await waitFor(() => expect(state(r).loading).toBe(true))

    await act(async () => {
      release({ ok: true, data: 'done' })
      await call
    })

    expect(state(r).loading).toBe(false)
  })

  it('stores data from a successful action', async () => {
    const r = renderHook(() => useAction())

    await act(async () => { await run(r)(async () => ({ ok: true, data: { deleted: 3 } })) })

    expect(state(r)).toEqual({ loading: false, result: { deleted: 3 }, error: '' })
  })

  it("stores the action's error message on failure", async () => {
    const r = renderHook(() => useAction())

    await act(async () => { await run(r)(async () => ({ ok: false, error: 'Campaign is live' })) })

    expect(state(r)).toEqual({ loading: false, result: null, error: 'Campaign is live' })
  })

  it('falls back to a default message when the failure carries none', async () => {
    const r = renderHook(() => useAction())

    await act(async () => { await run(r)(async () => ({ ok: false })) })

    expect(state(r).error).toBe('Action failed')
  })

  it('honours a caller-supplied fallback message', async () => {
    const r = renderHook(() => useAction())

    await act(async () => {
      await run(r)(async () => ({ ok: false }), { fallbackError: 'Teardown failed' })
    })

    expect(state(r).error).toBe('Teardown failed')
  })

  it('returns the raw action result to the caller', async () => {
    const r = renderHook(() => useAction())
    const payload = { ok: true, data: 'passthrough' }

    let returned
    await act(async () => { returned = await run(r)(async () => payload) })

    expect(returned).toBe(payload)
  })

  it('clears a previous error when a retry starts', async () => {
    const r = renderHook(() => useAction())

    await act(async () => { await run(r)(async () => ({ ok: false, error: 'first failure' })) })
    expect(state(r).error).toBe('first failure')

    await act(async () => { await run(r)(async () => ({ ok: true, data: 'recovered' })) })

    expect(state(r)).toEqual({ loading: false, result: 'recovered', error: '' })
  })

  it('clears a previous result when a retry starts', async () => {
    const r = renderHook(() => useAction())

    await act(async () => { await run(r)(async () => ({ ok: true, data: 'stale' })) })
    await act(async () => { await run(r)(async () => ({ ok: false, error: 'now broken' })) })

    expect(state(r).result).toBeNull()
  })

  describe('reset', () => {
    it('returns the state to idle', async () => {
      const r = renderHook(() => useAction())

      await act(async () => { await run(r)(async () => ({ ok: false, error: 'boom' })) })
      act(() => { reset(r)() })

      expect(state(r)).toEqual({ loading: false, result: null, error: '' })
    })

    it('restores initialExtra', async () => {
      const r = renderHook(() => useAction({ confirming: false }))

      act(() => { patch(r)({ confirming: true }) })
      act(() => { reset(r)() })

      expect(state(r).confirming).toBe(false)
    })

    it('applies an override on top of initialExtra', () => {
      const r = renderHook(() => useAction({ confirming: false }))

      act(() => { reset(r)({ confirming: true }) })

      expect(state(r).confirming).toBe(true)
    })
  })

  describe('patch', () => {
    it('merges extra fields without disturbing the rest', async () => {
      const r = renderHook(() => useAction({ confirming: false }))

      await act(async () => { await run(r)(async () => ({ ok: true, data: 'kept' })) })
      act(() => { patch(r)({ confirming: true }) })

      expect(state(r)).toEqual({
        loading: false,
        result: 'kept',
        error: '',
        confirming: true,
      })
    })
  })

  it('keeps run/reset/patch referentially stable across renders', () => {
    const r = renderHook(() => useAction())
    const before = [run(r), reset(r), patch(r)]

    act(() => { patch(r)({ nudge: 1 }) })

    // Identity matters: these are passed to memoised children and into
    // useEffect dependency arrays at the call sites.
    expect([run(r), reset(r), patch(r)]).toEqual(before)
  })

  it('rejects to the caller, and stays loading, when the action itself throws', async () => {
    const r = renderHook(() => useAction())
    let caught = null

    // Caught inside act() deliberately: letting the rejection escape act()
    // unwinds it before React commits, leaving result.current stale and the
    // state assertion below meaningless.
    await act(async () => {
      try {
        await run(r)(async () => { throw new Error('unexpected') })
      } catch (e) {
        caught = e
      }
    })

    expect(caught?.message).toBe('unexpected')
    // `run` has no try/catch, so the second setState never happens and the
    // hook is stranded mid-flight — a spinner that never stops. Safe today
    // only because every call site passes a store action, and those always
    // resolve to {ok, ...} instead of throwing. Pinned so that if a caller
    // ever passes a raw throwing promise, this is a known shape and not a
    // mystery.
    expect(state(r)).toEqual({ loading: true, result: null, error: '' })
  })

  it('does not throw when an action resolves after unmount', async () => {
    const r = renderHook(() => useAction())
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    let release
    const pending = new Promise((resolve) => { release = resolve })
    let call
    act(() => { call = run(r)(() => pending) })
    r.unmount()

    await act(async () => {
      release({ ok: true, data: 'late' })
      await call
    })

    expect(spy).not.toHaveBeenCalled()
  })
})
