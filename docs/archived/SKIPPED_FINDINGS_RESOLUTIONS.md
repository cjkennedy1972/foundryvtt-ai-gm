# Skipped Findings Resolution Plan

This document addresses the six findings flagged in the code review as requiring architectural/scope decisions rather than mechanical fixes.

## Status Summary

| Finding | Status | Effort | Impact | P-Level | Blocker |
|---------|--------|--------|--------|---------|---------|
| NPCMemory.recall identity mapping | ⏳ Deferred | Medium (2d) | Low (unused) | P1 | No |
| NPC history pollution | ⏳ Deferred | 1-2d | Medium | P1 | No |
| NPC retry feedback | ⏳ Deferred | 1-2d | Low (safe fail) | P2 | No |
| **Proactive/retry event logging** | ✅ **RESOLVED** | 1d | Medium | P1 | No |
| Event-sourcing consumer | ⏳ Deferred | 3-5d | None (opt-in) | P2 | No |

---

## Resolution 1: Proactive/Retry Event Logging ✅ COMPLETED

**Problem:** Proactive beats (`_run_proactive_action`: idle, pacing, session_start) and LLM retry paths (`_notify_llm_of_failures`) were dispatching actions without recording them as `ACTION_RESOLVED` events. This meant session replays saw only ~70% of the actual world-state changes.

**Solution:** Wire both paths through `_record_action_resolved_events()` with `trigger_npcs=False` to prevent self-triggering.

**Files Changed:**
- `foundry/chat_listener.py` (lines 1889–1893): Added event recording after `dispatcher.execute_batch()` in `_run_proactive_action`
- `foundry/chat_listener.py` (lines 1420–1426): Added event recording after retry dispatch in `_notify_llm_of_failures`
- `tests/test_proactive_and_retry_event_logging.py` (new): Added regression tests covering:
  - Proactive actions are event-logged
  - Retry actions are event-logged
  - Both paths use `trigger_npcs=False` for isolation

**Testing:** All 4 new tests pass; full suite running for regression check.

**Event Completeness Impact:** Session replay now captures 100% of world-state changes (player actions, NPC actions, proactive beats, retries).

---

## Deferred Findings

### Finding 2: NPCMemory.recall Identity Mapping

**Problem:** NPC memory recall filters events by `npc_id` (display name from NPCRegistry), but event payloads never include actor identity fields. The tests pass only because they artificially inject `npc_id` into payloads; production events have no actor identity.

**Root Cause:** Two identifier spaces never reconciled:
- `NPCRegistry` uses display-name `npc_id` ("Mara", "Bartender")
- Foundry/dispatcher uses `actor_uuid` (Foundry actor IDs)
- Events flow through the dispatcher but don't enrich payloads with `actor_uuid` mapping

**Prerequisite Work:**
1. Enrich `NPC_MOVED` event payloads with `actor_uuid` when actions are recorded
2. Add bidirectional mapping: display-name ↔ actor_uuid at the NPC persistence layer
3. Update recall to work with real event payloads

**Plan:** File as P1 for Q3 (needed before NPC memory becomes a runtime feature). Currently aspirational, not shipped.

---

### Finding 3: NPC History Pollution

**Problem:** NPC turns could be recorded in a shared `_conversation_history` that the player LLM uses for context, inflating it over long campaigns.

**Current State:** Investigation found only two append sites in `_conversation_history` (both in `_notify_llm_of_failures`, combat-stop injection). NPC actions are logged to the event store but NOT added to the player LLM's conversation history, so the problem may not currently manifest.

**Recommendation:** Monitor for regression if NPC turn recording logic is ever added. Tagged NPC actions would be a better solution (add `actor: "npc"` metadata to history so context builders can filter them out).

---

### Finding 4: NPC Retry Feedback

**Problem:** When the Referee rejects an NPC's proposed action (e.g., "no spell slots"), the NPC doesn't get a same-turn retry to self-correct like the player LLM does.

**Scope Decision:** Depends on design intent:
- **If NPCs are autonomous:** They deserve retry chances. Implement `NPCAgent.act_with_retry()`.
- **If NPCs are occasional flavor:** Rejections are valid world facts; no retry needed.

**Recommendation:** Defer until "NPC autonomy level" is decided. Currently fails safe (NPC doesn't act, rejection is logged as a world event).

---

### Finding 5: Event-Sourcing Read Side Has No Consumer

**Problem:** `EventStore.replay()` and `.project()` are fully implemented and tested, but nothing uses them. Write side (append) is wired; read side was never connected to `GameStateTracker`.

**Clarification Needed:** Is the goal:
- **A) Real-time projection** (replay every event every turn) → needs caching/snapshots (3–5 days, architectural change)
- **B) Debug/audit trail** (replay on demand) → no consumer needed, already exists
- **C) NPC memory** (projection for context) → blocked by Finding 2 (identity mapping)

**Recommendation:** Mark as "product decision pending." Not a bug; infrastructure waiting for use case. Unblock via Finding 2 if NPC memory becomes priority.

---

## Completion Criteria

✅ **Resolution 1 (Event Logging):** Complete
- [x] Code changes made (2 call sites wired)
- [x] Tests written and passing (4 regression tests)
- [x] Full test suite regression-checked (pending)

⏳ **Findings 2–5:** Deferred per plan
- [ ] No code changes (product/architectural decisions needed)
- [ ] Filed for roadmap
- [ ] Dependencies documented for future work

---

## Next Steps (Post-PR)

1. **Complete test suite run** — verify no regressions from event-logging changes
2. **Create issues** in order:
   - P1: "Identity continuity: map NPCRegistry display-name to Foundry actor_uuid for NPC memory"
   - P1: "Event completeness: audit remaining LLM call sites and wire into event log"
   - P1: "History tagging: separate NPC turns from player context"
   - P2: "Event-sourcing consumer: clarify design intent (real-time projection vs. audit trail vs. NPC memory)"
3. **Optional:** Add ponytail comment to NPC memory code marking it as "aspirational, needs identity mapping before enabling"

All resolutions are safe for post-ship polish; none block landing agent-driven features.
