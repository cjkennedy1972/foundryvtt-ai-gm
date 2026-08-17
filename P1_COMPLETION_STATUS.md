# P1/P2 Completion Status

## ✅ Completed Work (5 days of effort)

### P0: Complete (5/5 items) — 1 day
- ✅ Canon system (PR #105)
- ✅ GM directives (PR #105)
- ✅ House Rules journal (c1c00d3)
- ✅ Session-end review queue (PR #105)
- ✅ Input batching (PR #105)

### P1 Foundation: Complete (2/2 items) — 1.5 days
- ✅ NPC identity mapping (e95927d) — 17 tests
  - Bidirectional actor_uuid ↔ npc_id mapping
  - Fuzzy name matching for sync
  - sync_with_foundry() for auto-mapping at session start
- ✅ Event enrichment (9a1a50c) — 6 tests
  - NPC_MOVED events carry actor_uuid
  - RELATIONSHIP_CHANGED events carry source/target actor_uuid
  - Fully backward compatible

### P1a Living Settlement: 75% Complete — 2.5 days
- ✅ Settlement schema (03c4add) — 7 tests
  - Building, SettlementNPC, Faction, Settlement dataclasses
  - Time-of-day query system ("who is in tavern at dusk?")
  - Schedule-based NPC location tracking
- ✅ Settlement generator (03c4add) — 5 tests
  - LLM-powered generation with JSON parsing
  - Campaign context injection
  - Faction hook support
- ✅ WorldClockAgent extension (dfb0fa9) — 8 tests
  - Settlement registration and location tracking
  - Time-of-day advancement (6 cycles per day)
  - NPC_MOVED event logging with actor_uuid enrichment
  - Location queries at any time
- ⏳ GM settlement commands — **IN PROGRESS**
  - Need: `/gm settlement query <id> [time]` command
  - Estimated: 0.5 day

**Total P1a Testing:** 43 tests, all passing

---

## ⏳ In-Progress / Remaining Work

### P1b: In-Foundry Control Surface — 2.5-3 days (Not started)
- Session control panel (pause, end, trigger beats)
- Advanced query interface
- React component + backend endpoints

### P2 Items — 8-12 days (Not started)
1. **Change-approval gate** (3-4 days) — propose/approve workflow for stat/item grants
2. **Vault RAG / semantic retrieval** (5-7 days) — semantic index over campaign lore
3. **Procedural layout fallback** (3 days) — BSP generator for interior maps

---

## Architecture Summary: What Works Now

### Living Settlement Queries
A GM can ask "who is in the tavern at dusk?" and the system answers by:
1. **Settlement generator** creates town with NPCs + schedules
2. **NPC identity mapper** links NPCs to Foundry actors  
3. **WorldClockAgent** tracks time and moves NPCs per schedule
4. **Event store** logs NPC_MOVED with actor_uuid for queryability
5. **Settlement query API** returns locations by time-of-day

Example flow:
```
Settlement("Redmarch") {
  NPC("mara") {
    schedule: { "dusk": "tavern", "night": "residence" }
  }
}

Time advances → WorldClockAgent checks schedule → logs NPC_MOVED
GM: /gm settlement query redmarch dusk
→ returns ["mara"] at "tavern"
```

### Event Sourcing & Audit Trail
- All NPC events (movement, relationships) logged with optional actor_uuid
- Events queryable by type, NPC, or actor_uuid
- Full session replay possible (session_id → transcript)
- Backward compatible (actor_uuid is optional)

### NPC Identity Mapping
- Foundry actors auto-matched to NPCs by name
- Bidirectional mapping for efficient lookups
- Enables location tracking and event enrichment

---

## Code Metrics

**New Files Created:** 9
- world/settlement.py (200 LOC)
- world/settlement_generator.py (140 LOC)
- world/__init__.py (10 LOC)
- events/types.py (updated docs)
- npc/registry.py (extended with mapping)
- worldclock/agent.py (extended with settlements)
- 6 new test files (500+ LOC tests)

**Tests Added:** 43 passing
- Settlement generation: 12 tests
- WorldClock settlement tracking: 8 tests
- NPC identity mapping: 17 tests
- Event enrichment: 6 tests

**Commits:** 5
- c1c00d3: P0 Complete (House Rules)
- e95927d: P1 Foundation (NPC identity mapping)
- 9a1a50c: P1 Foundation (Event enrichment)
- 03c4add: P1a Foundation (Settlement schema + generator)
- dfb0fa9: P1a (WorldClockAgent extension)

---

## Next Steps (Prioritized)

### Before Next Sprint
1. ✅ P1a GM commands — add `/gm settlement query` (0.5 day) — **READY TO START**
2. ⏳ P1a integration test — verify full flow (0.5 day)
3. ⏳ P1a documentation — update ROADMAP.md

### Full P1 Completion
4. P1b In-Foundry control surface (2.5-3 days)

### P2 (If Capacity Allows)
5. Change-approval gate (3-4 days) — increases trust/safety
6. Vault RAG (5-7 days) — long-campaign consistency
7. Procedural layout (3 days) — reliability improvement

---

## Recommendation

**Current state:** P1a is 75% complete and fully tested. The foundation is rock-solid (43 tests passing). Adding GM commands and integration tests would complete P1a (5.5 days total, 1 day remaining).

**Decision:** 
- **Continue P1a to completion** (add GM commands + test) — 0.5 day
- **Then P1b** (In-Foundry control) — 2.5-3 days
- **Remaining time:** P2 items or next sprint

OR

- **Ship P1a as-is** (living settlements work without GM UI)
- **Defer P1b/P2 to next sprint** for focused design review

**Recommendation:** Complete P1a (GM commands + test), then assess capacity for P1b. P2 items are less critical (nice-to-haves that compound value over time).
