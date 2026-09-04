/**
 * AI GM — Control Panel
 *
 * In-Foundry sidebar module that mirrors the external admin panel's capabilities.
 * Provides buttons and panels for session control, combat management, narration,
 * and AI state management directly from within FoundryVTT.
 *
 * Communication paths:
 *   1. REST API via the relay (for commands/status)
 *   2. WebSocket to the admin engine (for real-time updates)
 *   3. socketlib sockets (for inter-module events)
 *
 * No external connection required for core controls when the engine is local.
 */

const MODULE_ID = "aigm-control-panel";

// ─── Configuration ───────────────────────────────────────────────────────────

const CONFIG = {
  engineUrl: "http://localhost:18080",
  wsUrl: null,
  relayUrl: null,
  apiKey: null,
  clientId: null,
  reconnectDelay: 1000,
  maxReconnectDelay: 10000,
  statusPollInterval: 5000,
};

// ─── State ───────────────────────────────────────────────────────────────────

let _engineWs = null;
let _statusTimer = null;
let _connectAttempt = 0;

const PANEL_STATE = {
  engineStatus: null,
  gameState: null,
  aiRunning: false,
  combat: null,
  events: [],
  wsConnected: false,
  npcs: [],
  scenes: [],
  spatialContext: { tokens: [] },
  spatialRelationships: { relationships: [] },
  selectedTokens: new Set(),
};

// ─── API Client ──────────────────────────────────────────────────────────────

class EngineClient {
  constructor() {
    this.baseUrl = CONFIG.engineUrl;
  }

  async request(path, options = {}) {
    try {
      const url = `${this.baseUrl}${path}`;
      const headers = { "Content-Type": "application/json" };
      if (CONFIG.apiKey) {
        headers["Authorization"] = `Bearer ${CONFIG.apiKey}`;
      }
      const response = await fetch(url, {
        ...options,
        headers: { ...headers, ...(options.headers || {}) },
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const error = data?.error || `HTTP ${response.status}`;
        console.warn(`[${MODULE_ID}] API error: ${path} → ${error}`);
        return { ok: false, error };
      }
      return { ok: true, data };
    } catch (e) {
      console.warn(`[${MODULE_ID}] Network error: ${path} → ${e.message}`);
      return { ok: false, error: e.message };
    }
  }

  async fetchStatus() {
    const res = await this.request("/api/status");
    if (res.ok) {
      PANEL_STATE.engineStatus = res.data;
      PANEL_STATE.aiRunning = res.data?.ai_running || false;
      return res;
    }
    return res;
  }

  async fetchState() {
    const res = await this.request("/api/state");
    if (res.ok) {
      PANEL_STATE.gameState = res.data;
      return res;
    }
    return res;
  }

  async fetchEvents(limit = 30) {
    const res = await this.request(`/api/session/events?limit=${limit}`);
    if (res.ok) {
      PANEL_STATE.events = res.data || [];
      return res;
    }
    return res;
  }

  async fetchSession() {
    const res = await this.request("/api/session/active");
    if (res.ok) return res.data;
    return null;
  }

  async pauseAI() {
    return this.request("/api/admin/pause", { method: "POST" });
  }

  async resumeAI() {
    return this.request("/api/admin/resume", { method: "POST" });
  }

  async endSession(reason = "Session ended via control panel") {
    return this.request("/api/session/end", {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  }

  async startCombat() {
    return this.request("/api/combat/start", { method: "POST" });
  }

  async stopCombat() {
    return this.request("/api/combat/stop", { method: "POST" });
  }

  async fetchCombatStatus() {
    const res = await this.request("/api/combat/status");
    if (res.ok) {
      PANEL_STATE.combat = res.data;
      return res;
    }
    return res;
  }

  async getScenes() {
    const res = await this.request("/api/scenes/list");
    if (res.ok) return res.data?.scenes || [];
    return [];
  }

  async switchScene(sceneName) {
    return this.request(`/api/scene/switch?scene_name=${encodeURIComponent(sceneName)}`, {
      method: "POST",
    });
  }

  async rollDice(formula, speaker = "GM", flavor = "") {
    return this.request("/api/roll", {
      method: "POST",
      body: JSON.stringify({ formula, speaker, flavor }),
    });
  }

  async getNpcs() {
    const res = await this.request("/api/npcs");
    if (res.ok) return res.data?.npcs || [];
    return [];
  }

  async getActiveCampaigns() {
    const res = await this.request("/api/campaign/list");
    if (res.ok) return res.data?.campaigns || [];
    return [];
  }

  /** Post narration text verbatim to Foundry chat. */
  async narrate(text) {
    return this.request("/api/admin/narrate", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
  }

  /** Get spatial context (tokens and positions) for current scene. */
  async getSpatialContext() {
    const res = await this.request("/api/scene/spatial-context");
    if (res.ok) return res.data;
    return { tokens: [], error: res.error };
  }

  /** Get spatial relationships (distances, cover, direction) for narration. */
  async getSpatialRelationships() {
    const res = await this.request("/api/combat/spatial-relationships");
    if (res.ok) return res.data;
    return { relationships: [], error: res.error };
  }

  async updateGameState(field, value) {
    return this.request("/api/state/update", {
      method: "POST",
      body: JSON.stringify({ [field]: value }),
    });
  }

  async isPlayerTurn() {
    const res = await this.request("/api/camera/is-player-turn");
    if (res.ok) return res.data?.player_turn ?? false;
    return false;
  }

  async panCamera(x, y, duration = 500) {
    return this.request("/api/camera/pan", {
      method: "POST",
      body: JSON.stringify({ x, y, duration }),
    });
  }

  async panToToken(tokenId, duration = 500) {
    return this.request("/api/camera/pan-to-token", {
      method: "POST",
      body: JSON.stringify({ token_id: tokenId, duration }),
    });
  }

  async zoomCamera(scale, duration = 500) {
    return this.request("/api/camera/zoom", {
      method: "POST",
      body: JSON.stringify({ scale, duration }),
    });
  }

  async pushInCamera(x, y, duration = 500) {
    return this.request("/api/camera/push-in", {
      method: "POST",
      body: JSON.stringify({ x, y, duration }),
    });
  }

  async pullBackCamera(duration = 500) {
    return this.request("/api/camera/pull-back", {
      method: "POST",
      body: JSON.stringify({ duration }),
    });
  }
}

const client = new EngineClient();

// ─── WebSocket Manager ───────────────────────────────────────────────────────

function connectEngineWS() {
  if (_engineWs && _engineWs.readyState === WebSocket.OPEN) return;

  const protocol = CONFIG.engineUrl.startsWith("https") ? "wss" : "ws";
  const host = CONFIG.engineUrl.replace(/https?:\/\//, "");
  const wsUrl = `${protocol}://${host}/api/ws`;
  CONFIG.wsUrl = wsUrl;

  console.log(`[${MODULE_ID}] Connecting to engine WS: ${wsUrl}`);

  _engineWs = new WebSocket(wsUrl);

  _engineWs.onopen = () => {
    console.log(`[${MODULE_ID}] WS connected`);
    PANEL_STATE.wsConnected = true;
    _connectAttempt = 0;
    renderPanel();

    // Send auth token if configured
    if (CONFIG.apiKey) {
      _engineWs.send(JSON.stringify({ type: "auth", token: CONFIG.apiKey }));
    }
  };

  _engineWs.onclose = (evt) => {
    console.log(`[${MODULE_ID}] WS closed: ${evt.code} ${evt.reason}`);
    PANEL_STATE.wsConnected = false;
    renderPanel();

    // Reconnect with backoff (unless intentionally closed or permanent error)
    if (evt.code !== 1000 && evt.code !== 1001) {
      _connectAttempt++;
      const delay = Math.min(
        CONFIG.reconnectDelay * Math.pow(1.5, _connectAttempt),
        CONFIG.maxReconnectDelay
      );
      console.log(`[${MODULE_ID}] Reconnecting in ${Math.round(delay / 1000)}s...`);
      setTimeout(connectEngineWS, delay);
    }
  };

  _engineWs.onerror = (err) => {
    console.warn(`[${MODULE_ID}] WS error`, err);
  };

  _engineWs.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      _handleWsMessage(msg);
    } catch (e) {
      console.warn(`[${MODULE_ID}] WS parse error`, e);
    }
  };
}

function _handleWsMessage(msg) {
  switch (msg.type) {
    case "ai_paused":
      PANEL_STATE.aiRunning = false;
      renderPanel();
      break;
    case "ai_resumed":
      PANEL_STATE.aiRunning = true;
      renderPanel();
      break;
    case "session_started":
      PANEL_STATE.gameState = { ...PANEL_STATE.gameState, session: msg.session_id };
      renderPanel();
      break;
    case "combat_started":
      PANEL_STATE.gameState = { ...PANEL_STATE.gameState, mode: "combat" };
      PANEL_STATE.combat = { round: msg.round || 1, ...PANEL_STATE.combat };
      renderPanel();
      break;
    case "combat_ended":
      PANEL_STATE.gameState = { ...PANEL_STATE.gameState, mode: "exploration" };
      PANEL_STATE.combat = null;
      renderPanel();
      break;
    case "round_started":
      if (PANEL_STATE.combat) {
        PANEL_STATE.combat.round = msg.round;
        renderPanel();
      }
      break;
    case "turn_started":
      if (PANEL_STATE.combat) {
        PANEL_STATE.combat.currentTurn = msg.turn;
        PANEL_STATE.combat.currentActor = msg.actor;
        renderPanel();
      }
      break;
    case "turn_complete":
      if (PANEL_STATE.combat) {
        PANEL_STATE.combat.lastTurnComplete = true;
        renderPanel();
      }
      break;
    case "actions_executed":
      _pollStatus();
      break;
    case "scene_loaded":
      if (PANEL_STATE.gameState) {
        PANEL_STATE.gameState.current_scene = msg.scene_name;
        renderPanel();
      }
      break;
  }
}

function _pollStatus() {
  client.fetchStatus();
  client.fetchState();
  client.fetchEvents();
  client.fetchCombatStatus();
}

function startStatusPolling() {
  _statusTimer = setInterval(_pollStatus, CONFIG.statusPollInterval);
}

function stopStatusPolling() {
  if (_statusTimer) {
    clearInterval(_statusTimer);
    _statusTimer = null;
  }
}

// ─── Control Actions ─────────────────────────────────────────────────────────

const Controls = {
  async pause() {
    const res = await client.pauseAI();
    if (res.ok) {
      PANEL_STATE.aiRunning = false;
      renderPanel();
      ui.notifications.info("⏸ AI engine paused");
    } else {
      ui.notifications.error(`Pause failed: ${res.error}`);
    }
  },

  async resume() {
    const res = await client.resumeAI();
    if (res.ok) {
      PANEL_STATE.aiRunning = true;
      renderPanel();
      ui.notifications.info("▶ AI engine resumed");
    } else {
      ui.notifications.error(`Resume failed: ${res.error}`);
    }
  },

  async startCombat() {
    const res = await client.startCombat();
    if (res.ok) {
      ui.notifications.info("⚔️ Combat started!");
      _pollStatus();
    } else {
      ui.notifications.error(`Combat start failed: ${res.error}`);
    }
  },

  async stopCombat() {
    const res = await client.stopCombat();
    if (res.ok) {
      ui.notifications.info("⏹ Combat stopped");
      _pollStatus();
    } else {
      ui.notifications.error(`Combat stop failed: ${res.error}`);
    }
  },

  async endSession() {
    if (!game.user.isGM) {
      ui.notifications.warn("Only the GM can end a session");
      return;
    }
    const reason = await _promptSessionEnd();
    if (!reason) return;

    const res = await client.endSession(reason);
    if (res.ok) {
      ui.notifications.info("✅ Session ended");
      _pollStatus();
    } else {
      ui.notifications.error(`Session end failed: ${res.error}`);
    }
  },

  async rollDice() {
    const result = await dialog._showRollDialog();
    if (!result) return;

    const res = await client.rollDice(result.formula, result.speaker, result.flavor);
    if (res.ok) {
      // Engine wraps the relay's RPC reply as {type, requestId, data: {roll: {...}}} —
      // fall back progressively in case a given relay op returns it less wrapped.
      const roll = res.data?.data?.roll ?? res.data?.roll ?? res.data;
      const total = roll?.total ?? roll?.rollTotal;
      ui.notifications.info(`🎲 ${roll?.formula ?? result.formula}: ${total ?? "success"}`);
    } else {
      ui.notifications.error(`Roll failed: ${res.error}`);
    }
  },

  async narrate(text, includeSpatial = false) {
    if (!text || !text.trim()) {
      ui.notifications.warn("Please enter narration text");
      return;
    }

    let finalText = text;

    // Append spatial context if selected
    if (includeSpatial && PANEL_STATE.spatialRelationships.relationships) {
      const spatial = PANEL_STATE.spatialRelationships.relationships;
      if (spatial.length > 0) {
        let spatialStr = "\n\n**Spatial Context:**";
        spatial.forEach(rel => {
          const dirStr = rel.direction === "left" ? "on your left" : rel.direction === "right" ? "on your right" : "ahead";
          const coverStr = rel.cover === "none" ? "" : ` behind ${rel.cover === "half" ? "partial" : "heavy"} cover`;
          spatialStr += `\n- ${rel.name}: ${rel.distance_ft} ft ${dirStr}${coverStr}`;
        });
        finalText += spatialStr;
      }
    }

    const res = await client.narrate(finalText);
    if (res.ok) {
      ui.notifications.info("📜 Narration sent");
      // Clear the textarea after successful post
      const ta = document.querySelector("#aigm-narrate-text");
      if (ta) ta.value = "";
    } else {
      ui.notifications.error(`Narration failed: ${res.error}`);
    }
  },

  async switchToScene(sceneName) {
    const res = await client.switchScene(sceneName);
    if (res.ok) {
      ui.notifications.info(`🗺 Switched to ${sceneName}`);
      _pollStatus();
    } else {
      ui.notifications.error(`Scene switch failed: ${res.error}`);
    }
  },

  async refreshStatus() {
    await _pollStatus();
    ui.notifications.info("🔄 Status refreshed");
  },

  async testConnection() {
    const res = await client.fetchStatus();
    if (res.ok) {
      ui.notifications.info(`✅ Engine connected: ${client.baseUrl}`);
    } else {
      ui.notifications.error(`❌ Cannot reach engine: ${res.error}`);
    }
  },

  async setMode(mode) {
    const res = await client.updateGameState("mode", mode);
    if (res.ok) {
      PANEL_STATE.gameState = { ...PANEL_STATE.gameState, mode };
      renderPanel();
      ui.notifications.info(`Mode set to ${mode}`);
    } else {
      ui.notifications.error(`Mode set failed: ${res.error}`);
    }
  },

  async panCamera() {
    if (await client.isPlayerTurn()) {
      ui.notifications.warn("Cannot move camera during player's turn");
      return;
    }
    const res = await client.panCamera(0, 0, 500);
    if (!res.ok) {
      ui.notifications.error(`Pan failed: ${res.error}`);
    }
  },

  async pushInCamera() {
    if (await client.isPlayerTurn()) {
      ui.notifications.warn("Cannot move camera during player's turn");
      return;
    }
    const res = await client.pushInCamera(0, 0, 500);
    if (!res.ok) {
      ui.notifications.error(`Push in failed: ${res.error}`);
    }
  },

  async pullBackCamera() {
    if (await client.isPlayerTurn()) {
      ui.notifications.warn("Cannot move camera during player's turn");
      return;
    }
    const res = await client.pullBackCamera(500);
    if (!res.ok) {
      ui.notifications.error(`Pull back failed: ${res.error}`);
    }
  },

  async zoomIn() {
    if (await client.isPlayerTurn()) {
      ui.notifications.warn("Cannot move camera during player's turn");
      return;
    }
    const res = await client.zoomCamera(1.5, 300);
    if (!res.ok) {
      ui.notifications.error(`Zoom failed: ${res.error}`);
    }
  },

  async zoomOut() {
    if (await client.isPlayerTurn()) {
      ui.notifications.warn("Cannot move camera during player's turn");
      return;
    }
    const res = await client.zoomCamera(0.75, 300);
    if (!res.ok) {
      ui.notifications.error(`Zoom failed: ${res.error}`);
    }
  },
};

// ─── Roll Dialog ─────────────────────────────────────────────────────────────

const dialog = {
  _showRollDialog() {
    return new Promise((resolve) => {
      const d = new Dialog({
        title: "AI GM — Dice Roll",
        content: `
          <div class="form-group">
            <label>Formula</label>
            <input type="text" id="aigm-roll-formula" value="1d20" placeholder="e.g., 2d6+3" />
          </div>
          <div class="form-group">
            <label>Speaker</label>
            <select id="aigm-roll-speaker">
              <option value="GM">GM</option>
              ${game.actors?.players?.map(a => `<option value="${a.name}">${a.name}</option>`).join("") || ""}
            </select>
          </div>
          <div class="form-group">
            <label>Flavor</label>
            <input type="text" id="aigm-roll-flavor" placeholder="Optional description" />
          </div>
        `,
        buttons: {
          roll: {
            label: "Roll",
            icon: '<i class="fas fa-dice"></i>',
            callback: () => {
              resolve({
                formula: document.getElementById("aigm-roll-formula").value,
                speaker: document.getElementById("aigm-roll-speaker").value,
                flavor: document.getElementById("aigm-roll-flavor").value,
              });
            },
          },
          cancel: {
            label: "Cancel",
            icon: '<i class="fas fa-times"></i>',
            callback: () => resolve(null),
          },
        },
        default: "roll",
      });
      d.render(true);
    });
  },
};

// ─── Session End Dialog ──────────────────────────────────────────────────────

function _promptSessionEnd() {
  return new Promise((resolve) => {
    const d = new Dialog({
      title: "AI GM — End Session",
      content: `
        <div class="form-group">
          <label>Reason for ending session</label>
          <textarea id="aigm-end-reason" rows="3" placeholder="Optional: why are you ending this session?">GM ended session</textarea>
        </div>
      `,
      buttons: {
        end: {
          label: "End Session",
          icon: '<i class="fas fa-sign-out-alt"></i>',
          callback: () => resolve(document.getElementById("aigm-end-reason").value),
        },
        cancel: {
          label: "Cancel",
          icon: '<i class="fas fa-times"></i>',
          callback: () => resolve(null),
        },
      },
      default: "end",
    });
    d.render(true);
  });
}

// ─── Panel Renderer ──────────────────────────────────────────────────────────

const _ESCAPE_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
/** Escape untrusted text (actor/scene names, event descriptions) before innerHTML use. */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => _ESCAPE_MAP[c]);
}

function _badge(className, text) {
  return `<span class="aigm-badge ${className}">${text}</span>`;
}

function _statusCard(title, status, icon) {
  const iconMap = {
    foundry: { connected: "🔗", disconnected: "❌" },
    relay: { running: "📡", crashed: "💥", down: "⏸" },
    ws: { connected: "🔌", disconnected: "⚫" },
    ai: { running: "🤖", paused: "⏸" },
  };
  const icons = iconMap[icon] || {};
  const iconChar = icons[status] || "•";
  return `<div class="aigm-stat">${iconChar} <strong>${title}</strong>: ${status}</div>`;
}

function _panelHTML() {
  const { engineStatus, gameState, aiRunning, combat, wsConnected } = PANEL_STATE;

  return `
    <div class="aigm-panel">
      <!-- Header -->
      <div class="aigm-header">
        <h3><i class="fas fa-robot"></i> AI GM Control</h3>
        <div class="aigm-badges">
          ${_badge(
            engineStatus?.connected ? "aigm-connected" : "aigm-disconnected",
            engineStatus?.connected ? "🔗 Foundry" : "❌ Foundry"
          )}
          ${_badge(
            engineStatus?.relay?.running ? "aigm-relay" : "aigm-relay-off",
            engineStatus?.relay?.running ? "📡 Relay" : "⏸ Relay"
          )}
          ${_badge(wsConnected ? "aigm-ws" : "aigm-ws-off", wsConnected ? "🔌 WS" : "⚫ WS")}
          ${_badge(aiRunning ? "aigm-ai" : "aigm-ai-paused", aiRunning ? "🤖 Active" : "⏸ Paused")}
        </div>
      </div>

      <!-- Status Summary -->
      <div class="aigm-section">
        <h4><i class="fas fa-tachometer-alt"></i> Engine Status</h4>
        <div class="aigm-grid">
          ${_statusCard("Model", engineStatus?.model || "Unknown", "foundry")}
          ${_statusCard("Campaign", gameState?.campaign || "None", "foundry")}
          ${_statusCard("Mode", gameState?.mode || "exploration", "foundry")}
          ${_statusCard("Scene", gameState?.current_scene || "None", "foundry")}
          ${_statusCard("Context", `${engineStatus?.conversation_length || 0} msgs`, "foundry")}
        </div>
      </div>

      <!-- Session Controls -->
      <div class="aigm-section">
        <h4><i class="fas fa-gamepad"></i> Session Controls</h4>
        <div class="aigm-controls">
          <button class="aigm-btn aigm-btn-primary" id="aigm-btn-refresh" title="Refresh status">
            <i class="fas fa-sync-alt"></i> Refresh
          </button>
          <button class="aigm-btn" id="aigm-btn-connection" title="Test connection">
            <i class="fas fa-signal"></i> Test
          </button>
          ${game?.user?.isGM ? `
            <button class="aigm-btn aigm-btn-danger" id="aigm-btn-end" title="End current session">
              <i class="fas fa-sign-out-alt"></i> End Session
            </button>
          ` : ""}
        </div>
      </div>

      <!-- AI Controls -->
      <div class="aigm-section">
        <h4><i class="fas fa-brain"></i> AI Engine</h4>
        <div class="aigm-controls">
          <button class="aigm-btn" id="aigm-btn-pause" ${aiRunning ? "" : "disabled"}>
            <i class="fas fa-pause"></i> Pause
          </button>
          <button class="aigm-btn aigm-btn-primary" id="aigm-btn-resume" ${!aiRunning ? "" : "disabled"}>
            <i class="fas fa-play"></i> Resume
          </button>
        </div>
      </div>

      <!-- Mode Selector -->
      <div class="aigm-section">
        <h4><i class="fas fa-exchange-alt"></i> Game Mode</h4>
        <div class="aigm-controls">
          <button class="aigm-btn ${gameState?.mode === 'exploration' ? 'aigm-btn-primary' : ''}"
                  id="aigm-mode-exploration" ${gameState?.mode === 'exploration' ? 'disabled' : ''}>
            🌍 Exploration
          </button>
          <button class="aigm-btn ${gameState?.mode === 'social' ? 'aigm-btn-primary' : ''}"
                  id="aigm-mode-social" ${gameState?.mode === 'social' ? 'disabled' : ''}>
            💬 Social
          </button>
          <button class="aigm-btn ${gameState?.mode === 'combat' ? 'aigm-btn-primary' : ''}"
                  id="aigm-mode-combat" ${gameState?.mode === 'combat' ? 'disabled' : ''}>
            ⚔️ Combat
          </button>
        </div>
      </div>

      <!-- Combat Controls -->
      <div class="aigm-section">
        <h4><i class="fas fa-swords"></i> Combat</h4>
        ${combat ? `
          <div class="aigm-combat-info">
            <div class="aigm-combat-stat">Round: <strong>${combat.round}</strong></div>
            ${combat.currentTurn !== undefined ? `<div class="aigm-combat-stat">Turn: <strong>${combat.currentTurn}</strong></div>` : ""}
            ${combat.currentActor ? `<div class="aigm-combat-stat">Actor: <strong>${combat.currentActor}</strong></div>` : ""}
          </div>
          <div class="aigm-controls">
            <button class="aigm-btn aigm-btn-danger" id="aigm-btn-stop-combat">
              <i class="fas fa-stop"></i> Stop Combat
            </button>
          </div>
        ` : `
          <div class="aigm-controls">
            <button class="aigm-btn aigm-btn-warning" id="aigm-btn-start-combat">
              <i class="fas fa-swords"></i> Start Combat
            </button>
          </div>
        `}
      </div>

      <!-- Cinematic Camera Controls -->
      <div class="aigm-section">
        <h4><i class="fas fa-film"></i> Cinematic Camera</h4>
        <div class="aigm-controls">
          <button class="aigm-btn" id="aigm-btn-pan" title="Pan to scene center">
            <i class="fas fa-arrows-alt"></i> Pan
          </button>
          <button class="aigm-btn" id="aigm-btn-push-in" title="Push in on focus point">
            <i class="fas fa-search-plus"></i> Push In
          </button>
          <button class="aigm-btn" id="aigm-btn-pull-back" title="Pull back and reset view">
            <i class="fas fa-search-minus"></i> Pull Back
          </button>
        </div>
        <div class="aigm-zoom-controls">
          <button class="aigm-btn aigm-btn-sm" id="aigm-btn-zoom-in" title="Zoom in">
            <i class="fas fa-plus"></i>
          </button>
          <button class="aigm-btn aigm-btn-sm" id="aigm-btn-zoom-out" title="Zoom out">
            <i class="fas fa-minus"></i>
          </button>
        </div>
      </div>

      <!-- Narration -->
      <div class="aigm-section">
        <h4><i class="fas fa-book-open"></i> Narration</h4>
        <div class="aigm-narrate-form">
          <!-- Spatial context display -->
          <div id="aigm-spatial-context" class="aigm-spatial-context"></div>
          <!-- Narration text input -->
          <textarea id="aigm-narrate-text" rows="3"
                    placeholder="Enter narration text to post to Foundry chat..."></textarea>
          <!-- Spatial data toggle -->
          <div class="aigm-spatial-options">
            <label>
              <input type="checkbox" id="aigm-include-spatial" checked />
              Include spatial context in narration
            </label>
          </div>
          <button class="aigm-btn aigm-btn-primary aigm-btn-full" id="aigm-btn-narrate">
            <i class="fas fa-comment-alt"></i> Post Narration
          </button>
        </div>
      </div>

      <!-- Quick Actions -->
      <div class="aigm-section">
        <h4><i class="fas fa-bolt"></i> Quick Actions</h4>
        <div class="aigm-controls">
          <button class="aigm-btn" id="aigm-btn-roll">
            <i class="fas fa-dice"></i> Roll Dice
          </button>
          <button class="aigm-btn" id="aigm-btn-npcs" title="View NPC registry">
            <i class="fas fa-users"></i> NPCs
          </button>
          <button class="aigm-btn" id="aigm-btn-scenes" title="Switch scenes">
            <i class="fas fa-map"></i> Scenes
          </button>
        </div>
        <div id="aigm-npc-list" class="aigm-list"></div>
        <div id="aigm-scene-list" class="aigm-list"></div>
      </div>

      <!-- Recent Events -->
      <div class="aigm-section">
        <h4><i class="fas fa-list"></i> Recent Activity</h4>
        <div class="aigm-events">
          ${PANEL_STATE.events?.length ? PANEL_STATE.events.slice(-8).reverse().map(evt => `
            <div class="aigm-event">
              <span class="aigm-event-time">${evt.timestamp ? evt.timestamp.slice(11, 19) : ""}</span>
              <span class="aigm-event-msg">${esc(evt.description)}</span>
            </div>
          `).join("") : '<div class="aigm-empty">No events yet</div>'}
        </div>
      </div>
    </div>
  `;
}

/** AI GM control panel window. Opened/closed via the scene-controls button. */
class AIGMControlPanel extends Application {
  static get defaultOptions() {
    return foundry.utils.mergeObject(super.defaultOptions, {
      id: "aigm-control-panel",
      title: "AI GM Control",
      width: 360,
      height: 640,
      resizable: true,
    });
  }

  async _renderInner() {
    return $(_panelHTML());
  }

  activateListeners(html) {
    super.activateListeners(html);
    _attachListeners(html[0]);
  }
}

let panelApp = null;

function renderPanel() {
  if (panelApp?.rendered) panelApp.render(false);
}

function togglePanel() {
  if (!panelApp) panelApp = new AIGMControlPanel();
  if (panelApp.rendered) {
    panelApp.close();
  } else {
    panelApp.render(true);
  }
}

function _attachListeners(root = document) {
  const safeClick = (id, handler) => {
    const el = root.querySelector(`#${id}`);
    if (el) el.addEventListener("click", handler);
  };

  safeClick("aigm-btn-refresh", () => Controls.refreshStatus());
  safeClick("aigm-btn-connection", () => Controls.testConnection());
  safeClick("aigm-btn-end", () => Controls.endSession());
  safeClick("aigm-btn-pause", () => Controls.pause());
  safeClick("aigm-btn-resume", () => Controls.resume());
  safeClick("aigm-btn-start-combat", () => Controls.startCombat());
  safeClick("aigm-btn-stop-combat", () => Controls.stopCombat());
  safeClick("aigm-btn-roll", () => Controls.rollDice());
  safeClick("aigm-btn-narrate", () => {
    const ta = root.querySelector("#aigm-narrate-text");
    const includeSpatial = root.querySelector("#aigm-include-spatial")?.checked ?? false;
    if (ta) Controls.narrate(ta.value, includeSpatial);
  });
  safeClick("aigm-btn-npcs", () => _loadNpcs(root));
  safeClick("aigm-btn-scenes", () => _loadScenes(root));
  safeClick("aigm-mode-exploration", () => Controls.setMode("exploration"));
  safeClick("aigm-mode-social", () => Controls.setMode("social"));
  safeClick("aigm-mode-combat", () => Controls.setMode("combat"));
  safeClick("aigm-btn-pan", () => Controls.panCamera());
  safeClick("aigm-btn-push-in", () => Controls.pushInCamera());
  safeClick("aigm-btn-pull-back", () => Controls.pullBackCamera());
  safeClick("aigm-btn-zoom-in", () => Controls.zoomIn());
  safeClick("aigm-btn-zoom-out", () => Controls.zoomOut());

  // Load spatial context
  _loadSpatialContext(root);

  // Narrate on Ctrl+Enter
  const narrateTa = root.querySelector("#aigm-narrate-text");
  if (narrateTa) {
    narrateTa.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        const includeSpatial = root.querySelector("#aigm-include-spatial")?.checked ?? false;
        Controls.narrate(narrateTa.value, includeSpatial);
      }
    });
  }
}

// ─── NPC/Scene Loaders ───────────────────────────────────────────────────────

async function _loadNpcs(root = document) {
  const list = root.querySelector("#aigm-npc-list");
  if (!list) return;
  list.innerHTML = '<div class="aigm-loading"><i class="fas fa-spinner fa-spin"></i></div>';
  const npcs = await client.getNpcs();
  PANEL_STATE.npcs = npcs;
  if (npcs.length) {
    list.innerHTML = npcs.slice(0, 20).map(npc => `
      <div class="aigm-npc-item" data-npc-id="${esc(npc.id)}" title="Click to speak">
        <strong>${esc(npc.name)}</strong>
        <span class="aigm-npc-meta">${esc(npc.type)}</span>
      </div>
    `).join("");
  } else {
    list.innerHTML = '<div class="aigm-empty">No NPCs registered</div>';
  }
}

async function _loadScenes(root = document) {
  const list = root.querySelector("#aigm-scene-list");
  if (!list) return;
  list.innerHTML = '<div class="aigm-loading"><i class="fas fa-spinner fa-spin"></i></div>';
  const scenes = await client.getScenes();
  PANEL_STATE.scenes = scenes;
  if (scenes.length) {
    list.innerHTML = scenes.map(s => `
      <div class="aigm-scene-item ${PANEL_STATE.gameState?.current_scene === s.name ? 'aigm-current' : ''}"
           data-scene="${esc(s.name)}">
        ${esc(s.name)}
        ${PANEL_STATE.gameState?.current_scene === s.name ? '<span class="aigm-current-badge">●</span>' : ''}
      </div>
    `).join("");
    // Attach click handlers for scene switching
    list.querySelectorAll(".aigm-scene-item").forEach(el => {
      el.addEventListener("click", () => {
        const name = el.dataset.scene;
        if (name !== PANEL_STATE.gameState?.current_scene) {
          Controls.switchToScene(name);
        }
      });
    });
  } else {
    list.innerHTML = '<div class="aigm-empty">No scenes found</div>';
  }
}

async function _loadSpatialContext(root = document) {
  const container = root.querySelector("#aigm-spatial-context");
  if (!container) return;

  try {
    const [spatialCtx, relationships] = await Promise.all([
      client.getSpatialContext(),
      client.getSpatialRelationships(),
    ]);

    PANEL_STATE.spatialContext = spatialCtx;
    PANEL_STATE.spatialRelationships = relationships;

    if (!relationships.relationships || relationships.relationships.length === 0) {
      container.innerHTML = '<div class="aigm-empty">No tokens on scene</div>';
      return;
    }

    // Build spatial context display
    let html = '<div class="aigm-tokens-list"><strong>Spatial Context:</strong>';
    relationships.relationships.slice(0, 8).forEach(rel => {
      const isSelected = PANEL_STATE.selectedTokens.has(rel.id);
      const dispositionIcon = rel.disposition >= 1 ? '👤' : rel.disposition === 0 ? '◆' : '👹';
      html += `
        <div class="aigm-token-item ${isSelected ? 'aigm-selected' : ''}" data-token-id="${esc(rel.id)}">
          <span class="aigm-token-name">${dispositionIcon} ${esc(rel.name)}</span>
          <span class="aigm-token-distance">${rel.distance_ft} ft</span>
          <span class="aigm-token-cover">${rel.cover === 'none' ? '◼' : rel.cover === 'half' ? '◐' : '●'}</span>
        </div>
      `;
    });
    html += '</div>';
    container.innerHTML = html;

    // Attach token selection handlers
    container.querySelectorAll(".aigm-token-item").forEach(el => {
      el.addEventListener("click", () => {
        const tokenId = el.dataset.tokenId;
        if (PANEL_STATE.selectedTokens.has(tokenId)) {
          PANEL_STATE.selectedTokens.delete(tokenId);
        } else {
          PANEL_STATE.selectedTokens.add(tokenId);
        }
        _loadSpatialContext(root);
      });
    });
  } catch (e) {
    console.warn(`[${MODULE_ID}] Failed to load spatial context:`, e);
    container.innerHTML = '<div class="aigm-empty">Could not load spatial data</div>';
  }
}

// ─── Socketlib Integration ───────────────────────────────────────────────────

Hooks.once("socketlib.ready", () => {
  const socket = socketlib.registerModule(MODULE_ID);

  // Subscribe to engine broadcast events
  socket.register("engine-status", (data) => {
    Object.assign(PANEL_STATE, data);
    renderPanel();
  });

  socket.register("combat-event", (data) => {
    switch (data.type) {
      case "start":
        PANEL_STATE.gameState = { ...PANEL_STATE.gameState, mode: "combat" };
        break;
      case "end":
        PANEL_STATE.gameState = { ...PANEL_STATE.gameState, mode: "exploration" };
        PANEL_STATE.combat = null;
        break;
      case "round":
        if (PANEL_STATE.combat) {
          PANEL_STATE.combat.round = data.round;
        }
        break;
    }
    renderPanel();
  });

  console.log(`[${MODULE_ID}] socketlib registered`);
});

// ─── Foundry Scene Controls Integration ──────────────────────────────────────

Hooks.on("getSceneControlButtons", (controls) => {
  if (!game.user.isGM) return;

  const tool = {
    name: MODULE_ID,
    title: "AI GM Control",
    icon: "fas fa-robot",
    button: true,
    onClick: () => togglePanel(),
  };

  // v13 passes controls/tools as Record<string, T>; v11-12 pass arrays.
  if (Array.isArray(controls)) {
    const tokenControls = controls.find(c => c.name === "token") || controls[0];
    tokenControls?.tools.push(tool);
  } else {
    const tokenControls = controls.tokens || Object.values(controls)[0];
    if (tokenControls) tokenControls.tools[MODULE_ID] = tool;
  }
});

Hooks.once("init", () => {
  game.settings.register(MODULE_ID, "engineUrl", {
    name: "Engine URL",
    hint: "URL of the AI GM engine (default: http://localhost:18080)",
    scope: "world",
    config: true,
    type: String,
    default: "http://localhost:18080",
    onChange: () => {
      CONFIG.engineUrl = game.settings.get(MODULE_ID, "engineUrl");
      client.baseUrl = CONFIG.engineUrl;
      if (_engineWs?.readyState === WebSocket.OPEN) {
        _engineWs.close();
      }
      connectEngineWS();
    },
  });

  game.settings.register(MODULE_ID, "adminToken", {
    name: "Admin Token",
    hint: "Bearer token for the engine when ADMIN_TOKEN is set (leave blank if unset). Stored in THIS browser only — set it on the GM's client; players never receive it.",
    // scope: "client", NOT "world". Foundry replicates world-scoped settings to
    // every connected client, so a world-scoped token was readable by any
    // player via game.settings.get() — full admin-API access whenever the
    // engine is exposed beyond loopback. Client scope keeps it in the GM's
    // browser, which is the only client that talks to the engine anyway
    // (connectEngineWS and the status poller are both isGM-gated).
    scope: "client",
    config: true,
    type: String,
    default: "",
    onChange: () => {
      CONFIG.apiKey = game.settings.get(MODULE_ID, "adminToken") || null;
    },
  });

  CONFIG.engineUrl = game.settings.get(MODULE_ID, "engineUrl") || "http://localhost:18080";
  CONFIG.apiKey = game.settings.get(MODULE_ID, "adminToken") || null;
});

Hooks.once("ready", () => {
  if (!game.user.isGM) return;
  connectEngineWS();
  startStatusPolling();
});

// Clean up on restart
Hooks.once("release", () => {
  stopStatusPolling();
  if (_engineWs) {
    _engineWs.close();
    _engineWs = null;
  }
});

// Export API for other modules
game.modules.get(MODULE_ID).api = {
  get state() { return PANEL_STATE; },
  get connected() { return PANEL_STATE.engineStatus?.connected ?? false; },
  get engineStatus() { return PANEL_STATE.engineStatus; },
  get gameState() { return PANEL_STATE.gameState; },
  get aiRunning() { return PANEL_STATE.aiRunning; },
  controls: Controls,
  client,
};

console.log(`[${MODULE_ID}] Control panel ready`);
