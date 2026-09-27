import { afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

import { FakeWebSocket } from './fake-websocket.js'

// jsdom ships a real WebSocket that would try to open a connection. Swap in
// the double globally so no test can accidentally hit the network, and so
// `WebSocket.OPEN` resolves to the double's constant in store.js.
globalThis.WebSocket = FakeWebSocket

beforeEach(() => {
  FakeWebSocket.reset()
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
})
