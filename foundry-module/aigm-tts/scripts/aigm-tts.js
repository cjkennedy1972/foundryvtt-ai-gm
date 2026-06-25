/**
 * AI GM — Browser TTS
 *
 * Plays narration / NPC dialogue aloud on every connected client using the
 * browser's built-in Web Speech API (window.speechSynthesis). No external TTS
 * server is needed.
 *
 * The AI GM engine triggers playback through the foundry-rest-api relay's
 * execute-js, calling the API this module exposes on:
 *     game.modules.get("aigm-tts").api.speakAll(payload)
 *
 * payload = {
 *   text:    string,          // what to say (required)
 *   gender:  "male"|"female", // voice-selection hint (optional)
 *   rate:    number,          // 0.1–10, default 1.0 (optional)
 *   pitch:   number,          // 0–2,    default 1.0 (optional)
 *   volume:  number,          // 0–1,    default 0.8 (optional)
 *   lang:    string,          // BCP-47, default "en-US" (optional)
 *   voiceURI:string,          // exact browser voice URI to force (optional)
 *   interrupt:boolean,        // cancel current speech first (default true)
 * }
 */

const MODULE_ID = "aigm-tts";

// Cache the resolved voice list. getVoices() is async-populated in some
// browsers, so we refresh it when the engine signals availability.
let _voices = [];

function _refreshVoices() {
  try {
    _voices = window.speechSynthesis?.getVoices?.() ?? [];
  } catch (e) {
    _voices = [];
  }
  return _voices;
}

// Heuristic gendered-voice matcher over the platform voice list.
const _FEMALE_HINT = /(female|woman|samantha|victoria|karen|moira|tessa|fiona|serena|allison|ava|susan|zira|hazel|catherine|aria|jenny|sonia|libby)/i;
const _MALE_HINT = /(male|man|daniel|alex|fred|tom|oliver|george|james|guy|davis|ryan|brian|arthur|thomas|aaron)/i;

function _pickVoice(payload) {
  const voices = _voices.length ? _voices : _refreshVoices();
  if (!voices.length) return null;

  // 1) Exact voice forced by the engine.
  if (payload.voiceURI) {
    const exact = voices.find((v) => v.voiceURI === payload.voiceURI);
    if (exact) return exact;
  }

  const lang = (payload.lang || "en").toLowerCase().slice(0, 2);
  const langVoices = voices.filter((v) => (v.lang || "").toLowerCase().startsWith(lang));
  const pool = langVoices.length ? langVoices : voices;

  // 2) Gender hint.
  if (payload.gender === "female") {
    const f = pool.find((v) => _FEMALE_HINT.test(v.name)) || pool.find((v) => !_MALE_HINT.test(v.name));
    if (f) return f;
  } else if (payload.gender === "male") {
    const m = pool.find((v) => _MALE_HINT.test(v.name)) || pool.find((v) => !_FEMALE_HINT.test(v.name));
    if (m) return m;
  }

  // 3) Default for the language.
  return pool.find((v) => v.default) || pool[0] || null;
}

function _speak(payload) {
  try {
    if (!payload || !payload.text) return;
    const synth = window.speechSynthesis;
    if (!synth) {
      console.warn(`[${MODULE_ID}] speechSynthesis unavailable in this browser`);
      return;
    }

    if (payload.interrupt !== false) {
      try { synth.cancel(); } catch (e) { /* ignore */ }
    }

    const u = new SpeechSynthesisUtterance(String(payload.text));
    u.rate = typeof payload.rate === "number" ? payload.rate : 1.0;
    u.pitch = typeof payload.pitch === "number" ? payload.pitch : 1.0;
    u.volume = typeof payload.volume === "number" ? payload.volume : 0.8;
    u.lang = payload.lang || "en-US";

    const voice = _pickVoice(payload);
    if (voice) u.voice = voice;

    synth.speak(u);
  } catch (e) {
    console.error(`[${MODULE_ID}] speak failed`, e);
  }
}

function _stop() {
  try { window.speechSynthesis?.cancel?.(); } catch (e) { /* ignore */ }
}

Hooks.once("init", () => {
  // Populate the voice list as early as possible; some browsers fire this late.
  _refreshVoices();
  if (window.speechSynthesis) {
    window.speechSynthesis.onvoiceschanged = _refreshVoices;
  }
});

Hooks.once("socketlib.ready", () => {
  const socket = socketlib.registerModule(MODULE_ID);
  socket.register("speak", _speak);
  socket.register("stop", _stop);

  const mod = game.modules.get(MODULE_ID);
  mod.api = {
    /** Speak on every connected client (including this one). */
    speakAll: (payload) => socket.executeForEveryone("speak", payload),
    /** Speak only on the local client. */
    speakLocal: (payload) => _speak(payload),
    /** Cancel any in-progress speech everywhere. */
    stopAll: () => socket.executeForEveryone("stop"),
    /** Refresh and return the local browser voice list (for debugging). */
    voices: () => _refreshVoices().map((v) => ({ name: v.name, lang: v.lang, uri: v.voiceURI, default: v.default })),
  };

  console.log(`[${MODULE_ID}] ready — Web Speech API browser TTS active`);
});
