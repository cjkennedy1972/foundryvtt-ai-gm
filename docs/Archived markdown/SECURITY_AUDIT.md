# Security Audit — foundryvtt-ai-gm (Pre-Session)

Re-verified against current source on 2026-07-03. Several findings were already fixed by later commits (Phase 4/5 combat automation, prior audit remediation); one real bug (TTS silent failure) was fixed in this pass; two findings couldn't be reproduced against the current code.

## CRITICAL: execute_js exposed to LLM — ✅ Already Fixed
- **Location:** `actions/schemas.py` line 525, `actions/executors.py::execute_execute_js`
- **Status:** `execute_execute_js` is gated behind `settings.allow_execute_js` (default `False`, `config.py`). When disabled, the LLM-invoked action returns an error and never reaches `foundry.execute_js()`. The internal `client.execute_js()` calls used throughout the codebase (attacks, scene setup, etc.) go through named helper scripts, not the LLM-controlled arbitrary-code path, so they're unaffected by (and don't need) this gate.

## HIGH: SkillCheck always rejected (inverted agency) — ✅ Already Fixed
- **Location:** `actions/executors.py::execute_skill_check` (~line 529)
- **Status:** Already checks `has_player_owner` via `_player_actor_name()` — only player-owned actors get deferred to the player (a chat prompt to roll their own dice); NPCs/monsters still auto-roll through `request_skill_check`.

## HIGH: PlaceWalls coordinate swapping (x↔y) — Not reproduced
- **Location:** `actions/executors.py::execute_place_walls`, `campaign/orchestrator.py::_scene_setup_to_canvas`
- **Status:** Reviewed both the executor and the grid→pixel conversion path. Coordinates are consistently unpacked and reassembled in `[x0, y0, x1, y1]` order with no swap. No evidence of this bug in the current code — leaving as-is rather than "fixing" a swap that doesn't exist.

## MEDIUM: TTS silently fails (dead code path) — ✅ Fixed
- **Location:** `tts/playback.py::narrate()` / `speak()`
- **Issue confirmed:** `if url: ... ` had no `else` — when `_tts_service.narrate()`/`speak()` returned `None` (e.g. empty text after markdown-stripping, or any upstream failure not otherwise logged at the call site), playback was silently skipped with no warning at this layer.
- **Fix applied:** Added an `else: logger.warning(...)` branch in both `narrate()` and `speak()` so a missing audio URL is always logged, even though `tts/service.py::_generate()` already logs the underlying HTTP/generation error separately.

## MEDIUM: Spell slot leak (no recovery on end_encounter) — Not reproduced
- **Location:** searched `combat/loop.py`, `foundry/scripts.py::end_combat()`, `actions/executors.py`
- **Status:** `end_combat()` only deletes the Foundry `Combat` document — it never touches actor spell-slot resources. Spell slots are consumed via `FoundryClient.use_spell_slot()` → the relay's `use-spell-slot` message, which mutates the actor's own Foundry data directly; there is no separate in-engine copy of slot counts and no restore/reset code path found anywhere. Could not reproduce this bug against current code.

## MEDIUM: Camera fire check fires twice (doubling damage) — Not reproduced
- **Location:** audit cited `foundry/chat_listener.py` ~line 1160-1200
- **Status:** That range is `_run_proactive_action`/session-start scene bootstrapping — unrelated to combat or camera. No "camera" code exists anywhere in `ai-engine/` (grepped the whole tree). Combat automation (`attack_with_item`, `resolve_item_attack`) was substantially rewritten in the most recent commits (real midi-qol Activities), which may have already superseded whatever produced this symptom. Could not reproduce; no fix applied.

---

## Outcome

| Finding | Status |
|---|---|
| execute_js exposed to LLM | Already fixed (config gate) |
| SkillCheck inverted agency | Already fixed (has_player_owner check) |
| PlaceWalls x/y swap | Not reproduced — no fix needed |
| TTS silent failure | Fixed this pass |
| Spell slot leak | Not reproduced — no fix needed |
| Camera double-fire | Not reproduced — no fix applied, flag if it recurs with a live repro |
