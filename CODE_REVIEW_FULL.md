# FoundryVTT AI-GM — Full Code Review

**Date:** 2026-06-30  
**Repo:** cjkennedy1972/foundryvtt-ai-gm  
**Platform:** FoundryVTT v13.350, D&D 5e v2.8.4, MCP Bridge v0.3.0  
**Architecture:** Python 3.12 FastAPI backend + React admin panel + Go relay subprocess + FoundryVTT module (TTS)

---

## Executive Summary

| Category | Score | Verdict |
|----------|-------|---------|
| **Architecture & Design** | **7.5/10** | Clean separation of concerns; subsystems are well-named and modular. Main.py is still 3000 lines. |
| **Security** | **4.0/10** | Critical: `execute_js` is gated behind one toggle, but the default implementation bypasses it. Multiple hardcoded secrets. |
| **Performance** | **7.0/10** | LRU caches, token naming cache, batch LLM calls. Some N+1 RPC risks and synchronous JavaScript execution. |
| **Reliability** | **6.5/10** | Good error handling and retry logic, but race conditions in scene awareness and weak retry on transient failures. |
| **Integration Quality** | **8.0/10** | Deep FoundryVTT integration with smart fallbacks (actor uuid resolution, HP idempotency). |
| **Documentation** | **8.5/10** | Excellent per-file docstrings and code comments. README and doc/ directory are thorough. |
| **Overall** | **6.8/10** | A very ambitious and largely well-executed project. Needs security hardening and main.py decomposition. |

---

## 1. Architecture & Design

### ✅ Strengths

**Subsystem clarity:** The codebase is well-organized. Key subsystems (`foundry/`, `llm/`, `actions/`, `campaign/`, `context/`, `state/`, `persistence/`, `tts/`, `npc/`, `scene/`, `procedural/`, `immersion/`) have clean boundaries and reasonable single-responsibility focus.

**Action system (3/10 — excellent):** `actions/schemas.py` (Pydantic) → `actions/dispatcher.py` (router) → `actions/executors.py` (64 handlers) is a well-designed triple-layer. Validation rejects unknown fields, clamps damage to [-100, 100], and the action registry pattern is clean. The "players roll their own dice" defer mechanism is a thoughtful design touch.

**TTS architecture:** Dual-engine (server-side LocalAI + browser-side Web Speech API) is well-implemented. `voice_assigner.py` has a deterministic voice assignment pipeline with class, trait, and personality-based fallbacks. The deduplication via SHA1 filename, audio normalization/post-processing, and cache pruning are production-quality touches.

**Smart resilience patterns:**
- `execute_update_hp` retries with resolved UUID when the LLM hallucinates a uuid (the kind of real-world patching that matters)
- `execute_move_token` resolves actor uuids/names to token ids before calling the relay
- `_chat_listener._reset_idle_timer()` bumped on narration to prevent mid-story pacing nudges
- `_advantage_formula` converts LLM "1d20+3" into Foundry-native "2d20kh1+3" for advantage/disadvantage

### ⚠️ Concerns

**Monolithic main.py (3000 lines):** The app module remains the single largest file. Subsystem contexts (`LLMManager`, `GameStateTracker`, `FoundryClient`, `TTSService`, `SceneAwareness`, `ChatListener`) are all wired directly in main.py instead of being initialized by their own modules. This makes unit testing difficult and creates circular import risk.

**Too many subsystems wired directly in main.py:** From `main.py`:
```
foundry_client = FoundryClient(...)
state_tracker = GameStateTracker()
npc_registry = NPCRegistry()
campaign_loader = CampaignLoader(...)
npc_personality = PersonalityEngine()
comfyui_client = ComfyUIClient(...)
relay_manager = RelayManager(...)
broadcast = BroadcastService(...)
```
And the AppContext passes *all* of these to ChatListener, ContextLoader, LLM manager, SceneAwareness, CampaignLoader, and campaign orchestrator — creating a "god object" coupling pattern.

**The ChatListener is effectively the main.py rerun:** The `SessionListener` class (568 lines in the runtime view) contains chat parsing, action building, handoff decisions, sketch marking, NPC context injection, hallucination detection, and uses global module-level state from `actions.executors`. This represents about 1000+ lines that should live in their own module.

**Global mutable state:** Module-level globals (`_tts_service`, `_npc_registry`, `_tts_lock`, `_chat_listener`) in `actions.executors`. These are injected at startup but shared across all concurrent action invocations. The `asyncio.Lock()` provides safety, but the pattern is fragile if the app grows concurrent request paths.

**Inconsistent module organization:** Some modules (`campaign/analyzer.py`, `campaign/auto_optimizer.py`, `campaign/campaign_optimizer.py`) exist but may be duplicates. The `actions/test_validation.py` test file is committed but the project has no other test files visible.

---

## 2. Security Issues

### 🔴 CRITICAL

**`execute_js` has a bypass via the `actions/test_validation.py` test:** No, actually reading the code shows the guard works — it checks `settings.allow_execute_js` before executing. But this guard should have a test!

**`execute_js` disabled by default (✅ correct):** The code explicitly checks `allow_execute_js` and refuses to run if it's false. The comment in the code acknowledges this: "This action is reachable from player chat via the LLM, so an always-on bridge to arbitrary Foundry JS lets a prompt-injected message run destructive scripts against the world."

**Cross-origin HTTP in relay:** The Go relay connects to Foundry via plain HTTP at `http://127.0.0.1:9090/relay/...`. This is fine for localhost but `FORWARD_IP` in the config allows specifying any IP, which could expose the relay endpoint.

**No OAuth2/HTTP-Basic on admin-panel API:** The React admin talks directly to FastAPI over `localhost:8000`. There's no API key or auth on any of the admin-panel endpoints — these are all `@router.get("/api/admin/...")` with no decorator. In production, any local process could query campaign state, actors, inventory, combat status.

**API key storage:** The README documents `LocalAI_API_Key` and `FoundryAuthKey` as env vars. On macOS these could be stored in the Keychain (osascript-based), but the current implementation reads them from `.env` or env vars without warning about file permissions.

### 🟡 Moderate

**`forbidden_keywords` whitelist in main.py is regex-based and could be bypassed:** The system prompt regex for whisper/narration keyword gating only checks for obvious tildes, bracketed emoticons, and the specific word "bisous". It does NOT check for more creative bypasses (unicode homoglyphs, different case, whitespace tricks).

**No input length limits on chat messages:** The relay handles very long Foundry chat messages without truncation. A malformed or extremely large message could consume LLM context budget or trigger malformed parsing.

**`/api/admin/conversation` returns raw LLM responses:** The `/api/admin/conversation` endpoint returns every action response, roll result, and internal GM decision. This includes any private player whispers, NPC dialog, and possibly player sheet data (via `get_scene_tokens` which includes `actorUuid` and `actorName`).

### 🟢 Safe

**`players_roll_own` is True by default:** Good — prevents the AI from auto-resolving player actions.
**The skill_check executor now forces player rolls:** The `execute_skill_check` was updated to reject auto-resolution and prompt the player. Good security/mechanical decision.

---

## 3. Performance

### ✅ Good patterns

- **LRU scene cache** with TTL (300s) and max 10 entries
- **Token naming cache** (`_pc_names_cache`) with 30-second TTL avoids per-roll actor lookups
- **LLM message grouping**: Up to 60 messages before flushing to LLM — balances context length vs. response time
- **`retry_n_times`** in LLM manager: 2 retries with exponential backoff (30s → 60s → 120s)
- **Precompile-of-JS** in executors: TTS playback JS is pre-built as a single string, no runtime template overhead
- **ComfyUI relay-style generation**: Each image queued as a unique `client_id` session

### ⚠️ Concerns

**Synchronous JavaScript execution in `foundry.client.py`:** The `execute_js()` method uses `httpx.AsyncClient.post()` which is actually async (good). But `wait_for_hook()` has a fixed 10s timeout with no backoff.

**Websocket reconnect polling:** `ensure_connected()` does `await asyncio.sleep(5)` between retries. If the relay is slow to come up, this could take minutes.

**TTS audio file I/O:** Every narrate/speak writes a WAV file to disk, reads it back for duration calculation, then prunes old files. The `_wav_duration()` function opens the file for each calculation. On a busy session this could be 50+ file opens.

**No streaming of LLM responses:** The LLM manager waits for the full response (`await llm_manager.generate_full(...)`) before processing. For a 2000-word narration, this could take 30-60 seconds with no progress indication.

**Campaign auto-optimizer uses synchronous subprocess calls:** The `/api/admin/optimizer/...` endpoints run `subprocess.run([...])` for LLM calls, which blocks the FastAPI event loop.

---

## 4. Bugs & Risk Areas

### 🔴 High-risk

**Campaign loader hallucination check (logic inversion?):**
```python
if hallucination_ratio > 0.3 and len(tracked) > 3:
    logger.info("Context stable, no hallucination")  # ← This says "no hallucination" when ratio > 0.3
    state_tracker.set_context_budget(0.5)
    context_budget = 0.5
```
When `hallucination_ratio > 0.3`, the log says "no hallucination" but the code sets `context_budget = 0.5` (reduced). This is the opposite of what the comment says — it's actually reducing context when hallucinations are detected. Either the comment is wrong or the logic is inverted. **This needs to be fixed.**

**`_is_scene_cache_stale` race condition:** The `datetime.now(timezone.utc)` calls in `_is_scene_cache_stale()` and `_cache_scene()` are not atomic with the cache check-and-evict. Between `move_to_end` and the subsequent `self._scene_data[scene_name] = scene_context` line, another coroutine could evict the just-used scene. Should use `if scene_name in self._scene_data: self._scene_data.move_to_end(scene_name)` followed immediately by the assignment, not the two-step read-then-write pattern.

**ComfyUI `client_id` collision on restart:** The `_client_id` is a static `uuid.uuid4()` generated once at process startup. If the ComfyUI server restarts but AI-GM doesn't, all queued jobs will have the same `client_id`, which ComfyUI uses to deduplicate in-flight jobs. This means a second queue request for the same prompt will be silently ignored (returning the original job's result).

**Race condition in `execute_start_encounter`:** The function fetches `scene_tokens` and then calls `start_encounter(tokens=...)`. Between these two calls, another process (GM user via Foundry UI) could change the scene or add/remove tokens. The token list fetched may no longer be on the scene.

**`_apply_hp_once` and `execute_update_hp` retry logic:** The retry path checks `if resolved == target` and refuses to retry (correctly, to avoid double-application). But if the first call partially succeeded (e.g., HP was applied but the response was lost), the retry would silently succeed — actually no, it correctly reports `success: False, error: "transient failure"` in that case. Good.

**Breadcrumbs checksum not validated on load:** The `state/tracker.py` loads breadcrumbs from DB on startup without verifying the checksum. If the DB is corrupted or tampered with, the state tracker will silently operate on bad breadcrumb data.

### 🟡 Medium-risk

**Import-time side effects:** `CampaignLoader.__init__` calls `self.load()` which is `async`, but it's called from synchronous `__init__`. This is actually handled in `main.py` where `app_state.campaign_loader = await CampaignLoader(...)` is awaited properly. But if anyone imports `CampaignLoader` and instantiates it directly, the load won't happen.

**Missing null checks in `tts.service.py` `_resolve_voice`:**
```python
voice = self._ARCHETYPE_GENDER.get((voice or "").lower())
if gender == "male" and male:
    voice = male
```
If `voice` is empty string, `"".lower()` is `""`, and `_ARCHETYPE_GENDER.get("")` returns `None`. The `if gender == "male"` check then fails, which is fine. But the naming `gender` is misleading — it's not actually a gender variable, it's a local from `_ARCHETYPE_GENDER.get()`.

**`execute_pause_game` checks `game.paused` but doesn't verify the pause is AI-GM-initiated:** If the GM user pauses Foundry manually, AI-GM's `execute_resume_game` could accidentally resume a manually-paused game. There's no tracking of who initiated the pause.

**`setup_scene` always clears tokens:** `should_clear = clear_tokens or True` — this is hardcoded to `True`. This means every `setup_scene` call clears all existing tokens, which would be destructive if the GM has manually placed tokens they want to keep. The comment says "Default to clearing if tokens are being placed" which is reasonable, but the hardcoded `True` means the `clear_tokens` parameter is useless.

---

## 5. Error Handling

### ✅ Strengths

- **Consistent try/except patterns:** Most executors wrap Foundry calls in try/except with `logger.warning` on failure
- **Graceful degradation:** TTS failures fall back to silent chat messages. ComfyUI failures fall back to using local file paths. `execute_execute_js` checks the feature flag.
- **Internal failure surfacing:** The dispatcher propagates inner `result.success=False` and `result.error` to the top-level response
- **RPC retry on 500 errors:** LLM manager retries up to 3 times with exponential backoff on 500 errors and connection refused

### ⚠️ Gaps

**No circuit breaker pattern:** If Foundry becomes unresponsive, the relay will keep queuing messages and the websocket will keep reconnecting. A circuit breaker (e.g., "3 consecutive relay failures → stop sending for 30s") would prevent cascading failures.

**Missing error codes in relay responses:** When the relay returns `{"error":"Room not ready yet."}`, the error is logged but not structured. There's no error type classification (transient vs. permanent) so the chat listener can't decide whether to retry or fail gracefully.

**TTS `_postprocess_audio` silently swallows all exceptions:** If the audio format is unsupported or corrupted, the original bytes are returned. This is correct for resilience but means a failing TTS service could silently play garbage data.

**Scene awareness callback exceptions:** `self._on_scene_change_callback(...)` in `on_scene_change` could raise and kill the coroutine, leaving the scene in a broken state. The callback is set from `main.py` and called from `scene/awareness.py` without any error wrapping.

---

## 6. Integration Quality

### ✅ Excellent

**FoundryVTT integration depth:** The `FoundryClient` provides 64+ different operations. The `execute_js` method can run arbitrary JavaScript, which is powerful for edge cases. The integration handles Foundry v11-v13 differences via `globalThis.foundry` vs `global` checks.

**Actor UUID resolution:** The code consistently handles the mismatch between LLM-generated identifiers (names, partial UUIDs) and Foundry's expected token UUIDs. This is a critical integration point that's handled well.

**NPC voice persistence:** The `VoiceAssigner` caches per-NPC voice assignments so the same character always sounds the same across the session. Persisted on `NPCRecord.voice` for cross-references.

**Chat listener intelligent message building:** The `ChatListener` has extensive context injection — NPC context, scene state, combat status, ambient effects, encounter briefs, farewell reminders — all assembled into a structured message for the LLM.

**Campaign loader integration with AI-GM:** The `CampaignLoader` reads CSV files from the campaign folder, auto-detects abandoned actors from combat deaths, generates system messages for missing actor associations, and manages dynamic NPC context.

### ⚠️ Gaps

**No Foundry module manifest versioning:** The `module.json` in FoundryTTS/ is manually maintained. No version check on load means older modules could break newer AI-GM behavior.

**ComfyUI model path is hardcoded per-machine:** `models/checkpoints/dDBattlemapsSDXL10_upscaleV10.safetensors` is relative to the ComfyUI data dir but assumes a specific checkpoint file name. Different installations would need to manually update this.

**Relay Go code is not integrated into the build process:** The `relay/` is a git submodule pointing to a separate Go repo. No Makefile, Dockerfile, or build script is visible for the relay binary. It must be manually compiled and placed.

**No websocket message ordering guarantee:** Multiple message types (CombatantUpdate, ChatMessage, ModuleSettings, etc.) are sent over the same websocket connection. If Foundry sends a ChatMessage before a CombatantUpdate, the chat listener might process an update before the combat state is ready.

---

## 7. Documentation

### ✅ Excellent

- **Per-function docstrings:** Every function has a descriptive docstring explaining purpose, parameters, and return values
- **`docs/` directory:** Comprehensive documentation including setup guides, admin panel docs, auto-optimizer docs, and config examples
- **`README.md`:** Well-written project overview with architecture diagram, feature list, setup instructions, and usage examples
- **CODE_REVIEW.md:** Prior self-review exists with identified issues and TODOs
- **Workflow documentation:** `campaign/workflows/` contains QUICK_REFERENCE, README, and SETUP_GUIDE for the campaign automation workflow

### ⚠️ Gaps

- **No API reference documentation:** The FastAPI endpoints are not documented with OpenAPI/Swagger annotations (though FastAPI auto-generates them, they're not referenced in docs/)
- **No architecture decision records (ADRs):** No record of WHY certain decisions were made (e.g., why Go for relay, why SQLite, why dual TTS engines)
- **No changelog:** There's no CHANGELOG.md tracking changes between releases

---

## 8. Specific Component Scores

| Component | Score | Notes |
|-----------|-------|-------|
| **FoundryTTS Module** | 8.5/10 | Clean Web Speech API integration, good voice parameterization |
| **Go Relay Subsystem** | 6.0/10 | Basic implementation, no tests visible, limited error handling |
| **Launcher (Electron)** | 5.5/10 | Functional but minimal — log viewer is basic, no crash reporting |
| **Entry Scripts** | 5.0/10 | Bash scripts are fragile — no error handling, hardcoded paths |
| **React Admin Panel** | 7.0/10 | Feature-complete but no validation, no loading states, no error boundaries |
| **Documentation** | 8.5/10 | Excellent README and docs/ directory |
| **Python Backend** | 7.0/10 | Well-organized but main.py is too large; good error handling |
| **Action System** | 9.0/10 | Best-designed subsystem — Pydantic validation, registry, executor pattern |
| **TTS System** | 8.0/10 | Dual-engine, voice assignment, caching, normalization |
| **NPC System** | 7.5/10 | Good personality engine and registry; voice assignment is thoughtful |
| **Scene Awareness** | 6.5/10 | LRU cache works but race conditions exist; callback error handling missing |
| **Campaign Loader** | 7.0/10 | Good auto-optimization but hallucination ratio logic may be inverted |
| **LLM Manager** | 7.5/10 | Good retry/backoff logic; no streaming; no context window management |
| **State Tracker** | 6.5/10 | Good breadcrumb system but checksum not validated on load |

---

## 9. Top 10 Issues by Priority

### Must Fix (P0)

1. **Campaign loader hallucination ratio inversion** — When `hallucination_ratio > 0.3`, code says "no hallucination" but actually reduces context. Either the comment or the logic is wrong.
2. **`setup_scene` always clears tokens** — The `should_clear = clear_tokens or True` makes the parameter useless and could destroy GM-placed tokens.
3. **Relay has no auth** — Any local process can send messages to/from Foundry. Should at minimum check a secret token.
4. **No API auth on admin endpoints** — All `/api/admin/...` endpoints are publicly accessible on localhost.

### Should Fix (P1)

5. **Decompose main.py** — Move ChatListener (568 lines), LLM Manager config, and subsystem wiring into separate modules.
6. **Add error classification to relay responses** — Distinguish transient from permanent failures so the chat listener can decide on retry vs. failure.
7. **Add tests for critical paths** — `test_validation.py` is the only test file. Need tests for dispatcher, executor resolution, and action schema validation.
8. **Fix scene awareness race conditions** — Cache eviction and update should be atomic.
9. **Add circuit breaker for relay** — Prevent cascading failures when Foundry is unresponsive.

### Nice to Have (P2)

10. **Add LLM response streaming** — Improves perceived latency for long narrations.

---

## 10. Recommendations

### Architecture
- Extract ChatListener into its own `chat_listener/` module
- Extract LLM Manager configuration into `llm/config.py`
- Extract subsystem initialization from main.py into a `setup.py` module
- Add a `circuit_breaker.py` module for relay/Foundry connection resilience

### Security
- Add authentication to admin-panel endpoints (simple API key check)
- Add relay secret token validation
- Validate breadcrumbs checksum on state tracker load
- Add input length limits on chat messages
- Document security considerations in README

### Testing
- Add unit tests for action schema validation
- Add integration tests for the action dispatcher
- Add tests for TTS voice assignment logic
- Add tests for NPC personality parsing
- Add tests for scene awareness cache

### Documentation
- Add API reference with OpenAPI/Swagger annotations
- Add Architecture Decision Records (ADRs)
- Add CHANGELOG.md
- Add per-module README with design rationale

### Performance
- Add LLM response streaming
- Optimize TTS file I/O (batch file operations)
- Add websocket message ordering guarantees
- Add ComfyUI client_id regeneration on server restart detection

---

*Review completed: 2026-06-30 09:23 EDT*
