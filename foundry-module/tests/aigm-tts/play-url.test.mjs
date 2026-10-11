import { test } from "node:test";
import assert from "node:assert/strict";

const hooks = {};
const registered = {};
const played = [];
const settings = new Map();
const settingDefs = {};
globalThis.window = { location: { href: "http://localhost:30000/game", origin: "http://localhost:30000" } };
globalThis.Hooks = { once: (name, fn) => { hooks[name] = fn; } };
globalThis.game = {
  modules: new Map([["aigm-tts", {}]]),
  users: { get: () => ({ isGM: true }) },
  settings: {
    register: (id, key, def) => { settingDefs[key] = def; settings.set(key, def.default); },
    get: (id, key) => settings.get(key),
  },
};
globalThis.foundry = { audio: { AudioHelper: { play: (o) => played.push(o.src) } } };
globalThis.socketlib = { registerModule: () => ({ register: (n, fn) => { registered[n] = fn; } }) };
await import("../../aigm-tts/scripts/aigm-tts.js");
hooks.init();
hooks["socketlib.ready"]();
const play = (url) => registered.playUrl.call({ socketdata: { userId: "gm" } }, { url });

test("engineOrigin defaults to the engine's port", () => {
  assert.equal(settingDefs.engineOrigin.default, "http://localhost:18080");
});

test("same-origin and the engine's absolute URL are played", () => {
  played.length = 0;
  play("/audio/a.wav");
  play("http://localhost:18080/audio/b.wav");
  assert.deepEqual(played, ["/audio/a.wav", "http://localhost:18080/audio/b.wav"]);
});

test("arbitrary remote origins are still refused", () => {
  played.length = 0;
  play("http://evil.example/a.wav");
  play("http://localhost:9999/a.wav");
  assert.deepEqual(played, []);
});

test("a malformed engineOrigin falls back to same-origin only", () => {
  settings.set("engineOrigin", "not a url");
  played.length = 0;
  play("http://localhost:18080/audio/b.wav");
  play("/audio/a.wav");
  assert.deepEqual(played, ["/audio/a.wav"]);
});
