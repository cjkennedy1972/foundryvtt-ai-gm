/**
 * A WebSocket stand-in the tests drive by hand.
 *
 * store.js reads the global `WebSocket.OPEN` constant and assigns
 * `onopen`/`onclose`/`onmessage`, so a double has to carry the same static
 * readyState constants as the real thing. Nothing here connects to anything:
 * the socket only changes state when a test calls one of the `simulate*`
 * helpers, which is what makes the reconnect assertions deterministic.
 */
export class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  /** Every socket constructed since the last reset, oldest first. */
  static instances = []

  static reset() {
    FakeWebSocket.instances = []
  }

  /** The most recently constructed socket. */
  static get last() {
    return FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
  }

  constructor(url) {
    this.url = url
    this.readyState = FakeWebSocket.CONNECTING
    /** Raw strings passed to send(), in order. */
    this.sent = []
    this.closeCalls = 0
    this.onopen = null
    this.onclose = null
    this.onerror = null
    this.onmessage = null
    FakeWebSocket.instances.push(this)
  }

  send(payload) {
    this.sent.push(payload)
  }

  /**
   * Mirrors the browser: close() only requests the close. The `onclose`
   * callback is delivered separately, so a test can assert on teardown that
   * happens before any close event arrives.
   */
  close() {
    this.closeCalls += 1
    this.readyState = FakeWebSocket.CLOSED
  }

  // ── test drivers ────────────────────────────────────────────────────────

  simulateOpen() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.({ type: 'open' })
  }

  simulateClose(code = 1006) {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code })
  }

  simulateError() {
    this.onerror?.({ type: 'error' })
  }

  /** Objects are JSON-encoded; strings are delivered verbatim so a test can
   *  feed malformed JSON. */
  simulateMessage(data) {
    const encoded = typeof data === 'string' ? data : JSON.stringify(data)
    this.onmessage?.({ data: encoded })
  }

  /** Parsed JSON of everything sent, for assertions on frame contents. */
  get sentJson() {
    return this.sent.map((s) => JSON.parse(s))
  }
}
