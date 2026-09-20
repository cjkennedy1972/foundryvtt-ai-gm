import { test } from "node:test";
import assert from "node:assert/strict";
import { createControls, formatSpatial } from "../../aigm-control-panel/scripts/controls.mjs";
import { createState } from "../../aigm-control-panel/scripts/panel-state.mjs";

const t = (key, data) => (data ? `${key}${JSON.stringify(data)}` : key);
const res = (ok, extra = {}) => (ok ? { ok: true, data: extra } : { ok: false, error: "boom" });

function setup(clientOverrides = {}, deps = {}) {
  const log = { info: [], warn: [], error: [], refresh: [], polls: 0, calls: [] };
  const record = (name, result) => async (...args) => { log.calls.push([name, ...args]); return result; };
  const client = {
    pause: record("pause", res(true)), resume: record("resume", res(true)), narrate: record("narrate", res(true)),
    endSession: record("endSession", res(true)), roll: record("roll", res(true, { data: { roll: { formula: "2d6", total: 9 } } })),
    isPlayerTurn: async () => res(true, { player_turn: false }), pan: record("pan", res(true)), zoom: record("zoom", res(true)),
    ...clientOverrides,
  };
  const state = createState();
  const controls = createControls({
    client, state, t,
    link: { poll: async () => { log.polls += 1; } },
    notify: { info: (m) => log.info.push(m), warn: (m) => log.warn.push(m), error: (m) => log.error.push(m) },
    dialogs: { askEndReason: async () => "done", askRoll: async () => ({ formula: "2d6", speaker: "GM", flavor: "" }) },
    refresh: (parts) => log.refresh.push(parts),
    ...deps,
  });
  return { controls, state, log };
}

test("pause updates the state, re-renders header and controls, and says so", async () => {
  const { controls, state, log } = setup();
  state.aiRunning = true;
  await controls.pause();
  assert.equal(state.aiRunning, false);
  assert.deepEqual(log.refresh, [["header", "controls"]]);
  assert.deepEqual(log.info, ["AIGM.notify.paused"]);
});

test("a failed pause reports the action and the engine's error and leaves the state alone", async () => {
  const { controls, state, log } = setup({ pause: async () => res(false) });
  state.aiRunning = true;
  await controls.pause();
  assert.equal(state.aiRunning, true);
  assert.match(log.error[0], /AIGM\.notify\.failed.*AIGM\.action\.pause.*boom/);
});

test("the camera refuses to move during a player's turn", async () => {
  const { controls, log } = setup({ isPlayerTurn: async () => res(true, { player_turn: true }) });
  await controls.pan();
  assert.deepEqual(log.warn, ["AIGM.notify.playerTurn"]);
  assert.equal(log.calls.filter((c) => c[0] === "pan").length, 0);
});

test("the camera moves when it is not a player's turn, and a camera failure is reported", async () => {
  const ok = setup();
  await ok.controls.zoomIn();
  assert.deepEqual(ok.log.calls.find((c) => c[0] === "zoom"), ["zoom", 1.5, 300]);
  const bad = setup({ zoom: async () => res(false) });
  await bad.controls.zoomOut();
  assert.match(bad.log.error[0], /AIGM\.action\.camera/);
});

test("narrate refuses empty text and reports whether it posted", async () => {
  const { controls, log } = setup();
  assert.equal(await controls.narrate("   "), false);
  assert.deepEqual(log.warn, ["AIGM.narrate.empty"]);
  assert.equal(await controls.narrate("The wind rises."), true);
  assert.deepEqual(log.calls.find((c) => c[0] === "narrate"), ["narrate", "The wind rises."]);
  const failing = setup({ narrate: async () => res(false) });
  assert.equal(await failing.controls.narrate("x"), false);
});

test("narrate appends spatial context only when asked", async () => {
  const { controls, state, log } = setup();
  state.relationships = [{ name: "Akhviri", distance_ft: 30, direction: "left", cover: "half" }];
  await controls.narrate("Look out.", false);
  await controls.narrate("Look out.", true);
  const [plain, withSpatial] = log.calls.filter((c) => c[0] === "narrate").map((c) => c[1]);
  assert.equal(plain, "Look out.");
  assert.match(withSpatial, /Akhviri: 30 ft AIGM\.spatial\.left/);
});

test("formatSpatial words direction and cover, and is empty for no tokens", () => {
  assert.equal(formatSpatial([], t), "");
  const text = formatSpatial([{ name: "Goblin", distance_ft: 10, direction: "right", cover: "none" }, { name: "Ogre", distance_ft: 20, cover: "full" }], t);
  assert.match(text, /- Goblin: 10 ft AIGM\.spatial\.right\n/);
  assert.match(text, /- Ogre: 20 ft AIGM\.spatial\.ahead AIGM\.spatial\.cover/);
});

test("ending a session needs the operator and a reason; cancelling does nothing", async () => {
  const denied = setup({}, { isOperator: () => false });
  await denied.controls.endSession();
  assert.deepEqual(denied.log.warn, ["AIGM.notify.operatorOnly"]);
  const cancelled = setup({}, { dialogs: { askEndReason: async () => null, askRoll: async () => null } });
  await cancelled.controls.endSession();
  assert.equal(cancelled.log.calls.filter((c) => c[0] === "endSession").length, 0);
  const ok = setup();
  await ok.controls.endSession();
  assert.deepEqual(ok.log.calls.find((c) => c[0] === "endSession"), ["endSession", "done"]);
  assert.equal(ok.log.polls, 1);
});

test("a roll reports the total from the engine's wrapped reply", async () => {
  const { controls, log } = setup();
  await controls.rollDice();
  assert.match(log.info[0], /AIGM\.notify\.rolled.*"total":9/);
});
