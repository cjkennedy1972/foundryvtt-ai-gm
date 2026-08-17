/**
 * Session Control Panel — in-Foundry UI for autonomous GM control
 *
 * Provides real-time session status, pause/resume, and settlement queries.
 * Loads via Foundry hooks during game initialization.
 */

class SessionControlPanel {
  constructor() {
    this.isVisible = false;
    this.sessionStatus = null;
    this.refreshInterval = null;
  }

  /**
   * Initialize the control panel and add Foundry hooks
   */
  static init() {
    Hooks.on("init", () => {
      const panel = new SessionControlPanel();
      panel.render(true);
      panel.startRefresh();
    });

    Hooks.on("closeApplication", (app) => {
      if (app instanceof SessionControlPanel) {
        clearInterval(app.refreshInterval);
      }
    });
  }

  /**
   * Render the control panel UI
   */
  async render(force = false) {
    const html = await this.getHTML();
    const panel = document.getElementById("session-control-panel");

    if (panel) {
      panel.innerHTML = html;
    } else {
      const container = document.createElement("div");
      container.id = "session-control-panel";
      container.innerHTML = html;
      container.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        width: 350px;
        background: rgba(20, 20, 20, 0.95);
        border: 2px solid #8B7355;
        border-radius: 8px;
        padding: 15px;
        z-index: 1000;
        font-family: "Signika", sans-serif;
        color: #ddd;
        box-shadow: 0 4px 8px rgba(0, 0, 0, 0.5);
      `;
      document.body.appendChild(container);
    }

    this.attachEventListeners();
  }

  /**
   * Generate HTML for the control panel
   */
  async getHTML() {
    const status = await this.fetchSessionStatus();
    this.sessionStatus = status;

    const statusColor = status.is_running ? "#4CAF50" : "#FF9800";
    const statusText = status.is_running ? "AI Active" : "AI Paused";

    let html = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
        <h3 style="margin: 0; font-size: 16px;">⚙️ Session Control</h3>
        <span style="font-size: 12px; color: ${statusColor}; font-weight: bold;">${statusText}</span>
      </div>

      <div style="margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid #555;">
        <div style="margin-bottom: 8px;">
          <small style="color: #aaa;">Session</small>
          <div style="font-size: 12px;">${status.session_id ? `📍 ${status.campaign || 'Unknown'}` : '❌ No active session'}</div>
        </div>
        ${status.current_time ? `
          <div style="margin-top: 8px;">
            <small style="color: #aaa;">Time</small>
            <div style="font-size: 12px;">🕐 ${status.current_time}</div>
          </div>
        ` : ''}
        <div style="margin-top: 8px;">
          <small style="color: #aaa;">Turns</small>
          <div style="font-size: 12px;">#${status.turn_count}</div>
        </div>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 15px;">
        <button
          id="btn-pause-resume"
          style="
            padding: 8px 12px;
            background: ${status.is_running ? '#FF6B6B' : '#4CAF50'};
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            font-weight: bold;
            transition: all 0.2s;
          "
          onmouseover="this.style.opacity='0.8'"
          onmouseout="this.style.opacity='1'"
        >
          ${status.is_running ? '⏸ Pause' : '▶️ Resume'}
        </button>

        <button
          id="btn-idle-beat"
          style="
            padding: 8px 12px;
            background: #2196F3;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            font-weight: bold;
            transition: all 0.2s;
          "
          onmouseover="this.style.opacity='0.8'"
          onmouseout="this.style.opacity='1'"
        >
          ⚡ Idle Beat
        </button>
      </div>

      <div style="margin-bottom: 15px; padding: 10px; background: rgba(255,255,255,0.05); border-radius: 4px;">
        <small style="color: #aaa;">📍 Settlements</small>
        <div id="settlements-list" style="margin-top: 8px; max-height: 120px; overflow-y: auto; font-size: 12px;">
          <div style="color: #666;">Loading...</div>
        </div>
      </div>

      <div style="font-size: 11px; color: #888; text-align: center;">
        Auto-refresh every 3s
      </div>
    `;

    return html;
  }

  /**
   * Fetch session status from API
   */
  async fetchSessionStatus() {
    try {
      const response = await fetch("/api/session/status");
      if (!response.ok) throw new Error("Failed to fetch status");
      return await response.json();
    } catch (e) {
      console.error("Session status error:", e);
      return {
        session_id: null,
        campaign: null,
        is_running: false,
        current_time: null,
        turn_count: 0,
      };
    }
  }

  /**
   * Fetch settlements from API
   */
  async fetchSettlements() {
    try {
      const response = await fetch("/api/session/settlements");
      if (!response.ok) return [];
      return await response.json();
    } catch (e) {
      console.error("Settlements fetch error:", e);
      return [];
    }
  }

  /**
   * Render settlements list
   */
  async renderSettlements() {
    const settlements = await this.fetchSettlements();
    const list = document.getElementById("settlements-list");

    if (!list) return;

    if (settlements.length === 0) {
      list.innerHTML = '<div style="color: #666;">No settlements</div>';
      return;
    }

    list.innerHTML = settlements
      .map(
        (s) => `
          <div style="margin-bottom: 6px; cursor: pointer; padding: 4px; border-radius: 2px; transition: background 0.2s;"
               onmouseover="this.style.background='rgba(255,255,255,0.1)'"
               onmouseout="this.style.background='transparent'"
               onclick="SessionControlPanel.querySettlement('${s.id}')">
            <strong>${s.name}</strong> (${s.npc_count} NPCs)
          </div>
        `
      )
      .join("");
  }

  /**
   * Attach event listeners to buttons
   */
  attachEventListeners() {
    const pauseBtn = document.getElementById("btn-pause-resume");
    if (pauseBtn) {
      pauseBtn.addEventListener("click", () => this.togglePause());
    }

    const idleBtn = document.getElementById("btn-idle-beat");
    if (idleBtn) {
      idleBtn.addEventListener("click", () => this.triggerIdleBeat());
    }

    this.renderSettlements();
  }

  /**
   * Toggle pause/resume
   */
  async togglePause() {
    try {
      const endpoint = this.sessionStatus?.is_running ? "/api/session/pause" : "/api/session/resume";
      const response = await fetch(endpoint, { method: "POST" });

      if (response.ok) {
        ui.notifications.info(
          this.sessionStatus?.is_running ? "🔇 AI Paused" : "🔊 AI Resumed"
        );
        await this.render();
      }
    } catch (e) {
      console.error("Toggle pause error:", e);
      ui.notifications.error("Failed to toggle pause");
    }
  }

  /**
   * Trigger an idle beat
   */
  async triggerIdleBeat() {
    try {
      const response = await fetch("/api/session/idle-beat", { method: "POST" });

      if (response.ok) {
        ui.notifications.info("⚡ Idle beat triggered");
      }
    } catch (e) {
      console.error("Idle beat error:", e);
      ui.notifications.error("Failed to trigger idle beat");
    }
  }

  /**
   * Query settlement locations (static method for onclick)
   */
  static async querySettlement(settlementId) {
    try {
      const response = await fetch(`/api/session/settlements/${settlementId}`);
      if (!response.ok) throw new Error("Failed to query settlement");

      const data = await response.json();
      let message = `📍 **${data.settlement_id}** at **${data.time_of_day}**:\n`;

      for (const [location, npcs] of Object.entries(data.locations)) {
        message += `• **${location}**: ${npcs.join(", ")}\n`;
      }

      ui.notifications.info(message);
    } catch (e) {
      console.error("Settlement query error:", e);
      ui.notifications.error("Failed to query settlement");
    }
  }

  /**
   * Start auto-refresh loop
   */
  startRefresh() {
    this.refreshInterval = setInterval(() => {
      this.render();
    }, 3000);
  }
}

// Initialize when Foundry is ready
if (game && game.ready) {
  SessionControlPanel.init();
} else {
  Hooks.once("ready", () => {
    SessionControlPanel.init();
  });
}
