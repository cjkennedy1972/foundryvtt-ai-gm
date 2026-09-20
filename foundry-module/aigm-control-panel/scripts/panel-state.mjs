/**
 * Panel state, the reducer for engine events, and the view-model the templates
 * render. Pure ES module (no Foundry globals) so it runs under `node --test`.
 */

/** Render parts, in window order. */
export const PARTS = ["header", "status", "controls", "spatial", "narration", "lists", "events"];

export function createState() {
  return {
    status: null,         // GET /api/status
    game: null,           // GET /api/state
    combat: null,
    events: [],
    aiRunning: false,
    wsConnected: false,
    reachable: null,      // null until the first poll answers
    error: "",
    relationships: null,  // null = not loaded yet
    spatialError: "",
    npcs: null,
    scenes: null,
  };
}

// What each part renders from. A poll re-renders a part only when its slice
// changed, so the 5s poll never rebuilds the DOM (or steals focus) needlessly.
const SLICES = {
  header: (s) => [s.status?.connected, s.status?.relay?.running, s.wsConnected, s.aiRunning, s.error],
  status: (s) => [s.status?.model, s.status?.conversation_length, s.game?.campaign, s.game?.mode, s.game?.current_scene],
  controls: (s) => [s.aiRunning, s.game?.mode, s.combat],
  spatial: (s) => [s.relationships, s.spatialError],
  lists: (s) => [s.npcs, s.scenes, s.game?.current_scene],
  events: (s) => s.events,
};

const snapshot = (state) => Object.fromEntries(Object.entries(SLICES).map(([part, slice]) => [part, JSON.stringify(slice(state))]));

/** Run `mutate` on the state and return the parts whose inputs changed. */
export function update(state, mutate) {
  const before = snapshot(state);
  mutate();
  const after = snapshot(state);
  return PARTS.filter((part) => before[part] !== undefined && before[part] !== after[part]);
}

/** Apply one engine WebSocket message. `poll` asks the caller to refresh from HTTP. */
export function applyEvent(state, msg) {
  let poll = false;
  const parts = update(state, () => {
    switch (msg?.type) {
      case "ai_paused": state.aiRunning = false; break;
      case "ai_resumed": state.aiRunning = true; break;
      case "session_started": state.game = { ...state.game, session: msg.session_id }; break;
      case "combat_started":
        state.game = { ...state.game, mode: "combat" };
        state.combat = { round: msg.round || 1, ...state.combat };
        break;
      case "combat_ended":
        state.game = { ...state.game, mode: "exploration" };
        state.combat = null;
        break;
      case "round_started": if (state.combat) state.combat = { ...state.combat, round: msg.round }; break;
      case "turn_started": if (state.combat) state.combat = { ...state.combat, currentTurn: msg.turn, currentActor: msg.actor }; break;
      case "turn_complete": if (state.combat) state.combat = { ...state.combat, lastTurnComplete: true }; break;
      case "actions_executed": poll = true; break;
      case "scene_loaded": if (state.game) state.game = { ...state.game, current_scene: msg.scene_name }; break;
    }
  });
  return { parts, poll };
}

/** Fold one round of HTTP results into the state; each is an EngineClient result. */
export function applyPoll(state, { status, game, events, combat }) {
  return update(state, () => {
    state.reachable = status.ok;
    state.error = status.ok ? "" : status.error;
    if (status.ok) {
      state.status = status.data;
      state.aiRunning = status.data?.ai_running || false;
    }
    if (game?.ok) state.game = game.data;
    if (events?.ok) state.events = events.data || [];
    if (combat?.ok) state.combat = combat.data;
  });
}

const DISPOSITION_ICON = (d) => (d >= 1 ? "👤" : d === 0 ? "◆" : "👹");
const COVER_ICON = (c) => (c === "none" ? "◼" : c === "half" ? "◐" : "●");

/**
 * The view-model for the templates. `t(key, data?)` localises; text is passed
 * through Handlebars, which escapes it, so actor and scene names need no
 * manual escaping.
 */
export function buildContext(state, { operator = false, t = (key) => key } = {}) {
  const { status, game, combat } = state;
  const mode = game?.mode || "exploration";
  const on = (flag, onIcon, offIcon) => (flag ? onIcon : offIcon);
  return {
    operator,
    error: state.error,
    badges: [
      { cls: status?.connected ? "aigm-connected" : "aigm-disconnected", icon: on(status?.connected, "🔗", "❌"), label: t("AIGM.badge.foundry") },
      { cls: status?.relay?.running ? "aigm-relay" : "aigm-relay-off", icon: on(status?.relay?.running, "📡", "⏸"), label: t("AIGM.badge.relay") },
      { cls: state.wsConnected ? "aigm-ws" : "aigm-ws-off", icon: on(state.wsConnected, "🔌", "⚫"), label: t("AIGM.badge.ws") },
      { cls: state.aiRunning ? "aigm-ai" : "aigm-ai-paused", icon: on(state.aiRunning, "🤖", "⏸"), label: t(state.aiRunning ? "AIGM.badge.active" : "AIGM.badge.paused") },
    ],
    stats: [
      { label: t("AIGM.stat.model"), value: status?.model || t("AIGM.stat.unknown") },
      { label: t("AIGM.stat.campaign"), value: game?.campaign || t("AIGM.stat.none") },
      { label: t("AIGM.stat.mode"), value: mode },
      { label: t("AIGM.stat.scene"), value: game?.current_scene || t("AIGM.stat.none") },
      { label: t("AIGM.stat.context"), value: t("AIGM.stat.messages", { count: status?.conversation_length || 0 }) },
    ],
    aiRunning: state.aiRunning,
    modes: ["exploration", "social", "combat"].map((id) => ({ id, label: t(`AIGM.mode.${id}`), active: mode === id })),
    combat: combat ? { round: combat.round, turn: combat.currentTurn, actor: combat.currentActor, hasTurn: combat.currentTurn !== undefined } : null,
    spatial: {
      loaded: state.relationships !== null,
      error: state.spatialError,
      tokens: (state.relationships ?? []).slice(0, 8).map((r) => ({
        id: r.id, name: r.name, icon: DISPOSITION_ICON(r.disposition), distance: r.distance_ft, cover: COVER_ICON(r.cover),
      })),
    },
    npcs: { loaded: state.npcs !== null, items: (state.npcs ?? []).slice(0, 20).map((n) => ({ id: n.id, name: n.name, type: n.type })) },
    scenes: { loaded: state.scenes !== null, items: (state.scenes ?? []).map((s) => ({ name: s.name, current: s.name === game?.current_scene })) },
    events: (state.events ?? []).slice(-8).reverse().map((e) => ({ time: e.timestamp ? e.timestamp.slice(11, 19) : "", text: e.description })),
  };
}
