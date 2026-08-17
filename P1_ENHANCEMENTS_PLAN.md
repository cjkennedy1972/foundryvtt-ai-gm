# P1 Enhancements Plan — Autonomous GM Moat Features

**Status:** PR #106 merged, P0 nearly complete (House Rules pending). Ready to execute P1.

**Goal:** Deliver on-moat features that amplify autonomy and campaign coherence:
1. **Living Settlement Generation** — queryable towns with NPCs, schedules, relationships
2. **In-Foundry Control Surface** — drive sessions from inside Foundry, not external admin panel
3. **Foundation work** — NPC identity mapping, event enrichment

---

## Part A: P0 Completion Gap (2–3 hours)

### House Rules Journal Loading

**Status:** Partial (system honors house rules but doesn't load them from vault)

**Work:** Implement `get_house_rules_context_sync()` in vault loader

**File:** `ai-engine/campaign/loader.py`

```python
def get_house_rules_context_sync(self, vault_path: str) -> str:
    """Load house rules from vault/HOUSE_RULES.md, inject into system prompt.
    
    Returns formatted string for inclusion in system prompt context.
    If file doesn't exist, return empty string (graceful).
    """
    house_rules_file = vault_path / "HOUSE_RULES.md"
    if not house_rules_file.exists():
        return ""
    
    content = house_rules_file.read_text()
    return f"## House Rules (GM-Approved)\n\n{content}"
```

**Integration:** Call in `system_prompts.py` context builder, append to `SYSTEM_PROMPT_BASE`

**Test:** `test_house_rules_loads_from_vault.py` — verify file exists, gets injected, honors GM overrides

**Effort:** 2–3 hours (including test)

**Blocker:** None

---

## Part B: Foundation Work for Living Settlement (3 days)

Before living settlement can work, NPCs must be addressable by Foundry actor ID and events must carry NPC identity.

### 1. NPC Identity Mapping (1 day)

**Problem:** NPCRegistry uses `npc_id` (string) but Foundry uses `actor_id` (UUID). When an NPC acts, we don't know which Foundry actor they correspond to, so we can't:
- Track NPC location changes across Foundry tokens
- Link NPC memories/goals to actors
- Query "who's in the tavern now?"

**Solution:** Add bidirectional registry in NPCRegistry

**File:** `ai-engine/npc/registry.py`

```python
class NPCRegistry:
    """Registry of autonomous NPCs with identity mapping to Foundry actors."""
    
    def __init__(self):
        self._npc_by_id: Dict[str, NPC] = {}  # npc_id → NPC instance
        self._actor_uuid_to_npc_id: Dict[str, str] = {}  # actor_uuid → npc_id
    
    def register(self, npc_id: str, npc: NPC, actor_uuid: Optional[str] = None):
        """Register NPC with optional Foundry actor mapping."""
        self._npc_by_id[npc_id] = npc
        if actor_uuid:
            self._actor_uuid_to_npc_id[actor_uuid] = npc_id
    
    def get_by_actor_uuid(self, actor_uuid: str) -> Optional[NPC]:
        """Retrieve NPC by Foundry actor ID."""
        npc_id = self._actor_uuid_to_npc_id.get(actor_uuid)
        return self._npc_by_id.get(npc_id) if npc_id else None
    
    def get_npc_id_for_actor(self, actor_uuid: str) -> Optional[str]:
        """Get npc_id for a Foundry actor."""
        return self._actor_uuid_to_npc_id.get(actor_uuid)
    
    async def sync_with_foundry(self, foundry_client, session_id: str):
        """Match NPCs to Foundry actors by name/fuzzy-match, build mapping."""
        # Fetch actors from Foundry
        actors = await foundry_client.get_actors()
        
        # For each NPC, find matching actor and register mapping
        for npc_id, npc in self._npc_by_id.items():
            matching_actor = self._find_actor_by_name(npc.name, actors)
            if matching_actor:
                self._actor_uuid_to_npc_id[matching_actor["uuid"]] = npc_id
```

**Integration:**
- Call `sync_with_foundry()` at session start (after campaign load)
- Use in NPCAgent.act() to record actor_uuid in events
- Use in settlement queries ("who's at location X?")

**Test:** `test_npc_registry_identity_mapping.py`
- Register NPC with actor_uuid
- Query by actor_uuid, get NPC back
- sync_with_foundry matches by name

**Effort:** 1 day

---

### 2. Event Payload Enrichment (0.5 day)

**Problem:** Events log NPC actions but don't carry the Foundry actor UUID, so we can't correlate events to tokens.

**Solution:** Enrich NPC_MOVED, NPC_ACTED, etc. with actor_uuid

**File:** `ai-engine/events/types.py` (update event schemas)

```python
# Add optional actor_uuid to NPC-related events
NPC_MOVED_PAYLOAD = {
    "npc_id": str,      # existing
    "actor_uuid": str,  # NEW: Foundry token/actor UUID
    "location": str,
    "timestamp": float,
}

RELATIONSHIP_CHANGED_PAYLOAD = {
    "source_id": str,
    "source_actor_uuid": str,  # NEW
    "target_id": str,
    "target_actor_uuid": str,  # NEW
    "relationship_type": str,
    "strength": float,
}
```

**Implementation:** In `NPCAgent.act()`, fetch actor_uuid from registry before logging event

```python
async def act(self, session_id: str, triggering_event: dict) -> List[Ruling]:
    # ... generate action ...
    
    # Log with actor_uuid enrichment
    actor_uuid = self._registry.get_actor_uuid_for_npc(self.npc_id)
    await self._event_store.append(session_id, NPC_MOVED, {
        "npc_id": self.npc_id,
        "actor_uuid": actor_uuid,  # NEW
        "location": action.location,
    })
```

**Test:** `test_events_carry_actor_uuid.py`
- NPC acts → event includes actor_uuid
- Query event, verify actor_uuid present and correct

**Effort:** 0.5 day

---

### 3. Scene Automation Verification (0 days)

**Status check:** From PR #106, all scene automation is complete:
- ✅ Fog of war (update_vision action, system prompt, dispatcher)
- ✅ Hazards (environmental_save action)
- ✅ Ambient sounds (place_sounds action)
- ✅ GM macros (execute_macro action, wired to effects manager)

**Action:** Mark as complete; move to P1 implementation.

---

## Part C: Living Settlement Generation (3–5 days)

Once foundation work is done, build queryable settlements with NPCs, schedules, buildings, factions.

### Overview

**What:** Generate towns as structured entities during campaign creation (or on-demand):
- Buildings (tavern, blacksmith, temple, etc.) with inventory, services, occupants
- NPCs with assigned buildings and daily schedules
- Faction relationships and power dynamics
- Time-of-day queries ("who's in the tavern at dusk?")

**Why:** Gives the autonomous GM a living social world to query and improvise into, instead of generating NPCs fresh every scene.

**Architecture:**
- Extend `campaign/world_builder.py` with settlement generator
- Add settlement schema to world `npc_records`
- Extend `WorldClockAgent` to track NPC location by time-of-day
- Add settlement queries API (worldclock + registry)

### 4a. Settlement Schema Design (0.5 day)

**File:** New `ai-engine/world/settlement.py`

```python
@dataclass
class Building:
    """A building in a settlement."""
    id: str  # unique within settlement
    name: str
    building_type: str  # "tavern", "blacksmith", "temple", etc.
    services: List[str]  # ["lodging", "ale", "rumors"]
    occupants: List[str]  # NPC ids during normal business hours
    inventory: Dict[str, int]  # item → quantity (for shops)
    description: str  # flavor

@dataclass
class Settlement:
    """A town/village as a queryable entity."""
    id: str
    name: str
    region: str  # part of campaign world
    buildings: Dict[str, Building]  # building_id → Building
    npcs: Dict[str, SettlementNPC]  # npc_id → NPC + schedule
    factions: List[Faction]  # power groups in town
    population: int
    character: str  # cultural flavor
    
    def query_location_at_time(self, time_of_day: str) -> Dict[str, List[str]]:
        """Return {location: [npc_ids]} for a given time of day."""
        # "dusk" → {tavern: [mara, kess], market: [elder_tobias]}
        pass
    
    def npc_occupation(self, npc_id: str) -> str:
        """What does this NPC do? e.g., 'tavern keeper', 'bounty hunter'"""
        pass

@dataclass
class SettlementNPC:
    """NPC as part of a settlement (schedule, occupation, relationships)."""
    npc_id: str
    occupation: str  # "blacksmith", "hedge wizard", etc.
    schedule: Dict[str, str]  # time_of_day → building_id
    primary_building: str  # where they work
    relationships: Dict[str, str]  # npc_id → relationship_type
    secrets: List[str]  # hooks for the GM
```

**Store:** In world config (alongside `campaign_name`, `factions`)

```python
# world/campaign.yaml
campaign_name: The Shattered Coast
settlements:
  - id: "redmarch"
    name: Redmarch
    buildings: [...]
    npcs: [...]
```

**Effort:** 0.5 day (design + schema)

---

### 4b. Settlement Generator (1.5 days)

**File:** `ai-engine/campaign/settlement_generator.py` (new)

```python
class SettlementGenerator:
    """Generate settlements with buildings, NPCs, schedules, and factions."""
    
    def __init__(self, llm_manager, comfyui_client=None):
        self.llm = llm_manager
        self.comfyui = comfyui_client  # optional: generate building art
    
    async def generate(
        self,
        campaign_context: str,
        settlement_name: str,
        population_hint: str = "small village",
        faction_hooks: List[str] = None,
    ) -> Settlement:
        """Generate a settlement for a campaign.
        
        Args:
            campaign_context: Campaign lore/setting (injected from vault)
            settlement_name: Name of the settlement to generate
            population_hint: "small village", "trade town", "city quarter", etc.
            faction_hooks: Optional faction names to weave in
        
        Returns:
            Settlement object with full structure.
        """
        prompt = f"""Generate a settlement for a fantasy campaign.
        
Campaign setting: {campaign_context}
Settlement: {settlement_name}
Size: {population_hint}

Provide a JSON structure with:
- buildings: [{{name, type, services, occupants_count, flavor}}]
- npcs: [{{name, occupation, personality, relationships_sketch}}]
- factions: [{{name, power_level, conflict}}]
- daily_rhythms: {{dawn: "...", noon: "...", dusk: "...", night: "..."}}

Generate 5–10 buildings, 8–12 NPCs, 2–4 factions. Make it coherent and explorable."""
        
        result = await self.llm.generate(prompt, schema=SETTLEMENT_SCHEMA)
        settlement = self._materialize(result, settlement_name)
        return settlement
    
    def _materialize(self, gen_result: dict, settlement_name: str) -> Settlement:
        """Convert LLM output to Settlement object."""
        # Build buildings, assign NPCs to time slots, link factions
        pass
```

**Integration:**
- Call during campaign creation (in `campaign/loader.py`)
- Store result in world config
- Load on session start

**Test:** `test_settlement_generator.py`
- Generate a settlement (mock LLM)
- Verify schema: buildings exist, NPCs have schedules, factions present
- Query location at different times

**Effort:** 1.5 days

---

### 4c. WorldClockAgent: Settlement-Aware Location Tracking (1 day)

**Problem:** Currently, `WorldClockAgent` just advances time. It doesn't track where NPCs are.

**Solution:** Extend `WorldClockAgent` to:
1. On TIME_ADVANCED events, update NPC locations based on their schedule
2. Provide query: "which NPCs are at location X right now?"

**File:** `ai-engine/worldclock/agent.py` (extend)

```python
class WorldClockAgent:
    """Track game time, manage NPC schedules, query NPC locations."""
    
    def __init__(self, event_store, settlement_registry, npc_registry):
        self.event_store = event_store
        self.settlements = settlement_registry
        self.npcs = npc_registry
        self.current_time_of_day = "dawn"  # tracks within-day cycle
    
    async def handle_time_advanced(self, session_id: str, event: dict):
        """On time change, update NPC locations per schedule."""
        duration_seconds = event["payload"]["duration_seconds"]
        new_time_of_day = self._compute_time_of_day(duration_seconds)
        
        # For each settlement, for each NPC, look up their scheduled location
        for settlement in self.settlements.all():
            for npc in settlement.npcs.values():
                new_location = settlement.query_location_at_time(new_time_of_day)
                
                # Log NPC_MOVED event
                await self.event_store.append(session_id, NPC_MOVED, {
                    "npc_id": npc.npc_id,
                    "actor_uuid": self.npcs.get_actor_uuid_for_npc(npc.npc_id),
                    "location": new_location,
                    "reason": "schedule_advanced",
                })
    
    async def query_location_at_time(
        self,
        settlement_id: str,
        time_of_day: Optional[str] = None,
    ) -> Dict[str, List[str]]:
        """Query: who's where in this settlement right now?
        
        Returns: {location_name: [npc_ids]}
        Example: {"tavern": ["mara", "kess"], "market": ["elder_tobias"]}
        """
        settlement = self.settlements.get(settlement_id)
        if not settlement:
            return {}
        
        time = time_of_day or self.current_time_of_day
        return settlement.query_location_at_time(time)
```

**Integration:**
- Register with event listener (on TIME_ADVANCED events)
- Expose queries to ChatListener (/gm settlement query commands)
- Expose to LLM system prompt (world context builder can ask "who's in the tavern?")

**Test:** `test_worldclock_settlement_queries.py`
- Advance time from dawn to dusk
- Verify NPCs moved via NPC_MOVED events
- Query location_at_time, verify correct NPCs present

**Effort:** 1 day

---

### 4d. Integration & User-Facing Queries (0.5 day)

**Add GM commands to ChatListener:**

```python
# In _handle_gm_command:
elif command.startswith("settlement query "):
    settlement_id = command[len("settlement query "):].strip()
    locations = await self.worldclock_agent.query_location_at_time(settlement_id)
    
    output = f"📍 **{settlement_id}** at {self.worldclock_agent.current_time_of_day}:\n"
    for location, npcs in locations.items():
        output += f"- {location}: {', '.join(npcs)}\n"
    
    await self.foundry.chat_message(output, speaker="GM")
```

**Expose to LLM:** Add to system prompt context builder:

```
## Available Settlements

{settlements_brief_summary}

You can query NPC locations by settlement and time of day:
/gm settlement query <settlement_id>

This helps you improvise encounters grounded in the living world.
```

**Test:** `test_settlement_gm_commands.py`

**Effort:** 0.5 day

---

## Part D: In-Foundry Control Surface (2–3 days)

**Goal:** Let the GM drive sessions from inside Foundry, not only the external admin panel.

Currently, most GM control happens via `/gm` commands posted to Foundry chat (canons, directives, etc.), but *session control* (start/stop/pause) and *advanced queries* require the external admin panel. This work brings those into Foundry.

### 5a. In-Foundry Session Control Tab (1.5 days)

**Concept:** Add a new sidebar panel ("Session Control") in Foundry that:
- Shows session status (running / paused / ended)
- Session time (real and in-game)
- Quick buttons: pause session, end session, trigger idle beat, trigger NPC turn
- Session transcript (last 10 events)

**Implementation:** 
- Add React component in `frontend/components/SessionControlPanel.tsx` (or similar)
- Wire to backend via `/api/session/status`, `/api/session/pause`, etc.
- Embed in Foundry via FoundryHooks integration (existing relay system)

**Effort:** 1.5 days (UI + backend endpoints)

**Files:**
- `relay/routes/session.py` — new endpoints for session control
- `frontend/components/SessionControlPanel.tsx` — new React component
- `foundry/hooks.js` — register panel with Foundry UI

**Test:** Integration test with mock Foundry, verify buttons work

---

### 5b. Advanced Query Interface (1 day)

**Add to Session Control:**
- Settlement query (who's where?)
- NPC status (location, goals, relationships)
- Canon review (pending vs. canonized facts)
- Event search ("show me all NPC_MOVED events")

**Implementation:** Extend React component with query form

**Effort:** 1 day

---

### 5c. Design Polish & Documentation (0.5 day)

**Effort:** 0.5 day

**Total for In-Foundry Control:** 2.5–3 days

---

## Part E: Execution Timeline

### Option A: Aggressive (2 weeks, all P1 complete)

```
Week 1:
  Mon–Tue: House Rules journal (0.5d) + NPC id mapping (1d)
  Wed–Thu: Event enrichment (0.5d) + Settlement generator design (0.5d)
  Fri: Settlement generator implementation start (1d)

Week 2:
  Mon–Tue: Settlement generator complete (1.5d) + WorldClockAgent (1d)
  Wed–Thu: In-Foundry control surface (2d)
  Fri: Integration testing + buffer (1d)

END: All P0 + P1 complete
```

**Effort:** ~11 days
**Risk:** Settlement generator quality (LLM coherence, schema validation)

---

### Option B: Staged (1 week P0 + foundation, then P1 incremental)

```
Week 1:
  Mon–Tue: House Rules journal (0.5d) + NPC id mapping (1d)
  Wed–Thu: Event enrichment (0.5d)
  Fri: Buffer + code review

END: P0 complete, P1 foundation ready (2d of work)

Following sprint:
  Settlement generator (2d)
  WorldClockAgent (1d)
  In-Foundry control (2–3d)
```

**Effort:** Week 1 = 2.5d; Week 2+ = 5–6d more
**Risk:** Lower; staged approach allows feedback on settlement quality before full P1

---

### Option C: Minimal (P0 only)

```
Week 1:
  Mon: House Rules journal (0.5d)
  Tue–Fri: Buffer, code review, catch-up
```

**Effort:** 0.5d
**Risk:** P1 deferred indefinitely

---

## Dependency Graph

```
P0 Complete (House Rules)
  ↓
NPC id Mapping (1d) + Event Enrichment (0.5d)
  ↓
├─→ Settlement Generator (1.5d)
│    ↓
│    WorldClockAgent (1d)
│    ↓
│    P1a: Living Settlement COMPLETE ✅
│
└─→ In-Foundry Control (2.5–3d)
     ↓
     P1b: Control Surface COMPLETE ✅
```

**Critical path:** Foundation work (1.5d) → Settlement (2.5d) → 4 days total

---

## Success Criteria

### P0 Complete
- ✅ House Rules journal loads from vault
- ✅ System prompt includes house rules on every turn
- ✅ Test coverage (house rules + GM overrides)

### P1a: Living Settlement
- ✅ Settlement schema designed and validated
- ✅ Settlement generator produces coherent towns
- ✅ NPCs have occupations, schedules, relationships
- ✅ GM can query "who's in the tavern at dusk?"
- ✅ NPC_MOVED events triggered by schedule changes
- ✅ Test: settlement queries return correct NPCs

### P1b: In-Foundry Control
- ✅ Session control panel in Foundry (status, pause, end)
- ✅ Advanced query interface (settlement, NPC, canon, events)
- ✅ All endpoints wired and tested
- ✅ Polish and documentation

---

## Recommendation

**Start with Option B (Staged): 1 week on foundation + P0, then reassess P1 scope.**

Rationale:
1. P0 (House Rules) is trivial and unblocks the full moat value
2. Foundation work (NPC identity + events) is critical for Living Settlement quality
3. Settlement generator quality is uncertain (LLM coherence); staged approach allows prototyping
4. In-Foundry control can run in parallel after foundation is solid

**By end of Week 1:** P0 100% complete, P1 foundation in place, ready to build settlements with confidence.

---

## Next Step: Confirm Plan

Approve timeline (A/B/C) and proceed with:
1. House Rules journal implementation
2. NPC identity mapping design + implementation
3. Event enrichment wiring

Ready?
