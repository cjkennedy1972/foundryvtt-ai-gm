import { test } from "node:test";
import assert from "node:assert/strict";
import { EngineClient } from "../../aigm-control-panel/scripts/engine-client.mjs";

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

function client(handler, extra = {}) {
  const calls = [];
  const c = new EngineClient({ baseUrl: "http://engine:1/", origin: "http://foundry:30000", fetchFn: async (url, init) => { calls.push({ url, init }); return handler(url, init); }, ...extra });
  return { c, calls };
}

test("a GET sends no Content-Type, a POST body is JSON, and the trailing slash is trimmed", async () => {
  const { c, calls } = client(() => json(200, { ok: 1 }));
  await c.status();
  await c.roll("1d20", "GM", "x");
  assert.equal(calls[0].url, "http://engine:1/api/status");
  assert.equal(calls[0].init.headers["Content-Type"], undefined);
  assert.equal(calls[1].init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[1].init.body), { formula: "1d20", speaker: "GM", flavor: "x" });
});

test("the admin token is sent as a bearer token only when set", async () => {
  const a = client(() => json(200, {}));
  await a.c.status();
  assert.equal(a.calls[0].init.headers.Authorization, undefined);
  const b = client(() => json(200, {}), { token: "s3cret" });
  await b.c.status();
  assert.equal(b.calls[0].init.headers.Authorization, "Bearer s3cret");
});

test("a network failure names the CORS fix with this Foundry's origin", async () => {
  const { c } = client(() => { throw new TypeError("Failed to fetch"); });
  const res = await c.status();
  assert.equal(res.ok, false);
  assert.equal(res.kind, "network");
  assert.match(res.error, /CORS_ORIGINS/);
  assert.match(res.error, /http:\/\/foundry:30000/);
});

test("an HTTP error surfaces the engine's own message, with fallbacks", async () => {
  assert.equal((await client(() => json(503, { error: "Not connected to Foundry" })).c.status()).error, "Not connected to Foundry");
  assert.equal((await client(() => json(400, { detail: "bad" })).c.status()).error, "bad");
  const res = await client(() => ({ ok: false, status: 500, json: async () => { throw new Error("not json"); } })).c.status();
  assert.deepEqual([res.kind, res.status, res.error], ["http", 500, "HTTP 500"]);
});

test("scene names are URL-encoded", async () => {
  const { c, calls } = client(() => json(200, {}));
  await c.switchScene("Bastion of Takhisis — Courtyard & more");
  assert.equal(calls[0].url, "http://engine:1/api/scene/switch?scene_name=Bastion%20of%20Takhisis%20%E2%80%94%20Courtyard%20%26%20more");
});
