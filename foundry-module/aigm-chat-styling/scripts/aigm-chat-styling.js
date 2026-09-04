/**
 * AI GM — Chat Styling
 *
 * Provides distinct visual styling for narration, NPC dialogue, and mechanical results.
 * Adds an ephemeral "thinking" indicator that appears within 200ms of a player message
 * and is cleared when the first narration token is received.
 */

const MODULE_ID = "aigm-chat-styling";
const THINKING_INDICATOR_ID = "aigm-thinking-indicator";
const THINKING_DISPLAY_TIMEOUT_MS = 200;
const NPC_COLOR_CACHE = new Map(); // npc_name -> {color, portraitUrl}

// ─── Thinking Indicator ──────────────────────────────────────────────────────

let thinkingIndicatorTimeout = null;
let thinkingMessageId = null;

/**
 * Show the "GM is thinking" indicator in chat.
 * This is called when a player message is detected and clears on narration.
 */
async function showThinkingIndicator() {
  // Clear any existing timeout to avoid duplicate indicators
  clearThinkingIndicator();

  // Delay showing the indicator by 200ms to avoid flashing for quick responses
  thinkingIndicatorTimeout = setTimeout(async () => {
    try {
      const content = `<div class="aigm-thinking-bubble">
        <span class="aigm-thinking-dot"></span>
        <span class="aigm-thinking-dot"></span>
        <span class="aigm-thinking-dot"></span>
      </div><p><em>The GM considers…</em></p>`;

      const msg = await ChatMessage.create({
        content,
        speaker: { alias: "GM" },
        type: "other",
        flags: {
          [MODULE_ID]: { isThinkingIndicator: true }
        }
      });

      thinkingMessageId = msg.id;
    } catch (e) {
      console.warn(`[${MODULE_ID}] Failed to create thinking indicator:`, e);
    }
  }, THINKING_DISPLAY_TIMEOUT_MS);
}

/**
 * Clear the thinking indicator if it exists.
 * Called when narration is received.
 */
async function clearThinkingIndicator() {
  if (thinkingIndicatorTimeout) {
    clearTimeout(thinkingIndicatorTimeout);
    thinkingIndicatorTimeout = null;
  }

  if (thinkingMessageId) {
    try {
      const msg = game.messages.get(thinkingMessageId);
      if (msg) {
        await msg.delete();
      }
    } catch (e) {
      console.warn(`[${MODULE_ID}] Failed to delete thinking indicator:`, e);
    }
    thinkingMessageId = null;
  }
}

// ─── NPC Styling ─────────────────────────────────────────────────────────────

/**
 * Generate a stable color for an NPC based on their name.
 * Same input always produces the same color.
 */
function getNpcColor(npcName) {
  if (NPC_COLOR_CACHE.has(npcName)) {
    return NPC_COLOR_CACHE.get(npcName).color;
  }

  // Simple hash function based on name
  let hash = 0;
  for (let i = 0; i < npcName.length; i++) {
    hash = ((hash << 5) - hash) + npcName.charCodeAt(i);
    hash |= 0;
  }

  // Convert to HSL for perceptually consistent colors
  const hue = Math.abs(hash) % 360;
  const saturation = 60 + (Math.abs(hash) % 20); // 60-80%
  const lightness = 45 + (Math.abs(hash) % 15); // 45-60%

  const color = `hsl(${hue}, ${saturation}%, ${lightness}%)`;
  NPC_COLOR_CACHE.set(npcName, { color });
  return color;
}

// ─── Message Type Detection ──────────────────────────────────────────────────

/**
 * Determine the message type based on speaker, content, and context.
 * Returns: 'narration', 'npc', 'roll', 'player', or 'other'
 *
 * Detection logic:
 * 1. Rolls (any dice-roll content or message type="roll")
 * 2. Thinking indicator (flagged by our module)
 * 3. Player characters (owned by a player)
 * 4. NPC dialogue (actor exists but no player owner, or alias is NPC)
 * 5. Narration (GM speaker or no actor, treated as narrative prose)
 */
function detectMessageType(message) {
  const speaker = message.speaker || {};
  const content = message.content || "";
  const flags = message.flags || {};
  const actorId = speaker.actor;
  const alias = speaker.alias || speaker.name || "";

  // Thinking indicator
  if (flags[MODULE_ID]?.isThinkingIndicator) {
    return "thinking";
  }

  // Rolls and mechanical results (highest priority)
  if (message.type === "roll" || content.includes("data-dice") || content.includes(".dice-roll")) {
    return "roll";
  }

  // Check if speaker is an actor
  if (actorId) {
    try {
      const actor = game.actors.get(actorId);
      if (actor) {
        // Player character (owned by a player)
        if (actor.hasPlayerOwner) {
          return "player";
        }
        // NPC (actor exists, but not player-owned)
        return "npc";
      }
    } catch (e) {
      console.debug(`[${MODULE_ID}] Could not resolve actor ${actorId}:`, e);
    }
  }

  // Check if speaker name matches a known actor (fallback for token-less speakers)
  if (alias) {
    try {
      // Search by name in case actorId wasn't set
      const actorByName = game.actors.getName(alias);
      if (actorByName) {
        return actorByName.hasPlayerOwner ? "player" : "npc";
      }
    } catch (e) {
      console.debug(`[${MODULE_ID}] Could not resolve actor by name ${alias}:`, e);
    }
  }

  // Speaker is the GM or system (no actor)
  const isGm = alias === "GM" || alias.toLowerCase() === "gamemaster" || alias === "";
  if (isGm) {
    return "narration";
  }

  // Default: treat unknown speakers as NPC dialogue if they have a distinct name
  if (alias && alias.length > 0) {
    return "npc";
  }

  // Last resort: treat as narration
  return "narration";
}

// ─── Hook Listeners ──────────────────────────────────────────────────────────

/**
 * Hook: Called after a chat message is rendered.
 * Apply styling based on message type.
 */
function onRenderChatMessage(message, html, data) {
  const type = detectMessageType(message);
  const messageEl = html[0] || html;

  if (!messageEl) return;

  // Remove any existing type classes
  messageEl.classList.remove(
    "aigm-narration",
    "aigm-npc-dialogue",
    "aigm-player-message",
    "aigm-mechanical-result",
    "aigm-thinking"
  );

  // Apply appropriate class
  switch (type) {
    case "narration":
      messageEl.classList.add("aigm-narration");
      break;
    case "npc":
      messageEl.classList.add("aigm-npc-dialogue");
      const speaker = message.speaker?.alias || message.speaker?.name;
      if (speaker) {
        const color = getNpcColor(speaker);
        messageEl.style.setProperty("--npc-color", color);
      }
      break;
    case "player":
      messageEl.classList.add("aigm-player-message");
      showThinkingIndicator();
      break;
    case "roll":
      messageEl.classList.add("aigm-mechanical-result");
      break;
    case "thinking":
      messageEl.classList.add("aigm-thinking");
      break;
    default:
      break;
  }

  // Clear thinking indicator when narration appears
  if (type === "narration") {
    clearThinkingIndicator();
  }
}

/**
 * Hook: Called when a chat message is created.
 * Used to detect player messages early.
 */
function onCreateChatMessage(message) {
  const type = detectMessageType(message);

  if (type === "player") {
    // Prepare to show thinking indicator (with delay)
    showThinkingIndicator();
  } else if (type === "narration") {
    // Clear thinking indicator immediately
    clearThinkingIndicator();
  }
}

// ─── Module Initialization ──────────────────────────────────────────────────

Hooks.once("init", () => {
  console.log(`[${MODULE_ID}] Initializing chat styling module`);
});

Hooks.once("ready", () => {
  console.log(`[${MODULE_ID}] Chat styling ready`);

  // Register hooks for chat message rendering
  Hooks.on("renderChatMessage", onRenderChatMessage);
  Hooks.on("createChatMessage", onCreateChatMessage);

  console.log(`[${MODULE_ID}] Hooks registered`);
});

// Cleanup on module disable
Hooks.once("release", () => {
  clearThinkingIndicator();
});

console.log(`[${MODULE_ID}] Chat styling module loaded`);
