/**
 * Keeps the panel current: a WebSocket for pushed events (with backoff) plus a
 * slow HTTP poll as the source of truth. Timers and the socket class are
 * injected so this runs under `node --test`.
 */
import { applyEvent, applyPoll } from "./panel-state.mjs";

export const wsUrl = (base) => `${String(base).replace(/\/+$/, "").replace(/^http/, "ws")}/api/ws`;

export const backoff = (attempt, { base = 1000, max = 10000 } = {}) => Math.min(base * 1.5 ** attempt, max);

export class EngineLink {
  constructor({ client, state, onChange, WebSocketImpl = globalThis.WebSocket, timers = globalThis, pollMs = 5000, log = console }) {
    Object.assign(this, { client, state, onChange, WebSocketImpl, timers, pollMs, log });
    this.socket = null;
    this.pollTimer = null;
    this.retryTimer = null;
    this.attempt = 0;
    this.stopped = true;
  }

  start() {
    this.stopped = false;
    this.connect();
    this.poll();
    this.pollTimer = this.timers.setInterval(() => this.poll(), this.pollMs);
  }

  stop() {
    this.stopped = true;
    this.timers.clearInterval(this.pollTimer);
    this.timers.clearTimeout(this.retryTimer);
    this.pollTimer = this.retryTimer = null;
    this.socket?.close(1000);
    this.socket = null;
  }

  /** Point at a new engine URL (settings change). */
  restart() {
    this.stop();
    this.start();
  }

  async poll() {
    const [status, game, events, combat] = await Promise.all([
      this.client.status(), this.client.state(), this.client.events(), this.client.combatStatus(),
    ]);
    this.onChange(applyPoll(this.state, { status, game, events, combat }));
  }

  connect() {
    if (this.stopped || !this.WebSocketImpl) return;
    const socket = new this.WebSocketImpl(wsUrl(this.client.base));
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.state.wsConnected = true;
      if (this.client.token) socket.send(JSON.stringify({ type: "auth", token: this.client.token }));
      this.onChange(["header"]);
    };
    socket.onclose = (evt) => {
      this.state.wsConnected = false;
      this.onChange(["header"]);
      // 1000/1001 are deliberate closes; anything else is worth retrying.
      if (this.stopped || evt.code === 1000 || evt.code === 1001) return;
      this.attempt += 1;
      this.retryTimer = this.timers.setTimeout(() => this.connect(), backoff(this.attempt));
    };
    socket.onerror = (err) => this.log.warn?.("[aigm-control-panel] WS error", err);
    socket.onmessage = (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch (e) {
        this.log.warn?.("[aigm-control-panel] WS parse error", e);
        return;
      }
      const { parts, poll } = applyEvent(this.state, msg);
      this.onChange(parts);
      if (poll) this.poll();
    };
  }
}
