import { test } from "node:test";
import assert from "node:assert/strict";
import { characterFields, biographyPath, appendBackstory } from "../../aigm-control-panel/scripts/backstory.mjs";

test("characterFields reads name, ancestry and class off the sheet, tolerating a bare actor", () => {
  assert.deepEqual(characterFields({ name: "Elara", itemTypes: { race: [{ name: "Elf" }], class: [{ name: "Wizard" }, { name: "Rogue" }] } }),
    { name: "Elara", ancestry: "Elf", char_class: "Wizard / Rogue" });
  assert.deepEqual(characterFields({ name: "Rook" }), { name: "Rook", ancestry: "", char_class: "" });
});

test("biographyPath prefers dnd5e's field and returns null when the system has none", () => {
  assert.equal(biographyPath({ system: { details: { biography: { value: "" } } } }), "system.details.biography.value");
  assert.equal(biographyPath({ system: { biography: "<p>x</p>" } }), "system.biography");
  assert.equal(biographyPath({ system: { hp: 3 } }), null);
});

test("appendBackstory keeps the old text, escapes the new, and splits paragraphs", () => {
  const out = appendBackstory("<p>Old</p>", "One <b>.\n\nTwo\nlines", "1/2/2026");
  assert.equal(out, "<p>Old</p><hr><p><em>1/2/2026</em></p><p>One &lt;b&gt;.</p><p>Two<br>lines</p>");
  assert.equal(appendBackstory(undefined, "x", "d"), "<hr><p><em>d</em></p><p>x</p>");
});
