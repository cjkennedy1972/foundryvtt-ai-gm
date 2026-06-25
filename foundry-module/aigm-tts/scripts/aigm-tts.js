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
const _MALE_HINT = /(male|man|daniel|alex|tom|oliver|george|james|guy|davis|ryan|brian|arthur|thomas|aaron|rishi)/i;

// High-quality voice families to PREFER (natural/neural). Higher = better.
const _QUALITY_RANK = [
  /natural/i, /neural/i, /premium/i, /enhanced/i, /siri/i,   // best, if installed
  /google/i,                                                  // Chrome's Google voices — very good, no install
  /samantha|daniel|karen|moira|tessa|serena|allison|ava/i,    // decent built-in named voices
];

// Robotic / novelty voices to AVOID at all costs (macOS joke voices + low-q).
const _NOVELTY = /(albert|bad news|good news|bahh|bells|boing|bubbles|cellos|jester|junior|kathy|organ|ralph|superstar|trinoids|whisper|wobble|zarvox|eddy|flo|grandma|grandpa|reed|rocko|sandy|shelley|fred|deranged|hysterical|pipe organ|bahh)/i;

function _qualityScore(v) {
  for (let i = 0; i < _QUALITY_RANK.length; i++) {
    if (_QUALITY_RANK[i].test(v.name)) return _QUALITY_RANK.length - i;
  }
  return 0;
}

function _pickVoice(payload) {
  const voices = _voices.length ? _voices : _refreshVoices();
  if (!voices.length) return null;

  // 1) Exact voice forced by the engine (by URI or name).
  if (payload.voiceURI) {
    const exact = voices.find((v) => v.voiceURI === payload.voiceURI);
    if (exact) return exact;
  }
  if (payload.voiceName) {
    const named = voices.find((v) => v.name === payload.voiceName);
    if (named) return named;
  }

  const lang = (payload.lang || "en").toLowerCase().slice(0, 2);
  let pool = voices.filter((v) => (v.lang || "").toLowerCase().startsWith(lang));
  if (!pool.length) pool = voices.slice();

  // Drop novelty/robotic voices unless they're literally all we have.
  const nonNovelty = pool.filter((v) => !_NOVELTY.test(v.name));
  if (nonNovelty.length) pool = nonNovelty;

  // Gender filter (best-effort): keep voices matching the hint, but don't
  // discard everything if the hint can't be satisfied.
  if (payload.gender === "female") {
    const f = pool.filter((v) => _FEMALE_HINT.test(v.name) || !_MALE_HINT.test(v.name));
    if (f.length) pool = f;
  } else if (payload.gender === "male") {
    const m = pool.filter((v) => _MALE_HINT.test(v.name));
    if (m.length) pool = m;
  }

  // Rank by quality, preferring local (offline, no latency) on ties only when
  // quality is equal — Google voices are remote but worth the small latency.
  pool.sort((a, b) => {
    const qd = _qualityScore(b) - _qualityScore(a);
    if (qd !== 0) return qd;
    if (a.default !== b.default) return a.default ? -1 : 1;
    return 0;
  });

  return pool[0] || null;
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

// Play a pre-generated audio URL (e.g. from a server TTS model) locally on
// THIS client. Broadcast via socketlib so every player hears it — more
// reliable than AudioHelper's own socket arg when the caller is a muted
// headless GM session.
function _playUrl(payload) {
  try {
    if (!payload || !payload.url) return;
    const vol = typeof payload.volume === "number" ? payload.volume : 0.8;
    const AH = globalThis.foundry?.audio?.AudioHelper
      ?? (typeof AudioHelper !== "undefined" ? AudioHelper : null);
    if (AH) {
      AH.play({ src: payload.url, volume: vol, loop: false }, false);
      return;
    }
    const a = new Audio(payload.url);
    a.volume = vol;
    a.play().catch((e) => console.warn(`[${MODULE_ID}] audio play blocked`, e));
  } catch (e) {
    console.error(`[${MODULE_ID}] playUrl failed`, e);
  }
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
  socket.register("playUrl", _playUrl);

  const mod = game.modules.get(MODULE_ID);
  mod.api = {
    /** Speak on every connected client (including this one). */
    speakAll: (payload) => socket.executeForEveryone("speak", payload),
    /** Speak only on the local client. */
    speakLocal: (payload) => _speak(payload),
    /** Play a pre-generated audio URL on every client (server-TTS path). */
    playUrlAll: (payload) => socket.executeForEveryone("playUrl", payload),
    /** Cancel any in-progress speech everywhere. */
    stopAll: () => socket.executeForEveryone("stop"),
    /** Refresh and return the local browser voice list (for debugging). */
    voices: () => _refreshVoices().map((v) => ({ name: v.name, lang: v.lang, uri: v.voiceURI, default: v.default })),
  };

  console.log(`[${MODULE_ID}] ready — Web Speech API browser TTS active`);
});
