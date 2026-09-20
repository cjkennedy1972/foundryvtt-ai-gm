/**
 * HTTP client for the AI GM engine. Pure ES module: `fetch` is injected so it
 * runs under `node --test` without Foundry.
 */
export class EngineClient {
  constructor({ baseUrl = "http://localhost:18080", token = null, origin = "", fetchFn = (...a) => globalThis.fetch(...a) } = {}) {
    this.baseUrl = baseUrl;
    this.token = token;
    this.origin = origin;
    this.fetchFn = fetchFn;
  }

  get base() {
    return String(this.baseUrl).replace(/\/+$/, "");
  }

  /** @returns {Promise<{ok:true,data:any}|{ok:false,kind:"network"|"http",error:string,status?:number}>} */
  async request(path, { method = "GET", body } = {}) {
    const headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    let response;
    try {
      response = await this.fetchFn(`${this.base}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      // A dead engine and a CORS-blocked request both reject with a bare TypeError,
      // so say what to check rather than "Failed to fetch".
      return {
        ok: false,
        kind: "network",
        error: `Cannot reach the engine at ${this.base}. If it is running, add ${this.origin || "this Foundry origin"} to CORS_ORIGINS in its .env and restart it.`,
      };
    }
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      return { ok: false, kind: "http", status: response.status, error: data?.error ?? data?.detail ?? `HTTP ${response.status}` };
    }
    return { ok: true, data };
  }

  #post(path, body) {
    return this.request(path, { method: "POST", body });
  }

  status() { return this.request("/api/status"); }
  state() { return this.request("/api/state"); }
  events(limit = 30) { return this.request(`/api/session/events?limit=${limit}`); }
  combatStatus() { return this.request("/api/combat/status"); }
  scenes() { return this.request("/api/scenes/list"); }
  npcs() { return this.request("/api/npcs"); }
  spatialContext() { return this.request("/api/scene/spatial-context"); }
  spatialRelationships() { return this.request("/api/combat/spatial-relationships"); }
  isPlayerTurn() { return this.request("/api/camera/is-player-turn"); }

  pause() { return this.#post("/api/admin/pause"); }
  resume() { return this.#post("/api/admin/resume"); }
  endSession(reason) { return this.#post("/api/session/end", { reason }); }
  startCombat() { return this.#post("/api/combat/start"); }
  stopCombat() { return this.#post("/api/combat/stop"); }
  switchScene(name) { return this.#post(`/api/scene/switch?scene_name=${encodeURIComponent(name)}`); }
  roll(formula, speaker = "GM", flavor = "") { return this.#post("/api/roll", { formula, speaker, flavor }); }
  narrate(text) { return this.#post("/api/admin/narrate", { text }); }
  setState(field, value) { return this.#post("/api/state/update", { [field]: value }); }

  pan(x, y, duration = 500) { return this.#post("/api/camera/pan", { x, y, duration }); }
  zoom(scale, duration = 500) { return this.#post("/api/camera/zoom", { scale, duration }); }
  pushIn(x, y, duration = 500) { return this.#post("/api/camera/push-in", { x, y, duration }); }
  pullBack(duration = 500) { return this.#post("/api/camera/pull-back", { duration }); }
}
