import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useStore } from './store.js'
import { FakeWebSocket } from './test/fake-websocket.js'

// zustand's create() runs once per module, so the store is a singleton shared
// by every test in this file. Snapshot the pristine state (actions included —
// they live on the same object) and restore it wholesale before each test.
const pristine = { ...useStore.getState() }

const store = () => useStore.getState()

beforeEach(() => {
  useStore.setState(pristine, true)
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('connectWS', () => {
  it('opens a socket at the same-origin ws URL', () => {
    store().connectWS()

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.last.url).toBe('ws://localhost:3000/api/ws')
  })

  it('stores the socket immediately, before it opens', () => {
    store().connectWS()

    expect(store().ws).toBe(FakeWebSocket.last)
    expect(store().ws.readyState).toBe(FakeWebSocket.CONNECTING)
  })

  it('reports that it is connecting', () => {
    store().connectWS()

    expect(store().statusMessage).toBe('Connecting to AI engine…')
  })

  it('is a no-op when a socket is already open', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    store().connectWS()

    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('does open a second socket when the existing one is still connecting', () => {
    store().connectWS()
    store().connectWS()

    // The guard only short-circuits on OPEN, so a slow connect is replaced.
    expect(FakeWebSocket.instances).toHaveLength(2)
  })
})

describe('on open', () => {
  it('marks the connection live', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    expect(store().wsConnected).toBe(true)
  })

  it('sends no auth frame when no admin token is stored', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    expect(FakeWebSocket.last.sent).toEqual([])
  })

  it('authenticates in-band when an admin token is stored', () => {
    localStorage.setItem('aigm_admin_token', 'sekrit')

    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    // In-band rather than in the URL: a query-string token would land in
    // proxy and server access logs.
    expect(FakeWebSocket.last.sentJson).toEqual([{ type: 'auth', token: 'sekrit' }])
  })

  it('resets the backoff counter so the next outage starts at 3s again', () => {
    useStore.setState({ wsReconnectAttempt: 5 })

    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    expect(store().wsReconnectAttempt).toBe(0)
  })
})

describe('on close', () => {
  it('clears the live connection', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    FakeWebSocket.last.simulateClose(1006)

    expect(store().ws).toBeNull()
    expect(store().wsConnected).toBe(false)
  })

  it.each([
    ['a normal server shutdown', 1000],
    ['the tab navigating away', 1001],
  ])('does not reconnect after %s', (_label, code) => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    FakeWebSocket.last.simulateClose(code)
    vi.advanceTimersByTime(60_000)

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(store().wsReconnectAttempt).toBe(0)
  })

  it('reconnects after an abnormal close', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    FakeWebSocket.last.simulateClose(1006)
    vi.advanceTimersByTime(3000)

    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('counts the reconnect attempt', () => {
    store().connectWS()
    FakeWebSocket.last.simulateClose(1006)

    expect(store().wsReconnectAttempt).toBe(1)
  })

  it('still schedules a reconnect when a superseded socket closes', () => {
    store().connectWS()
    const stale = FakeWebSocket.last
    store().connectWS() // supersedes `stale` while it was still connecting
    const current = FakeWebSocket.last

    stale.simulateClose(1006)

    // The store's socket is untouched because the closing one is not current…
    expect(store().ws).toBe(current)
    // …but the reconnect fires regardless, which is how a superseded socket
    // can still drive the backoff counter.
    expect(store().wsReconnectAttempt).toBe(1)
  })
})

describe('reconnect backoff', () => {
  /** Drives one failed connect cycle and returns the ms waited before the
   *  next socket appeared. Asserts to the millisecond: one tick short must
   *  produce nothing, the exact tick must produce the socket. */
  function measureDelay(expected) {
    const before = FakeWebSocket.instances.length
    FakeWebSocket.last.simulateClose(1006)

    vi.advanceTimersByTime(expected - 1)
    expect(FakeWebSocket.instances).toHaveLength(before)

    vi.advanceTimersByTime(1)
    expect(FakeWebSocket.instances).toHaveLength(before + 1)
  }

  it('grows 3s, 4.5s, 6.75s across successive failures', () => {
    store().connectWS()

    measureDelay(3000)
    measureDelay(4500)
    measureDelay(6750)
  })

  it('caps the wait at 30s', () => {
    // 3000 * 1.5^6 is ~34s, past the ceiling.
    useStore.setState({ wsReconnectAttempt: 6 })
    store().connectWS()

    measureDelay(30_000)
  })

  it('announces the wait for the first three attempts', () => {
    store().connectWS()

    FakeWebSocket.last.simulateClose(1006)
    expect(store().statusMessage).toBe('Reconnecting in 3s…')

    vi.advanceTimersByTime(3000)
    FakeWebSocket.last.simulateClose(1006)
    expect(store().statusMessage).toBe('Reconnecting in 5s…')
  })

  it('stops announcing after the third attempt, to avoid a nagging banner', () => {
    useStore.setState({ wsReconnectAttempt: 3 })
    store().connectWS()
    useStore.setState({ statusMessage: 'untouched' })

    FakeWebSocket.last.simulateClose(1006)

    expect(store().wsReconnectAttempt).toBe(4)
    expect(store().statusMessage).toBe('untouched')
  })

  it('replaces a pending timer instead of stacking reconnects', () => {
    store().connectWS()
    const first = FakeWebSocket.last

    first.simulateClose(1006)
    const timerAfterFirst = store().wsReconnectTimer
    first.simulateClose(1006) // a duplicate close event for the same socket

    expect(store().wsReconnectTimer).not.toBe(timerAfterFirst)

    // Only one socket may appear, however many closes arrived.
    vi.advanceTimersByTime(60_000)
    expect(FakeWebSocket.instances).toHaveLength(2)
  })
})

describe('closeWS', () => {
  it('closes the underlying socket', () => {
    store().connectWS()
    const ws = FakeWebSocket.last

    store().closeWS()

    expect(ws.closeCalls).toBe(1)
  })

  it('resets the connection state', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    store().closeWS()

    expect(store().ws).toBeNull()
    expect(store().wsConnected).toBe(false)
    expect(store().wsReconnectAttempt).toBe(0)
  })

  it('cancels a pending reconnect so an unmounted panel stops dialling', () => {
    store().connectWS()
    FakeWebSocket.last.simulateClose(1006)
    expect(store().wsReconnectTimer).not.toBeNull()

    store().closeWS()
    vi.advanceTimersByTime(60_000)

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(store().wsReconnectTimer).toBeNull()
  })

  it('is safe to call when nothing is connected', () => {
    expect(() => store().closeWS()).not.toThrow()
    expect(store().ws).toBeNull()
  })
})

describe('sendWS', () => {
  it('sends a framed message on an open socket', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    store().sendWS('roll_command', { formula: '1d20' })

    expect(FakeWebSocket.last.sentJson).toEqual([
      { type: 'roll_command', formula: '1d20' },
    ])
  })

  it('defaults to an empty payload', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    store().sendWS('ping')

    expect(FakeWebSocket.last.sentJson).toEqual([{ type: 'ping' }])
  })

  it('drops the message when the socket is still connecting', () => {
    store().connectWS()

    store().sendWS('ping')

    expect(FakeWebSocket.last.sent).toEqual([])
  })

  it('does not throw when there is no socket at all', () => {
    expect(() => store().sendWS('ping')).not.toThrow()
  })
})

describe('on error', () => {
  it('is swallowed, leaving the close handler to drive reconnection', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    expect(() => FakeWebSocket.last.simulateError()).not.toThrow()

    // onerror deliberately does nothing: the browser always follows an error
    // with a close, and that is where the backoff lives. Reacting here too
    // would double-count the attempt.
    expect(store().wsConnected).toBe(true)
    expect(store().wsReconnectAttempt).toBe(0)
  })
})

describe('pauseAI / resumeAI', () => {
  it('sends pause over the socket and flips the flag', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()
    useStore.setState({ aiRunning: true })

    store().pauseAI()

    expect(FakeWebSocket.last.sentJson).toEqual([{ type: 'pause' }])
    expect(store().aiRunning).toBe(false)
  })

  it('sends resume over the socket and flips the flag', () => {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()

    store().resumeAI()

    expect(FakeWebSocket.last.sentJson).toEqual([{ type: 'resume' }])
    expect(store().aiRunning).toBe(true)
  })

  it('still updates the flag optimistically with no socket', () => {
    useStore.setState({ aiRunning: true })

    store().pauseAI()

    expect(store().aiRunning).toBe(false)
  })
})

describe('incoming messages', () => {
  function connected() {
    store().connectWS()
    FakeWebSocket.last.simulateOpen()
    return FakeWebSocket.last
  }

  it('handles ai_paused', () => {
    useStore.setState({ aiRunning: true })
    connected().simulateMessage({ type: 'ai_paused' })

    expect(store().aiRunning).toBe(false)
  })

  it('handles ai_resumed', () => {
    connected().simulateMessage({ type: 'ai_resumed' })

    expect(store().aiRunning).toBe(true)
  })

  it('records a started session', () => {
    connected().simulateMessage({
      type: 'session_started',
      session_id: 'abc123',
      campaign_name: 'Greenrest',
    })

    expect(store().campaignSession.activeSession).toEqual({
      session_id: 'abc123',
      campaign_name: 'Greenrest',
      status: 'started',
    })
  })

  it('updates the current scene', () => {
    useStore.setState({ gameState: { mode: 'exploration', current_scene: 'Old Scene' } })

    connected().simulateMessage({ type: 'scene_loaded', scene_name: 'The Mill' })

    expect(store().gameState.current_scene).toBe('The Mill')
    expect(store().gameState.mode).toBe('exploration')
  })

  it('enters combat', () => {
    useStore.setState({ gameState: { mode: 'exploration' } })

    connected().simulateMessage({
      type: 'combat_started',
      round: 2,
      pc_count: 4,
      npc_count: 3,
      turn_order: ['Ranger', 'Goblin'],
    })

    expect(store().gameState).toEqual({
      mode: 'combat',
      combat: { round: 2, pc_count: 4, npc_count: 3, turn_order: ['Ranger', 'Goblin'] },
    })
  })

  it('defaults a combat_started payload that omits its counts', () => {
    useStore.setState({ gameState: { mode: 'exploration' } })

    connected().simulateMessage({ type: 'combat_started' })

    expect(store().gameState.combat).toEqual({
      round: 1,
      pc_count: 0,
      npc_count: 0,
      turn_order: [],
    })
  })

  it('tracks the active turn without dropping the rest of the combat block', () => {
    useStore.setState({
      gameState: { mode: 'combat', combat: { round: 1, pc_count: 4 } },
    })

    connected().simulateMessage({
      type: 'turn_started',
      round: 1,
      turn: 2,
      actor: 'Goblin',
      is_npc: true,
    })

    expect(store().gameState.combat).toEqual({
      round: 1,
      pc_count: 4,
      current_round: 1,
      current_turn: 2,
      current_actor: 'Goblin',
      is_npc_turn: true,
    })
  })

  it('advances the round', () => {
    useStore.setState({ gameState: { mode: 'combat', combat: { round: 1 } } })

    connected().simulateMessage({ type: 'round_started', round: 2 })

    expect(store().gameState.combat.round).toBe(2)
  })

  it('flags a completed turn', () => {
    useStore.setState({ gameState: { mode: 'combat', combat: { round: 1 } } })

    connected().simulateMessage({ type: 'turn_complete' })

    expect(store().gameState.combat.last_turn_complete).toBe(true)
  })

  it('leaves combat', () => {
    useStore.setState({ gameState: { mode: 'combat', combat: { round: 5 } } })

    connected().simulateMessage({ type: 'combat_ended', rounds: 5 })

    expect(store().gameState.mode).toBe('exploration')
    expect(store().gameState.combat.round).toBe(5)
    expect(store().gameState.combat.ended_at).toEqual(expect.any(String))
  })

  it('records zero rounds when combat_ended omits the count', () => {
    useStore.setState({ gameState: { mode: 'combat', combat: { round: 5 } } })

    connected().simulateMessage({ type: 'combat_ended' })

    expect(store().gameState.combat.round).toBe(0)
  })

  it.each([
    'scene_loaded',
    'combat_started',
    'turn_started',
    'round_started',
    'turn_complete',
    'combat_ended',
  ])('ignores %s while no game state has loaded yet', (type) => {
    expect(store().gameState).toBeNull()

    connected().simulateMessage({ type })

    expect(store().gameState).toBeNull()
  })

  it('ignores an unrecognised message type', () => {
    const before = store().gameState
    connected().simulateMessage({ type: 'something_new_from_the_server' })

    expect(store().gameState).toBe(before)
  })

  it('survives malformed JSON without tearing down the socket', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const ws = connected()

    expect(() => ws.simulateMessage('{not json')).not.toThrow()

    expect(spy).toHaveBeenCalled()
    expect(store().wsConnected).toBe(true)
  })
})
