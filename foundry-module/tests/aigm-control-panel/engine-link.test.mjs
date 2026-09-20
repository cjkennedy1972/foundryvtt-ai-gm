import { test } from "node:test";
import assert from "node:assert/strict";
import { EngineLink, wsUrl, backoff } from "../../aigm-control-panel/scripts/engine-link.mjs";
import { createState } from "../../aigm-control-panel/scripts/panel-state.mjs";

const ok = (data) => ({ ok: true, data });

class FakeSocket {
  static last = null;
  constructor(url) { this.url = url; this.sent = []; this.closed = null; FakeSocket.last = this; }
  send(m) { this.sent.push(m); }
  close(code) { this.closed = code; }
}

function harness(token = null) {
  const timeouts = [];
  const intervals = [];
  const timers = {
    setTimeout: (fn, ms) => { timeouts.push({ fn, ms }); return timeouts.length; },
    clearTimeout: () => {},
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval: () => {},
  };
  const client = { base: "http://engine:1", token, status: async () => ok({ connected: true, ai_running: true }), state: async () => ok({ mode: "exploration" }), events: async () => ok([]), combatStatus: async () => ok(null) };
  const changes = [];
  const link = new EngineLink({ client, state: createState(), onChange: (p) => changes.push(p), WebSocketImpl: FakeSocket, timers, log: { warn() {} } });
  return { link, timeouts, intervals, changes };
}

test("wsUrl swaps the scheme and backoff grows to a ceiling", () => {
  assert.equal(wsUrl("http://localhost:18080/"), "ws://localhost:18080/api/ws");
  assert.equal(wsUrl("https://gm.example"), "wss://gm.example/api/ws");
  assert.deepEqual([1, 2, 30].map((n) => backoff(n)), [1500, 2250, 10000]);
});

test("start connects, polls on an interval, and authenticates with the token", async () => {
  const h = harness("tok");
  h.link.start();
  assert.equal(FakeSocket.last.url, "ws://engine:1/api/ws");
  assert.equal(h.intervals[0].ms, 5000);
  FakeSocket.last.onopen();
  assert.deepEqual(JSON.parse(FakeSocket.last.sent[0]), { type: "auth", token: "tok" });
  assert.equal(h.link.state.wsConnected, true);
  await h.link.poll();
  assert.equal(h.link.state.aiRunning, true);
});

test("pushed events update the state, and actions_executed triggers a poll", async () => {
  const h = harness();
  h.link.start();
  let polled = 0;
  h.link.poll = async () => { polled += 1; };
  FakeSocket.last.onmessage({ data: JSON.stringify({ type: "ai_paused" }) });
  assert.equal(h.link.state.aiRunning, false);
  FakeSocket.last.onmessage({ data: JSON.stringify({ type: "actions_executed" }) });
  FakeSocket.last.onmessage({ data: "not json" });   // must not throw
  assert.equal(polled, 1);
});

test("an abnormal close reconnects with backoff; a deliberate close and stop() do not", () => {
  const h = harness();
  h.link.start();
  FakeSocket.last.onclose({ code: 1006 });
  assert.equal(h.timeouts.length, 1);
  assert.equal(h.timeouts[0].ms, 1500);
  FakeSocket.last.onclose({ code: 1000 });
  assert.equal(h.timeouts.length, 1);
  h.link.stop();
  FakeSocket.last.onclose({ code: 1006 });
  assert.equal(h.timeouts.length, 1);
  assert.equal(FakeSocket.last.closed, 1000);
});
