/**
 * What each button does. Every dependency is injected (engine client, state,
 * notifications, dialogs), so this runs under `node --test` and the Foundry
 * side stays a thin shell.
 */
import { update } from "./panel-state.mjs";

/** Text appended to a narration so players hear where things are. */
export function formatSpatial(relationships, t) {
  if (!relationships?.length) return "";
  const lines = relationships.map((rel) => {
    const dir = t(rel.direction === "left" ? "AIGM.spatial.left" : rel.direction === "right" ? "AIGM.spatial.right" : "AIGM.spatial.ahead");
    const cover = rel.cover === "none" || !rel.cover ? "" : ` ${t("AIGM.spatial.cover", { kind: t(rel.cover === "half" ? "AIGM.spatial.half" : "AIGM.spatial.full") })}`;
    return `- ${rel.name}: ${rel.distance_ft} ft ${dir}${cover}`;
  });
  return `\n\n**${t("AIGM.spatial.title")}:**\n${lines.join("\n")}`;
}

export function createControls({ client, state, link, notify, t, dialogs, refresh, isOperator = () => true }) {
  const fail = (action, res) => notify.error(t("AIGM.notify.failed", { action: t(`AIGM.action.${action}`), error: res.error }));
  const mutate = (fn) => refresh(update(state, fn));

  async function camera(call) {
    const turn = await client.isPlayerTurn();
    if (turn.ok && turn.data?.player_turn) return notify.warn(t("AIGM.notify.playerTurn"));
    const res = await call();
    if (!res.ok) fail("camera", res);
  }

  return {
    async pause() {
      const res = await client.pause();
      if (!res.ok) return fail("pause", res);
      mutate(() => { state.aiRunning = false; });
      notify.info(t("AIGM.notify.paused"));
    },

    async resume() {
      const res = await client.resume();
      if (!res.ok) return fail("resume", res);
      mutate(() => { state.aiRunning = true; });
      notify.info(t("AIGM.notify.resumed"));
    },

    async startCombat() {
      const res = await client.startCombat();
      if (!res.ok) return fail("startCombat", res);
      notify.info(t("AIGM.notify.combatStarted"));
      link.poll();
    },

    async stopCombat() {
      const res = await client.stopCombat();
      if (!res.ok) return fail("stopCombat", res);
      notify.info(t("AIGM.notify.combatStopped"));
      link.poll();
    },

    async endSession() {
      if (!isOperator()) return notify.warn(t("AIGM.notify.operatorOnly"));
      const reason = await dialogs.askEndReason();
      if (!reason) return;
      const res = await client.endSession(reason);
      if (!res.ok) return fail("endSession", res);
      notify.info(t("AIGM.notify.sessionEnded"));
      link.poll();
    },

    async rollDice() {
      const input = await dialogs.askRoll();
      if (!input) return;
      const res = await client.roll(input.formula, input.speaker, input.flavor);
      if (!res.ok) return fail("roll", res);
      // The engine wraps the relay's reply; fall back progressively in case an
      // op returns it less wrapped.
      const roll = res.data?.data?.roll ?? res.data?.roll ?? res.data;
      notify.info(t("AIGM.notify.rolled", { formula: roll?.formula ?? input.formula, total: roll?.total ?? roll?.rollTotal ?? "ok" }));
    },

    /** @returns {Promise<boolean>} true when posted, so the caller can clear its textarea */
    async narrate(text, includeSpatial = false) {
      if (!text?.trim()) {
        notify.warn(t("AIGM.narrate.empty"));
        return false;
      }
      const spatial = includeSpatial ? formatSpatial(state.relationships, t) : "";
      const res = await client.narrate(text + spatial);
      if (!res.ok) {
        fail("narrate", res);
        return false;
      }
      notify.info(t("AIGM.narrate.sent"));
      return true;
    },

    async switchToScene(name) {
      const res = await client.switchScene(name);
      if (!res.ok) return fail("switchScene", res);
      notify.info(t("AIGM.notify.sceneSwitched", { scene: name }));
      link.poll();
    },

    async refreshStatus() {
      await link.poll();
      notify.info(t("AIGM.notify.refreshed"));
    },

    async testConnection() {
      const res = await client.status();
      if (res.ok) notify.info(t("AIGM.notify.connected", { url: client.base }));
      else notify.error(res.error);
    },

    async setMode(mode) {
      const res = await client.setState("mode", mode);
      if (!res.ok) return fail("setMode", res);
      mutate(() => { state.game = { ...state.game, mode }; });
      notify.info(t("AIGM.notify.modeSet", { mode }));
    },

    pan: () => camera(() => client.pan(0, 0, 500)),
    pushIn: () => camera(() => client.pushIn(0, 0, 500)),
    pullBack: () => camera(() => client.pullBack(500)),
    zoomIn: () => camera(() => client.zoom(1.5, 300)),
    zoomOut: () => camera(() => client.zoom(0.75, 300)),

    async loadNpcs() {
      const res = await client.npcs();
      state.npcs = res.ok ? res.data?.npcs ?? [] : [];
      refresh(["lists"]);
    },

    async loadScenes() {
      const res = await client.scenes();
      state.scenes = res.ok ? res.data?.scenes ?? [] : [];
      refresh(["lists"]);
    },

    async loadSpatial() {
      const res = await client.spatialRelationships();
      state.relationships = res.ok ? res.data?.relationships ?? [] : [];
      state.spatialError = res.ok ? "" : res.error;
      refresh(["spatial"]);
    },
  };
}
