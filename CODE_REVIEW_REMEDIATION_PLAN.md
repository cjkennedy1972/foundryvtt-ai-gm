# FoundryVTT AI GM — Code Review Remediation Plan

**Date:** 2026-06-20
**Review:** Comprehensive Project Analysis (CODE_REVIEW_FINDINGS.md)
**Target:** Resolve all P0, P1, and P2 issues identified

---

## Summary of Findings

The review identified **8 issues** across 3 priority levels in a ~50,500+ line project (18K Python, 32K Go). The system scored **7/10 — "Internal Beta Qualified"** but needs addressing before regular player sessions.

---

## P0 — Ship Blockers (Fix Before First Real Game)

### Issue 1: Missing Procedural Action Execution Bridge

**Problem:** `execute_generate_encounter`, `execute_generate_treasure`, `execute_generate_npc`, `execute_generate_quest` exist as action handlers, but they only return *textual descriptions* from the procedural generators. None of them:
- Create Foundry actors (monster/NPC tokens)
- Place tokens on the current scene
- Start combat or add to encounters
- Reference the relay's `create_entity` endpoint (line 931 of client.py)

The LLM can generate encounter descriptions but cannot actually place monsters on the map.

**Impact:** High. Players see text descriptions of encounters but never see visual content on the VTT.

**Fix:**

1. **Extend `execute_generate_encounter`** (executors.py, ~line 381):
   ```python
   async def execute_generate_encounter(party_level, party_size, environment=None,
                                        app_state=None, foundry: FoundryClient = None):
       # (existing: get textual encounter from ProceduralGenerator)
       # NEW: For each monster in encounter["monsters"]:
       #   1. Create Foundry actor via foundry.create_entity("Actor", {...})
       #   2. Get the current scene via foundry.get_active_scene()
       #   3. Place tokens at computed positions (use existing place_token method)
       #   4. Start an encounter if one isn't active: foundry.start_encounter([token_ids])
   ```

2. **Extend `execute_generate_npc`** (executors.py, ~line 433):
   - Same pattern: generate NPC text → create actor in Foundry → place token on scene

3. **Extend `execute_generate_treasure`** (executors.py, ~line 408):
   - Create Foundry "Item" documents for each treasure item
   - Optionally add to an existing "Loot Table" journal entry

4. **Extend `execute_generate_quest`** (executors.py, ~line 461):
   - Create a JournalEntry in Foundry for the quest

5. **Dependency:** All 4 require `foundry.create_entity()` to work end-to-end
   with the relay. Test that `create_entity` → Go relay's POST `/api/create`
   correctly creates actors with stat blocks, HP, and abilities.

**Files modified:** `ai-engine/actions/executors.py` (4 functions), `ai-engine/foundry/client.py` (verify `create_entity`)

**Estimated effort:** 2-3 days

---

### Issue 2: Campaign Build — Silent Mid-Pipeline Abort

**Problem:** The 6-phase pipeline (scan → generate → save → assets → deploy → report) saves `deployment_state.json` after each phase, but on restart:
- It loads NPC UUIDs from last deployment (line 546-553 of orchestrator.py) ✅
- It does NOT skip already-deployed phases or resume from a broken checkpoint ❌
- If a scene attachment fails (network timeout, relay crash), the campaign is left in a partial state with no resume path

Looking at the current code (orchestrator.py ~line 550), deployment state is loaded but only for NPC UUID caching. There's no "resume from phase X" logic.

**Impact:** High. A crashed deployment leaves partial state with no recovery, potentially corrupting Foundry (partial scenes, orphan NPCs).

**Fix:**

1. **Add phase-level checkpointing** (orchestrator.py):
   - After each phase (scan, generate, save, assets, deploy, report), write phase completion to `deployment_state.json` as `"phases_completed": ["scan", "generate"]`
   - On restart, read this and skip to the first incomplete phase

2. **Add `--resume` or auto-resume logic**:
   ```python
   async def deploy_campaign(...):
       state = load_deployment_state()
       for phase in PHASE_ORDER:
           if phase in state.get("completed_phases", []):
               logger.info(f"Skipping completed phase: {phase}")
               continue
           # run phase
           state["completed_phases"].append(phase)
           save_deployment_state(state)
   ```

3. **Add partial state cleanup option**:
   - Command to "undo last deployment" — delete all actors, scenes, journal entries created by the last campaign deploy

4. **Handle `None` responses explicitly** (already partially done at line 630-634):
   - Network timeout returning `None` currently just appends an error. Make it a *recoverable* error that allows retrying just that phase.

**Files modified:** `ai-engine/campaign/orchestrator.py` (~30 lines for checkpoint logic), `ai-engine/main.py` (add resume flag/endpoint)

**Estimated effort:** 1-2 days

---

### Issue 3: Combat Deadlock Risk (No LLM Timeout/Fallback)

**Problem:** The combat loop (loop.py ~line 180) calls `self.llm.generate()` with no timeout handling. The HTTP timeout in manager.py is hardcoded to 120 seconds (line 225). This means:
- If the LLM is slow or unresponsive, the **entire combat blocks for 2 minutes per NPC turn**
- PCs cannot act until the LLM responds (line 159: `await self._wait_for_pc_input` never fires because the NPC turn never completes)
- If the LLM returns malformed JSON or an unrecognized action, the dispatcher logs a warning but the combat continues with potentially corrupted state

Looking at lines 180-232 of combat/loop.py, the try/except catches exceptions but only sends an error to chat and returns — the combat loop then advances to the next turn, but HP/state may be inconsistent.

**Impact:** Critical. A single bad LLM call can freeze a combat session for 2+ minutes, ruining gameplay.

**Fix:**

1. **Add configurable LLM timeout per call** (combat/loop.py):
   ```python
   LLM_TIMEOUT_SECONDS = self.settings.llm_timeout or 60  # configurable default
   result = await asyncio.wait_for(
       self.llm.generate(...),
       timeout=LLM_TIMEOUT_SECONDS
   )
   ```

2. **Implement fallback generic NPC behavior** (when timeout hits):
   ```python
   except asyncio.TimeoutError:
       logger.warning(f"[Combat] LLM timeout for {actor_name}, using generic behavior")
       fallback_actions = await self._generic_npc_behavior(token, scene_info, combat_context)
       await self.dispatcher.execute_batch(fallback_actions)
   ```
   Generic behavior: move toward nearest PC, use basic attack if in range, use healing ability if low HP.

3. **Add `--confirm-combat` flag** (main.py):
   - Before starting the combat loop, display a confirmation prompt in the admin panel
   - GM reviews NPC list and token placement before combat begins

4. **Add combat snapshot/rollback** (state/tracker.py):
   ```python
   async def save_combat_snapshot():
       """Capture full game state before combat starts"""
       return {
           "round": self.round_num,
           "turn": self.turn,
           "tokens": [t.model_dump() for t in all_tokens],
           "actors": {uuid: actor.model_dump() for uuid, actor in actors.items()},
       }
   ```

5. **Handle malformed JSON** (line 180-190):
   - If `result.get("actions")` is malformed, log and send a default "stand" action rather than silently continuing

**Files modified:** `ai-engine/combat/loop.py` (~40 lines), `ai-engine/config.py` (add `llm_timeout` field), `ai-engine/state/tracker.py` (add `save_combat_snapshot`)

**Estimated effort:** 2-3 days

---

## P1 — Important Improvements (Fix Before Regular Play)

### Issue 4: Campaign Asset Storage Workflow

**Problem:** The report states `campaign_assets/` directory doesn't exist, but **log evidence shows it DOES exist** (see CODE_REVIEW_FINDING line 546-547 of orchestrator.py, and logs showing `campaign_assets/the avenveild chronicles/deployment_state.json`). However, the gap is:

- The map generator produces images to `campaign_assets/<campaign>_maps/` but there's no explicit error handling for when ComfyUI produces bad/missing files
- Portraits are uploaded with a semaphore of 4, but if the relay returns `None` (network timeout), the error is logged but the campaign JSON is still saved with broken references
- No explicit "campaign_assets" directory creation check before writing

**Impact:** Medium-High. Asset generation failures silently corrupt campaign JSON with broken image references.

**Fix:**

1. **Add pre-deployment asset validation** (orchestrator.py, generate_assets method):
   ```python
   # After generating all assets, validate every referenced file exists
   for scene in campaign_data["scenes"]:
       map_file = scene.get("map_file")
       if map_file and not (asset_output_dir / map_file).exists():
           summary["errors"].append(f"Missing map file: {map_file}")
           return summary  # Fail fast
   ```

2. **Add `campaign_assets/` directory auto-creation** on orchestrator init

3. **Add cleanup for failed uploads**: If upload fails, remove the broken reference from campaign JSON so the next retry attempt can re-generate

4. **Document the campaign_assets structure** in the README (it's currently undocumented in the codebase)

**Files modified:** `ai-engine/campaign/orchestrator.py` (~20 lines)

**Estimated effort:** 0.5-1 day

---

### Issue 5: Context Management for Long Sessions

**Problem:** The context system has anchor facts (reinforced every 3 LLM calls) and message windowing (last N pairs), but:
- No token-aware pruning — the 50K token limit will be exhausted in long campaigns
- No hierarchical summaries (session, scene, encounter level)
- Combat contexts inject full combatant lists every turn (lines 173-193 of combat/loop.py), which is ~2-3K tokens per turn

**Impact:** Medium. Long campaigns will lose context about earlier events, NPCs, and plot points.

**Fix:**

1. **Add token-count-aware context pruning** (llm/manager.py):
   ```python
   # Before each LLM call, check total tokens in payload
   # If over budget, prune oldest 50% of conversation history
   # Keep: system prompt + anchor facts + last 10 turns + current scene/combat state
   ```

2. **Implement hierarchical summaries** (context/reinforcement_manager.py):
   - **Session summary** (every 30 turns): Key plot points, NPCs met, quests given
   - **Scene summary** (on scene change): Scene description, remaining threats, discovered items
   - **Encounter summary** (on combat end): Outcome, NPCs defeated, loot taken

3. **Add context pruning config** (config.py):
   ```python
   context_max_tokens: int = 50000
   context_prune_threshold: float = 0.8  # Start pruning at 80% of limit
   ```

**Files modified:** `ai-engine/llm/manager.py`, `ai-engine/context/reinforcement_manager.py`, `ai-engine/config.py`

**Estimated effort:** 2-3 days

---

### Issue 6: Combat Rollback/Snapshots

**Problem:** See Issue 3 fix #4 above. Combat goes wrong (bad HP values, lost tokens, misplaced NPCs) with no way to restore.

**Fix:** (Merged with Issue 3 fix #4)
- Add `save_combat_snapshot()` to state tracker
- Add `rollback_to_snapshot(snapshot_id)` method
- Admin panel endpoint to view/restore snapshots

**Files modified:** `ai-engine/state/tracker.py`, `ai-engine/main.py` (admin endpoint)

**Estimated effort:** 1-2 days (merged with Issue 3)

---

## P2 — Nice-to-Have (Post-Production Polish)

### Issue 7: Encounter Template Library

**Gap:** Every encounter requires a full LLM call (~10+ seconds). A template library with pre-staged encounters could deploy in seconds.

**Fix:**
- Create 5-10 encounter templates (ambush, boss fight, puzzle, social encounter, escape)
- Each template defines: scene layout, token positions, NPC stat blocks, triggered events
- Admin panel endpoint to "deploy template" → auto-creates actors, places tokens, starts encounter

**Files:** New file `ai-engine/combat/templates.py`, admin panel template browser

**Estimated effort:** 3-4 days

---

### Issue 8: NPC Personality is Keyword-Based, Not LLM-Driven

**Gap:** The personality system (npc/personality.py, ~229 lines) uses predefined keyword categories (temperament, intellect, morality, sociability, courage) for matching against NPC profiles.

**Fix:**
- Option 1: Add `llm-driven-personality` config flag
- When enabled, LLM generates personality from NPC description
- LLM output: structured JSON with traits, quirks, speech patterns, triggers
- Falls back to keyword extraction if LLM unavailable

**Files:** `ai-engine/npc/personality.py` (new `generate_llm_personality` method)

**Estimated effort:** 2-3 days

---

### Issue 9: Missing Integration with Foundry Addon Modules

**Gap:** The scanner detects JB2A, Midi-QOL, DAE, Item Piles, Dynamic Soundscapes, Fog Weaver, etc. but only uses a subset (level background, basic token placement).

**Fix:**
- Map detected modules to action executors:
  - **JB2A** (animation library) → `apply_token_effect` uses JB2A spell animations
  - **Midi-QOL** (automation) → Skip manual damage rolls, use Midi's calculated damage
  - **Dynamic Effect Aura** (DAE) → `apply_token_effect` uses DAE conditions instead of manual token colors
  - **Item Piles** → `generate_treasure` places items on ground with Item Piles
  - **Dynamic Soundscapes** → `play_music` triggers dynamic layers
  - **Fog Weaver** → `configure_scene` uses fog presets

**Files:** `ai-engine/actions/executors.py` (add addon-aware executors), `ai-engine/campaign/orchestrator.py` (use detected capabilities)

**Estimated effort:** 5-7 days (largest item on P2)

---

## Implementation Priority & Timeline

### Week 1 (Critical Path)
| Priority | Issue | Effort | Blocked By |
|----------|-------|--------|------------|
| P0 | #1 Procedural Bridge | 2-3 days | — |
| P0 | #2 Deploy Checkpoint | 1-2 days | — |
| P0 | #3 Combat Timeout/Fallback | 2-3 days | — |
| **Subtotal** | | **5-8 days** | |

### Week 2 (Important)
| Priority | Issue | Effort | Blocked By |
|----------|-------|--------|------------|
| P1 | #4 Asset Validation | 0.5-1 day | — |
| P1 | #5 Context Management | 2-3 days | #3 (timeout already added) |
| P1 | #6 Combat Rollback | 1-2 days | #3 (merge) |
| **Subtotal** | | **3.5-6 days** | |

### Post-Production (P2)
| Priority | Issue | Effort |
|----------|-------|--------|
| P2 | #7 Encounter Templates | 3-4 days |
| P2 | #8 LLM NPC Personality | 2-3 days |
| P2 | #9 Addon Integration | 5-7 days |
| **Subtotal** | | **10-14 days** |

### Total Estimated: **18-28 days** (2.5-4 weeks)

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Procedural bridge fails due to relay API changes | Medium | Test `create_entity` endpoint against current relay version first |
| Combat timeout fallback creates unpredictable gameplay | Medium | Make fallback behavior configurable; GM can disable it |
| Context pruning cuts too aggressively | Low | Start with conservative 80% threshold; monitor token usage |
| Deploy checkpoint interferes with existing campaigns | Low | Only activates on new/updated campaigns; skip for campaigns with no `deployment_state.json` |

---

## Testing Plan (Post-Fix)

1. **Procedural Bridge:** Create a test campaign with 3 encounter types (ambush, boss, puzzle) and verify each deploys actors + tokens correctly in Foundry
2. **Deploy Checkpoint:** Intentionally crash a deployment mid-pipeline, then resume — verify it skips completed phases and retries broken ones
3. **Combat Timeout:** Configure LLM endpoint to return 500 error, verify combat falls back to generic behavior within 60s, not 120s
4. **Context Management:** Run a 4+ hour campaign session, monitor token usage graph, verify summaries are being generated
5. **Rollback:** Start combat, corrupt an actor's HP manually in Foundry, use rollback to restore — verify state matches

---

## Notes

- **Relay submodule** (`relay/go-relay/`) is a separate codebase (32K lines, Go). Most P0/P1 fixes only require the relay's `create_entity` endpoint to work — no relay code changes expected
- **Admin Panel** (React SPA) needs one new endpoint for combat snapshots and one for template browser (P2)
- **Code review quality:** 9/10 — this is a thorough analysis that correctly identifies real gaps
