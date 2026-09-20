/**
 * Structural guards. These exist because the module this replaced rotted
 * silently: buttons without handlers, a launcher on a dead API, and V1
 * Application code that v16 removes. Each is cheap to check statically.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";

const root = new URL("../../aigm-control-panel/", import.meta.url).pathname;
const read = (p) => readFileSync(join(root, p), "utf8");
const scripts = readdirSync(join(root, "scripts")).map((f) => [f, read(`scripts/${f}`)]);
const templates = readdirSync(join(root, "templates")).map((f) => [f, read(`templates/${f}`)]);
const lang = JSON.parse(read("lang/en.json"));

test("the manifest targets v14 and points at files that exist", () => {
  const m = JSON.parse(read("module.json"));
  assert.equal(m.id, "aigm-control-panel");
  assert.ok(Number(m.compatibility.minimum) >= 14);
  for (const f of [...m.esmodules, ...m.styles, ...m.languages.map((l) => l.path)]) assert.ok(existsSync(join(root, f)), f);
  assert.equal(m.relationships?.requires, undefined, "no dependency on socketlib or anything else");
});

test("every data-action in a template has a handler in panel.mjs", () => {
  const panel = read("scripts/panel.mjs");
  const listed = [...panel.match(/SIMPLE_ACTIONS = \[([^\]]+)\]/s)[1].matchAll(/"(\w+)"/g)].map((m) => m[1]);
  const explicit = [...panel.matchAll(/^\s{6}(\w+)\(.*?\) \{/gm)].map((m) => m[1]);
  const handlers = new Set([...listed, ...explicit]);
  const used = new Set(templates.flatMap(([, src]) => [...src.matchAll(/data-action="(\w+)"/g)].map((m) => m[1])));
  assert.ok(used.size > 10);
  for (const action of used) assert.ok(handlers.has(action), `no handler for data-action="${action}"`);
});

test("every simple action names a real controls method", () => {
  const panel = read("scripts/panel.mjs");
  const controls = read("scripts/controls.mjs");
  for (const name of [...panel.match(/SIMPLE_ACTIONS = \[([^\]]+)\]/s)[1].matchAll(/"(\w+)"/g)].map((m) => m[1])) {
    assert.match(controls, new RegExp(`\\b(async )?${name}\\(|\\b${name}:`), `controls.${name} missing`);
  }
});

test("every panel part has a template", () => {
  const parts = [...read("scripts/panel.mjs").matchAll(/\["header"[^\]]+\]/g)][0][0].match(/"(\w+)"/g).map((s) => s.replaceAll('"', ""));
  for (const p of parts) assert.ok(existsSync(join(root, `templates/${p}.hbs`)), p);
});

test("every template renders exactly one root element (ApplicationV2 rejects a part with several)", () => {
  const VOID = new Set(["input", "br", "img", "hr"]);
  for (const [name, src] of templates) {
    let depth = 0;
    let roots = 0;
    for (const [, close, tag, selfClosed] of src.matchAll(/<(\/?)([a-zA-Z0-9]+)[^>]*?(\/?)>/g)) {
      if (VOID.has(tag) || selfClosed) { if (!depth) roots++; continue; }
      if (close) depth--;
      else { if (!depth) roots++; depth++; }
    }
    assert.equal(roots, 1, `${name} has ${roots} root elements`);
  }
});

test("every localisation key used exists in en.json", () => {
  const has = (key) => key.split(".").reduce((o, k) => o?.[k], lang) !== undefined;
  const keys = new Set();
  for (const [, src] of [...scripts, ...templates]) for (const m of src.matchAll(/["'](AIGM\.[\w.]+)["']/g)) keys.add(m[1]);
  for (const [, src] of templates) for (const m of src.matchAll(/localize ['"](AIGM\.[\w.]+)['"]/g)) keys.add(m[1]);
  for (const key of keys) {
    // dynamic keys like AIGM.mode.${id} are covered by their siblings
    if (!key.includes("${")) assert.ok(has(key), `missing lang key ${key}`);
  }
  for (const id of ["exploration", "social", "combat"]) assert.ok(has(`AIGM.mode.${id}`));
  for (const a of ["pause", "resume", "startCombat", "stopCombat", "endSession", "roll", "narrate", "switchScene", "setMode", "camera", "test"]) assert.ok(has(`AIGM.action.${a}`), a);
});

test("no legacy Application, jQuery or pre-v13 scene-control API (v16 gate)", () => {
  const banned = [/extends\s+Application\b/, /activateListeners/, /\$\(/, /\bjQuery\b/, /onClick\s*:/, /renderChatMessage\b/, /foundry\.appv1/, /new Dialog\(/, /\bsocketlib\b/];
  for (const [file, src] of scripts) for (const re of banned) assert.doesNotMatch(src, re, `${file} matches ${re}`);
});
