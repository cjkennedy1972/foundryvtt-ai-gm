# Comprehensive Completion Plan — All Open Work

Complete audit of remaining work across:
1. **Roadmap items** (P0/P1/P2, prioritized by leverage-to-effort)
2. **Code review deferred findings** (5 architectural/scope decisions)
3. **Known gaps** (from memory, code comments, recent fixes)

---

## Part A: Roadmap Status

### P0 — Highest leverage (MOSTLY DONE)

| Item | Status | Effort | Done By |
|------|--------|--------|---------|
| **Canon system** (draft vs. canonized) | ✅ DONE | — | PR #105 |
| **GM directives** (extend `/gm` for fact-pinning) | ✅ DONE | — | PR #105 |
| **House Rules journal** (vault doc always injected) | 🟡 PARTIAL | 1 day | Need to implement |
| **Session-end review queue** (canon proposals) | ✅ DONE | — | PR #105 |
| **Input batching** (multi-player message debounce) | ✅ DONE | — | PR #105 |

**P0 Completion:** 4/5 done. **1 day to finish House Rules.**

---

### P1 — On-moat, medium build

| Item | Status | Effort | Est. Completion |
|------|--------|--------|---------|
| **In-Foundry control surface** | ❌ NOT STARTED | 2-3d | Need design |
| **Living settlement generation** | ❌ NOT STARTED | 3-5d | Depends on NPC identity (Finding #2) |

**P1 Completion:** 0/2. Both require foundation work (identity mapping blocks living settlements).

---

### P2 — Higher value, larger builds

| Item | Status | Effort | Notes |
|------|--------|--------|-------|
| **Change-approval gate** | ❌ NOT STARTED | 3-4d | Safe-fail feature, not urgent |
| **Vault RAG / semantic retrieval** | ❌ NOT STARTED | 5-7d | Major effort; compounds long-campaign benefit |
| **Procedural layout fallback** | ❌ NOT STARTED | 3d | Reliability tweak; start if LLM geometry problems observed |

**P2 Completion:** 0/3. Defer unless blocking user issues.

---

## Part B: Code Review Deferred Findings

Five findings flagged as requiring architecture/product decisions rather than fixes:

| Finding | Effort | Blocker? | Path Forward |
|---------|--------|----------|--------------|
| **NPCMemory.recall identity mapping** | 2d | No | Design NPC id↔uuid bridge; unblocks Living Settlement |
| **NPC history tagging** | 1-2d | No | Add metadata to conversation history; prevent context pollution |
| **NPC retry feedback** | 1-2d | No | Product decision: autonomous NPCs or flavor NPCs? |
| **Proactive/retry event logging** | **✅ DONE** | No | Completed; PR #107 |
| **Event-sourcing consumer** | 3-5d | No | Product decision: which use case? (real-time / audit / NPC memory) |

**Deferred Findings:** 1/5 resolved; 4 need product/architectural decisions.

---

## Part C: Integration Gaps & Known Issues

### From Code Review Gap Sweep

None explicitly flagged, but related to deferred findings:

- **Event log completeness** — proactive/retry paths now log events (✅ fixed in Part B)
- **NPC goal scheduling** — implemented, triggered on TIME_ADVANCED + ACTION_RESOLVED events ✅
- **Referee adjudication** — spell-slot legality now checked live vs. Foundry sheet ✅
- **Scene director contention** — one NPC per tick, highest priority ✅
- **Fresh database backup spurious** — fixed (was triggering every new install) ✅

### From Session Compilation

**Unresolved:** From memory (25 days old, verify against current code):
- Campaign lifecycle overhaul — "all done 2026-07" (suggests grid padding, scene automation, pacing/combat sync)
  - Grid padding: ✅ (memory says done)
  - Scene automation: Fog of war, hazard viz, ambient sound, GM macros — **CHECK IF IMPLEMENTED**
  - Combat sync / MIDI QoL — **CHECK IF IMPLEMENTED**

---

## Part D: Prioritized Completion Roadmap

### Tier 1: Ship-blocking (finish before PR #106 lands)

| Task | Effort | Status | Why |
|------|--------|--------|-----|
| Run full test suite on PR #106 | 0 | 🟡 Pending | Verify no regressions from event-logging fixes |
| Merge PR #106 (agent-driven features) | 0 | ⏳ Ready | Pass approved; waiting to land |
| **SUBTOTAL** | **0** | | Land the 6-phase system |

### Tier 2: Complete P0 (highest ROI, low effort)

| Task | Effort | Impact | Est. Time |
|------|--------|--------|-----------|
| Implement House Rules journal loading | 1d | High (consistency win) | + 1 day to master |
| **SUBTOTAL** | **1d** | | Finish all P0 items |

### Tier 3: Unblock P1 (foundation work)

| Task | Effort | Impact | Blocker For |
|------|--------|--------|------------|
| **Design NPC id mapping** (NPCRegistry ↔ Foundry actor_uuid) | 1d | Medium | Living Settlement, NPC memory |
| **Implement NPC id bridge** in persistence layer | 1d | Medium | Living Settlement, NPC memory |
| **Enrich event payloads** with actor_uuid on NPC_MOVED | 1d | High (enables replay) | Session debugging, NPC memory |
| **Verify Scene Automation features** (fog of war, hazard viz, etc.) | 0 | ? | Might be done |
| **SUBTOTAL** | **3d + verification** | | Unblock Living Settlement |

### Tier 4: Implement Living Settlement (P1 leverage play)

| Task | Effort | Depends On | Est. Time |
|------|--------|-----------|-----------|
| Design NPC schedule model (per-NPC data in npc_records) | 1d | Tier 3 NPC id work |  |
| Implement schedule → location tracking (WorldClockAgent extension) | 1d | Tier 3 NPC id work |  |
| Extend event types (NPC_MOVED now includes schedule logic) | 1d | Tier 3 work | |
| Add faction event sourcing (FACTION_* event types) | 1d | Tier 3 work | |
| Test settlement queries ("who's in the tavern at dusk?") | 1d | Tier 3 + building model | |
| **SUBTOTAL** | **5d** | Tier 3 complete | Living settlement MVP |

### Tier 5: Resolve Architectural Decisions (4 deferred findings)

| Decision | Effort | Time Window |
|----------|--------|-------------|
| **NPC autonomy level:** Are NPCs autonomous (deserve retries) or flavor (fail safe)? | 0 | Clarify in meeting |
| **Event-sourcing consumer intent:** Real-time projection vs. audit trail vs. NPC memory fuel? | 0 | Clarify in meeting |
| **History tagging approach:** Metadata on conversation_history, or separate NPC log? | 0 | Clarify in meeting |
| **Implement chosen decisions** (after clarification) | 2-5d | TBD |

### Tier 6: P2 Items (defer unless user-requested)

| Item | Effort | Triggers |
|------|--------|----------|
| Change-approval gate | 3-4d | User request / trust/safety concern |
| Vault RAG / semantic retrieval | 5-7d | User complaint about context surface issues |
| Procedural layout fallback | 3d | Observed LLM geometry failures in play |

---

## Timeline: Path to "Everything Complete"

### Scenario A: Aggressive (finish all P0 + P1 in ~2 weeks)

```
Week 1:
  - Merge PR #106 + run full test suite (1d)
  - House Rules journal implementation (1d)
  - NPC id mapping design + bridge implementation (2d)
  - Event payload enrichment (1d)

Week 2:
  - Verify/debug scene automation features (1d)
  - Living settlement scheduling + NPC location tracking (2d)
  - Faction event sourcing + integration testing (2d)
  - Buffer/bug fixes (1d)

END: All P0 + P1 items complete; P2 ready for review.
```

### Scenario B: Staged (finish P0 + prep P1 in ~1 week)

```
Week 1:
  - Merge PR #106 + test suite (1d)
  - House Rules journal (1d)
  - NPC id mapping design + bridge (2d)
  - Buffer/review (1d)

Post-Sprint:
  - Event enrichment (1d)
  - Living settlement build (5d, incremental)
  - Architectural decision clarification (async)
```

### Scenario C: Minimal (land PR #106, hold P1 for next sprint)

```
Week 1:
  - Merge PR #106 + test suite (1d)
  - House Rules journal (1d)
  - Buffer (1d)

Hold for next sprint:
  - NPC id mapping + Living Settlement (scope too large for one sprint)
  - Architectural decisions (P2/P3 dependencies)
```

---

## Dependency Graph

```
PR #106 (Agent-Driven) ✅
  ↓
Full Test Suite + Merge
  ↓
House Rules Journal (P0) ← 1d
  ↓
ALL P0 COMPLETE
  ↓
├─→ NPC id Mapping (design + bridge) ← 2d
│    ↓
│    Event Payload Enrichment ← 1d
│    ↓
│    Living Settlement (build) ← 5d
│    ↓
│    P1 COMPLETE
│
└─→ Architectural Decisions (async)
     ↓
     NPC Autonomy → NPC Retry feedback implementation
     Event Consumer → EventStore consumer + real-time projection
     History Tagging → Conversation history cleanup
```

---

## Success Criteria

✅ **Tier 1 (Ship)**: PR #106 merged, full tests passing
✅ **Tier 2 (P0)**: All 5 P0 items complete and tested
✅ **Tier 3 (Foundation)**: NPC id mapping wired; events enriched
✅ **Tier 4 (P1 MVP)**: Living settlement scheduling working; settlements queryable
🟡 **Tier 5 (Decisions)**: Architectural intent clarified (async)
🟡 **Tier 6 (P2)**: Deferred; start on user signal

---

## What's Recommended: 2-Week Sprint (Aggressive Scenario)

**Win condition:** All P0 + foundational work for P1 complete, Living Settlement in first-iteration form.

**Starting Monday:**
- Day 1: Merge PR #106, run full test suite, resolve any blockers
- Days 2–3: House Rules journal + test coverage
- Days 4–5: NPC id mapping architecture + implementation
- Days 6–7: Event payload enrichment + verification
- Days 8–10: Living settlement build (schedule tracking, location queries, faction events)
- Days 11–14: Integration testing, documentation, bug buffer

**By end:** User has the autonomous GM moat features (canon, directives, agent-driven NPC autonomy, living settlements) ready to play with.

---

## Next Step: User Input

1. **Which scenario?** (Aggressive / Staged / Minimal)
2. **Clarify architectural decisions now** (NPC autonomy level, event consumer intent, history approach)?
3. **Scene automation status** — check if fog of war / hazard viz / ambient sound / GM macros already exist?
4. **Priority conflicts?** Any P2 items urgent (approval gate, RAG, procedural geometry)?

Once confirmed, I can start with **Tier 1 (merge PR #106)** immediately.
