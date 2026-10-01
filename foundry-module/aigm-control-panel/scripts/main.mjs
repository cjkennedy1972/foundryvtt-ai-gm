/** Module entry: settings, the scene-controls launcher, keybinding, and wiring. */
import { EngineClient } from "./engine-client.mjs";
import { EngineLink } from "./engine-link.mjs";
import { createControls } from "./controls.mjs";
import { createDialogs } from "./dialogs.mjs";
import { createState } from "./panel-state.mjs";
import { AIGMControlPanel } from "./panel.mjs";

const MODULE_ID = "aigm-control-panel";

const t = (key, data) => (data ? game.i18n.format(key, data) : game.i18n.localize(key));
const setting = (key) => game.settings.get(MODULE_ID, key);

/** The operator is a GM, or a player who was given the engine's admin token. */
const isOperator = () => game.user.isGM || !!setting("adminToken");

const state = createState();
const client = new EngineClient({ origin: window.location.origin });
let panel = null;
let link = null;

const notify = {
  info: (msg) => ui.notifications.info(msg),
  warn: (msg) => ui.notifications.warn(msg),
  error: (msg) => ui.notifications.error(msg),
};

/** Built on first use. Non-operators get the same controls (they only use the sheet button), just no link. */
function getControls() {
  if (!api.controls) {
    api.controls = createControls({
      client, state, link, notify, t, isOperator,
      dialogs: createDialogs({ t, speakers: () => game.actors.filter((a) => a.hasPlayerOwner && a.type === "character").map((a) => a.name) }),
      refresh: (parts) => panel?.refresh(parts),
    });
  }
  return api.controls;
}

function open() {
  if (!panel) panel = new AIGMControlPanel({ state, controls: getControls(), isOperator, t });
  return panel.render({ force: true });
}

const toggle = () => (panel?.rendered ? panel.close() : open());

function configureClient() {
  client.baseUrl = setting("engineUrl") || "http://localhost:18080";
  client.token = setting("adminToken") || null;
}

Hooks.once("init", () => {
  const onChange = () => {
    configureClient();
    link?.restart();
  };
  game.settings.register(MODULE_ID, "engineUrl", {
    name: "AIGM.settings.engineUrl.name",
    hint: "AIGM.settings.engineUrl.hint",
    scope: "client", // relative to each browser: another machine cannot reach this one's localhost
    config: true,
    type: String,
    default: "http://localhost:18080",
    onChange,
  });
  game.settings.register(MODULE_ID, "adminToken", {
    name: "AIGM.settings.adminToken.name",
    hint: "AIGM.settings.adminToken.hint",
    // client, not world: world settings replicate to every connected client,
    // which would hand the admin token to every player.
    scope: "client",
    config: true,
    type: String,
    default: "",
    onChange,
  });
  game.keybindings.register(MODULE_ID, "toggle", {
    name: "AIGM.keybinding",
    editable: [{ key: "KeyG", modifiers: ["Control", "Shift"] }],
    onDown: () => { if (isOperator()) toggle(); return true; },
  });
});

// v14 scene-control tools take `order` and `onChange`; `onClick` (pre-v13) is ignored.
Hooks.on("getSceneControlButtons", (controls) => {
  const tokens = controls.tokens;
  if (!tokens) return;
  tokens.tools[MODULE_ID] = {
    name: MODULE_ID,
    title: "AIGM.title",
    icon: "fa-solid fa-robot",
    order: Object.keys(tokens.tools).length,
    button: true,
    visible: isOperator(),
    onChange: () => toggle(),
  };
});

const api = { state, client, open, close: () => panel?.close(), toggle, controls: null };

// A "Backstory" entry in the header menu of every character sheet its owner can edit.
Hooks.on("getHeaderControlsApplicationV2", (app, controls) => {
  const actor = app.document;
  if (actor?.documentName !== "Actor" || actor.type !== "character" || !actor.isOwner) return;
  controls.push({
    action: "aigmBackstory",
    icon: "fa-solid fa-book-open",
    label: "AIGM.backstory.menu",
    onClick: () => getControls().writeBackstory(actor),
  });
});

Hooks.once("ready", () => {
  configureClient();
  game.modules.get(MODULE_ID).api = api;
  if (!isOperator()) return;
  link = new EngineLink({ client, state, onChange: (parts) => panel?.refresh(parts) });
  link.start();
});
