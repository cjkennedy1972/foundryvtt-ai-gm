import { test } from "node:test";
import assert from "node:assert/strict";
import { createState, applyEvent, applyPoll, buildContext, update } from "../../aigm-control-panel/scripts/panel-state.mjs";

const ok = (data) => ({ ok: true, data });

test("pausing the AI touches the header and the controls, nothing else", () => {
  const s = createState();
  s.aiRunning = true;
  const { parts } = applyEvent(s, { type: "ai_paused" });
  assert.equal(s.aiRunning, false);
  assert.deepEqual(parts, ["header", "controls"]);
});

test("combat start and end move the mode and the combat block", () => {
  const s = createState();
  s.game = { mode: "exploration" };
  applyEvent(s, { type: "combat_started", round: 2 });
  assert.deepEqual([s.game.mode, s.combat.round], ["combat", 2]);
  applyEvent(s, { type: "turn_started", turn: 3, actor: "Akhviri" });
  assert.deepEqual([s.combat.currentTurn, s.combat.currentActor], [3, "Akhviri"]);
  applyEvent(s, { type: "combat_ended" });
  assert.deepEqual([s.game.mode, s.combat], ["exploration", null]);
});

test("round and turn events are ignored outside combat", () => {
  const s = createState();
  assert.deepEqual(applyEvent(s, { type: "round_started", round: 5 }).parts, []);
  assert.equal(s.combat, null);
});

test("actions_executed asks the caller to poll and changes nothing itself", () => {
  const s = createState();
  assert.deepEqual(applyEvent(s, { type: "actions_executed" }), { parts: [], poll: true });
});

test("a poll that changes nothing re-renders nothing", () => {
  const s = createState();
  const result = () => ({ status: ok({ connected: true, model: "m", ai_running: true }), game: ok({ campaign: "C", mode: "exploration" }), events: ok([]), combat: ok(null) });
  assert.ok(applyPoll(s, result()).length > 0);
  assert.deepEqual(applyPoll(s, result()), []);
});

test("an unreachable engine keeps the last known data and shows the error", () => {
  const s = createState();
  applyPoll(s, { status: ok({ connected: true, model: "m" }), game: ok({ campaign: "C" }), events: ok([]), combat: ok(null) });
  const parts = applyPoll(s, { status: { ok: false, error: "Cannot reach the engine" }, game: { ok: false }, events: { ok: false }, combat: { ok: false } });
  assert.equal(s.reachable, false);
  assert.equal(s.game.campaign, "C");
  assert.ok(parts.includes("header"));
  assert.equal(buildContext(s).error, "Cannot reach the engine");
});

test("update() reports only the parts a mutation changed", () => {
  const s = createState();
  assert.deepEqual(update(s, () => { s.aiRunning = true; }), ["header", "controls"]);
  assert.deepEqual(update(s, () => {}), []);
});

test("buildContext marks the active mode, orders events newest first and localises through t", () => {
  const s = createState();
  s.game = { mode: "social", campaign: "Dragonlance", current_scene: "Courtyard" };
  s.events = Array.from({ length: 10 }, (_, i) => ({ timestamp: `2026-09-20T14:0${i % 10}:00`, description: `e${i}` }));
  s.npcs = [];
  s.scenes = [{ name: "Courtyard" }, { name: "Keep" }];
  const ctx = buildContext(s, { operator: true, t: (k) => `<${k}>` });
  assert.deepEqual(ctx.modes.filter((m) => m.active).map((m) => m.id), ["social"]);
  assert.equal(ctx.events.length, 8);
  assert.equal(ctx.events[0].text, "e9");
  assert.equal(ctx.badges[0].label, "<AIGM.badge.foundry>");
  assert.deepEqual(ctx.scenes.items.map((x) => [x.name, x.current]), [["Courtyard", true], ["Keep", false]]);
  assert.deepEqual([ctx.npcs.loaded, ctx.npcs.items.length], [true, 0]);
});

test("spatial tokens are capped, given icons, and unloaded state is distinct from empty", () => {
  const s = createState();
  assert.equal(buildContext(s).spatial.loaded, false);
  s.relationships = Array.from({ length: 12 }, (_, i) => ({ id: `t${i}`, name: `N${i}`, disposition: i === 0 ? 1 : -1, distance_ft: 5 * i, cover: i === 1 ? "half" : "none" }));
  const { spatial } = buildContext(s);
  assert.equal(spatial.tokens.length, 8);
  assert.deepEqual([spatial.tokens[0].icon, spatial.tokens[1].icon, spatial.tokens[1].cover], ["👤", "👹", "◐"]);
});
