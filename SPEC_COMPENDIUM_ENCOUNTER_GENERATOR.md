# Compendium Encounter Generator — Architecture Specification

**Version:** 1.1 (post-audit)  
**Status:** Phase 1 implemented & verified  
**Last Updated:** 2026-06-30  
**Owner:** AI-GM Team  

> **v1.1 audit corrections.** The original v1.0 spec prescribed four correctness
> bugs that any team building to it would have reproduced. They are now fixed in
> both this spec and the implementation:
> 1. **Budget ignored party level** (size-only `DynamicDifficulty` table) → now
>    `per_character_threshold[level] * size`, see *Budget math*.
> 2. **Encounter multiplier missing** (raw XP vs budget) → selection now uses
>    `adjusted_xp = raw * multiplier(count, size)`.
> 3. **Deployment placed by name and discarded the compendium UUID** (a
>    regression vs. the code it replaced) → now imports via `ensure_monster_actor`
>    and places by world UUID.
> 4. **`environment` was a dead parameter** → now a soft post-query filter.
>
> Also: positioning no longer fakes melee/ranged from CR (role-agnostic cluster
> using the real scene size + grid); schema uses Pydantic v2 `pattern=`. The
> implementation in `combat/compendium_generator.py` + `actions/executors.py`
> matches this spec and is covered by 28 passing tests.

---

## Executive Summary

Replace LLM-based (hallucinated) monster generation with query-based encounter generation from Foundry D&D 5e compendium. This enhancement improves encounter quality, eliminates stat block hallucination, and integrates tactical positioning.

### Scope
- **Phase 1 (MVP):** Compendium queries + budget-based selection + basic positioning
- **Phase 2 (Polish):** Monster caching, role-based grouping, environmental awareness
- **Phase 3 (Future):** Multi-system support, streaming generation, UI dashboard

### Success Criteria (Phase 1)
- ✅ Generate encounters from real D&D 5e monsters (no hallucination)
- ✅ Respect party power rating (difficulty accurate ±10%)
- ✅ Position creatures tactically (melee front, ranged back)
- ✅ All tests pass; integration with `execute_generate_encounter()` complete
- ✅ No performance regression (<2s generation time)

---

## Architecture Overview

### System Diagram
```
┌─────────────────────────────────────────────────────────────┐
│ AI-GM: execute_generate_encounter()                         │
│ (actions/executors.py)                                      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ CompendiumEncounterGenerator                                │
│ (combat/compendium_generator.py)                            │
├─────────────────────────────────────────────────────────────┤
│ ┌──────────────────┐  ┌──────────────────┐                 │
│ │ Query Module     │  │ Selection Module │                 │
│ │ (compendium)     │  │ (greedy algo)    │                 │
│ └──────────────────┘  └──────────────────┘                 │
│       ↓                       ↓                             │
│ ┌─────────────────────────────────────┐                   │
│ │ DynamicDifficulty (from combat/)    │                   │
│ │ • party power rating                │                   │
│ │ • XP budget lookup                  │                   │
│ └─────────────────────────────────────┘                   │
│       ↓                                                     │
│ ┌──────────────────┐  ┌──────────────────┐                 │
│ │ Position Module  │  │ Notes Generation │                 │
│ │ (CombatMechanics)│  │                  │                 │
│ └──────────────────┘  └──────────────────┘                 │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ Foundry Integration                                         │
│ ├─ execute_js: Query compendium index                      │
│ ├─ place_token: Deploy creatures to map                    │
│ └─ start_encounter: Begin combat                           │
└─────────────────────────────────────────────────────────────┘
```

---

## Module Specifications

### 1. CompendiumEncounterGenerator (`combat/compendium_generator.py`)

**Purpose:** Main orchestrator for encounter generation.

#### Class: `Monster`
```python
@dataclass
class Monster:
    """A single monster from the compendium."""
    name: str                      # "Goblin", "Bugbear", etc.
    cr: float                      # Challenge Rating (0.125, 0.25, 1, 2, ...)
    xp: int                        # XP value (25, 50, 200, 450, ...)
    uuid: str                      # Foundry unique identifier
    size: str = "medium"           # "tiny", "small", "medium", "large", "huge", "gargantuan"
    has_multiattack: bool = False  # Can attack multiple times
    avg_damage_per_turn: float = 0.0  # Average DPR (future use)
```

#### Class: `CompendiumEncounterGenerator`

**Constructor:**
```python
def __init__(
    self,
    foundry: FoundryClient,
    scene_width: int = 800,
    scene_height: int = 600
)
```

**Public Methods:**

##### `async def generate(...) -> Dict[str, Any]`
```python
async def generate(
    party_level: int,              # 1-20
    party_size: int,               # 1-8
    difficulty: str = "medium",    # "trivial", "easy", "medium", "hard", "deadly"
    environment: Optional[str] = None,  # "underdark", "forest", "dungeon", etc.
    max_creatures: int = 5         # Max combatants
) -> Dict[str, Any]
```

**Returns:**
```python
{
    "creatures": [
        {
            "name": "Goblin",
            "cr": 0.125,
            "xp": 25,
            "uuid": "Compendium.dnd5e.monsters.Actor.abc123...",
            "size": "small"
        },
        ...
    ],
    "placements": [
        {
            "uuid": "Compendium.dnd5e.monsters.Actor.abc123...",
            "name": "Goblin",
            "x": 150,
            "y": 200,
            "hidden": False
        },
        ...
    ],
    "total_xp": 500,
    "difficulty_rating": "medium",
    "party_level": 5,
    "party_size": 4,
    "notes": "2 combatants: Goblin, Bugbear. Positioned: 1 front-line, 1 ranged."
}
```

**Behavior:**
1. Calculate XP budget from `DynamicDifficulty` based on party power
2. Query Foundry D&D 5e compendium for monsters matching CR range
3. Greedily select monsters that fit budget (prefer variety)
4. Position tactically on map (front-line vs. back-line)
5. Generate human-readable encounter notes
6. Return complete encounter data

**Error Handling:**
- If compendium not found: log warning, return empty encounter
- If no monsters match criteria: return "Empty encounter" with empty creatures list
- If placement out of bounds: clamp to scene boundaries

---

### 2. Query Module (within `CompendiumEncounterGenerator`)

**Method: `async def _query_compendium(max_cr: float, environment: Optional[str]) -> List[Monster]`**

**Purpose:** Query Foundry's D&D 5e compendium for monsters.

**Implementation Details:**

1. **Compendium Pack IDs** (vary by D&D 5e version):
   - Primary: `dnd5e.monsters`
   - Fallback: `dnd5e.bestiary`
   - Custom: May include third-party packs

2. **JavaScript Query** (via `foundry.execute_js`):
   ```javascript
   const pack = game.packs.get('dnd5e.monsters');
   const index = await pack.getIndex({
       fields: ['system.details.cr', 'system.details.environment', 'system.details.type']
   });
   return index.filter(m => {
       const cr = m.system?.details?.cr ?? 0;
       return cr >= 0.125 && cr <= max_cr + 2;
   }).slice(0, 20);
   ```

3. **Monster Data Extraction:**
   - Extract: `name`, `cr`, `uuid`, `size`, `environment`
   - Map CR to XP using the shared `CR_XP` table (single source of truth — do not redefine)
   - Filter out monsters with unknown CR values

4. **Return:** List of `Monster` objects (up to 60 candidates, CR band `[0, party_level + 3]`)

5. **Environment filter (soft — applied after query, in `_apply_environment_filter`):**
   - If `environment` given and **≥3** candidates' `environment` tag matches (substring, case-insensitive), restrict to matches.
   - Otherwise keep the full pool. Compendium environment data is sparse; never
     return an empty encounter just because a tag is missing. Either implement
     this soft filter or remove the parameter — do not leave it dead.

**Edge Cases:**
- CR not found in `CR_XP`: skip monster (log debug message)
- No compendium pack found: return empty list (log warning)
- Compendium query timeout: return empty list (log error)

---

### 3. Selection Module (within `CompendiumEncounterGenerator`)

**Method: `def _select_monsters_greedy(candidates, budget, party_size, max_creatures) -> List[Monster]`**

**Purpose:** Greedily select monsters whose **adjusted** XP fits the budget while
maximizing variety. (Adjusted XP, not raw sum — the multiplier rises with count.)

**Algorithm:**
```
0. ordered = candidates sorted by CR descending
   def fits(trial): return len(trial) <= max_creatures and adjusted_xp(trial, party_size) <= budget

1. PASS 1 — Variety (one of each name):
   FOR EACH monster IN ordered:
       IF monster.name IN seen_names: continue
       IF fits(selected + [monster]):
           selected.append(monster); seen_names.add(monster.name)

2. PASS 2 — Fill (allow duplicates):
   FOR EACH monster IN ordered:
       IF len(selected) >= max_creatures: break
       IF fits(selected + [monster]):
           selected.append(monster)

3. RETURN: selected (sorted by CR descending)
```

**Example:**
- Input: [Goblin (25 XP), Goblin (25 XP), Bugbear (200 XP)], budget=300, max=3
- Pass 1: [Goblin (25 XP), Bugbear (200 XP)] (250 XP, 1 goblin variety)
- Pass 2: [Goblin (25 XP), Bugbear (200 XP), Goblin (25 XP)] (275 XP, added second goblin)
- Return: Sorted by CR → [Bugbear, Goblin, Goblin]

**Constraints:**
- **Adjusted** XP ≤ budget (raw sum × encounter multiplier — see Budget math). Re-check after each add; the multiplier jumps with count.
- Count ≤ max_creatures
- Order candidates by CR descending so high-level parties get a real centerpiece, not only a swarm
- First pass prioritizes unique names (no all-goblin encounters)

---

### 4. Positioning Module (within `CompendiumEncounterGenerator`)

**Method: `def _position_tactically(monsters: List[Monster]) -> List[Dict[str, Any]]`**

**Purpose:** Place hostiles within the real scene bounds, snapped to the grid.

**Phase 1 is deliberately role-agnostic.** Do NOT infer melee/ranged from CR —
CR is not role (an Ogre is CR 2 melee; a CR ¼ kobold can be ranged). Real role
detection needs each stat block's actions (a per-monster document fetch) and is
deferred to Phase 2. Until then, cluster the group; don't fake a front/back line.

**Logic:**
```
1. cols = min(3, n); rows = ceil(n / cols); gs = grid_size
2. Center the block toward the right-center of the map:
     center = (scene_width * 0.65, scene_height * 0.5)
     origin  = center - (block_w/2, block_h/2)   # block_w=(cols-1)*gs, block_h=(rows-1)*gs
3. For each monster i:  r, c = divmod(i, cols)
     x = snap(origin_x + c*gs);  y = snap(origin_y + r*gs)   # snap = round(v/gs)*gs
     clamp x to [0, scene_width - gs], y to [0, scene_height - gs]
4. Return List[Dict] with uuid, name, cr, x, y, hidden=False
```

**Constraints:**
- All placements within [0, scene_width - gs] × [0, scene_height - gs] (no off-canvas, even on tiny scenes)
- Coordinates grid-snapped (`x % gs == 0`, `y % gs == 0`)
- **`cr` MUST be included** in each placement — the executor needs it for `ensure_monster_actor`

---

### 5. Notes Generation Module

**Method: `def _generate_notes(monsters: List[Monster], placements: List[Dict]) -> str`**

**Purpose:** Create human-readable encounter summary.

**Format:**
```
"{count} combatants: {name1}, {name2}, ...
Positioned: {front_count} front-line, {back_count} ranged."

OR (if only one type):
"{count} {type} combatants."
```

**Examples:**
- "3 combatants: Goblin, Bugbear, Ogre. Positioned: 2 front-line, 1 ranged."
- "4 melee combatants."
- "Empty encounter."

---

## Data Structures

### Monster Dataclass
```python
@dataclass
class Monster:
    name: str
    cr: float
    xp: int
    uuid: str
    size: str = "medium"
    has_multiattack: bool = False
    avg_damage_per_turn: float = 0.0
```

### Encounter Output
```python
{
    "creatures": List[Dict],      # Monster metadata (name, cr, xp, uuid)
    "placements": List[Dict],     # Positioned tokens (uuid, x, y, hidden)
    "total_xp": int,              # Sum of selected monsters' XP
    "difficulty_rating": str,     # Echoed difficulty level
    "party_level": int,           # For logging/reference
    "party_size": int,            # For logging/reference
    "notes": str                  # Human-readable summary
}
```

### XP_VALUES Mapping
```python
XP_VALUES = {
    0: 10,           # CR 0
    0.125: 25,       # CR 1/8
    0.25: 50,        # CR 1/4
    0.5: 100,        # CR 1/2
    1: 200,          # CR 1
    2: 450,          # CR 2
    3: 700,          # CR 3
    4: 1100,         # CR 4
    5: 1800,         # CR 5
    # ... up to CR 20
}
```

---

## Integration Points

### 1. Integration with `execute_generate_encounter()` (actions/executors.py)

**Current Signature (to update):**
```python
async def execute_generate_encounter(
    party_level: int,
    party_size: int,
    environment: Optional[str] = None,
    app_state = None,
    foundry: FoundryClient = None
) -> dict
```

**New Signature:**
```python
async def execute_generate_encounter(
    party_level: int,
    party_size: int,
    difficulty: str = "medium",           # NEW: required for budget
    environment: Optional[str] = None,
    app_state = None,
    foundry: FoundryClient = None
) -> dict
```

**Implementation:**
```python
# 1. Build the generator against the REAL scene (so placements land on canvas
#    and snap to its grid). Defaults are used only if the scene query fails.
scene_w, scene_h, grid = await _resolve_scene_dimensions(foundry)
gen = CompendiumEncounterGenerator(
    foundry=foundry, scene_width=scene_w, scene_height=scene_h, grid_size=grid
)

# 2. Generate encounter
encounter = await gen.generate(
    party_level=party_level,
    party_size=party_size,
    difficulty=difficulty,
    environment=environment,
)

# 3. Deploy to Foundry (if connected).
#    CRITICAL: compendium monsters are NOT world actors. You MUST import the
#    stat block into the world first (ensure_monster_actor), then place the
#    token by the returned world UUID. Placing by bare name fails for any
#    monster not already in the world — that was the original design's regression.
for placement in encounter["placements"]:
    world_uuid = await ensure_monster_actor(foundry, placement["name"], cr=placement["cr"])
    if not world_uuid:
        continue  # skip unresolved actors rather than placing a broken token
    token = await foundry.place_token(uuid=world_uuid, x=placement["x"], y=placement["y"], disposition=-1)
    if token and "error" not in token:
        tid = token.get("id") or token.get("token_id")
        if tid:
            placed_tokens.append(tid)

# 4. Start combat if tokens placed
if placed_tokens:
    await foundry.start_encounter(placed_tokens)

# 5. Return encounter data ({"deployed_to_foundry": ...} only when connected)
```

> `_resolve_scene_dimensions(foundry)` lives in `executors.py` and parses
> `get_scene_details()` defensively (width/height/grid vary by Foundry version).

### 2. Budget math (in compendium_generator.py — DO NOT use DynamicDifficulty.encounter_budget)

**Why not DynamicDifficulty:** its `encounter_budget` table is keyed by party
*size only* and has no level dimension — using it makes a level-1 and a level-20
party get the same budget. It is also consumed by other code, so we don't reshape
it. The generator carries its own correct tables (single source of truth).

**Budget formula (DMG p.82):**
```python
budget = per_character_threshold[level][difficulty] * party_size
```
- `LEVEL_XP_THRESHOLDS[level]` → `(easy, medium, hard, deadly)` for levels 1–20.
- `trivial` = `easy_threshold * 0.5`.
- Level is clamped to `[1, 20]`.

**Difficulty is measured with the encounter multiplier (count matters):**
```python
adjusted_xp = sum(monster_xp) * encounter_multiplier(count, party_size)
# multiplier tiers by count: 1, 2, 3-6, 7-10, 11-14, 15+  -> 1, 1.5, 2, 2.5, 3, 4
# party_size < 3 shifts one tier up; party_size >= 6 shifts one tier down.
```
An encounter fits when `adjusted_xp <= budget`. Selection re-checks adjusted XP
after each tentative add (the multiplier jumps with count, so a running raw sum
is wrong).

### 3. Integration with FoundryClient (foundry/client.py)

**Methods Called:**
- `execute_js(query: str) -> Any` — Query compendium
- `get_scene_details() -> Dict` — Read real scene width/height/grid
- `ensure_monster_actor(foundry, name, cr) -> Optional[str]` (campaign/monster_actor.py) — import compendium stat block into world, return world UUID
- `place_token(uuid=..., x, y, disposition) -> Dict` — Deploy token **by UUID**
- `start_encounter(token_ids: List) -> Dict` — Start combat

**No client changes needed** — but note `place_token` resolves **world** actors
only, which is why `ensure_monster_actor` must run first.

### 4. Action Schema (actions/schemas.py)

**Verify:** `GenerateEncounterAction` accepts `difficulty` parameter

**Current (check):**
```python
class GenerateEncounterAction(BaseModel):
    party_level: int = Field(..., ge=1, le=20)
    party_size: int = Field(..., ge=1, le=10)
    environment: Optional[str] = Field(None, max_length=100)
```

**Add (REQUIRED — the dispatcher derives executor kwargs from the schema via
`model_dump`, so `difficulty` will NOT reach the executor unless it is a schema
field):**
```python
# Pydantic v2 uses `pattern=`, NOT `regex=` (regex= raises in v2).
difficulty: Optional[str] = Field("medium", pattern="^(trivial|easy|medium|hard|deadly)$")
```

---

## Testing Requirements

### Unit Tests (`tests/test_compendium_generator.py`)

**Test Categories:**

1. **Monster Creation**
   - ✅ `test_monster_dataclass_creation`
   - ✅ `test_monster_xp_calculation`

2. **Generator Initialization**
   - ✅ `test_generator_default_scene_size`
   - ✅ `test_generator_custom_scene_size`

3. **Greedy Selection**
   - ✅ `test_selection_respects_budget`
   - ✅ `test_selection_prefers_variety` (first pass)
   - ✅ `test_selection_respects_max_creatures`
   - ✅ `test_selection_empty_candidates`

4. **Positioning**
   - ✅ `test_positioning_within_bounds`
   - ✅ `test_positioning_separates_front_and_back`
   - ✅ `test_positioning_spreads_y_axis`
   - ✅ `test_positioning_metadata_complete`

5. **Notes Generation**
   - ✅ `test_notes_includes_creature_count`
   - ✅ `test_notes_describes_positioning`
   - ✅ `test_notes_empty_encounter`

6. **Async Generation**
   - ✅ `test_generate_returns_valid_structure`
   - ✅ `test_generate_respects_difficulty`
   - ✅ `test_generate_with_environment_hint`
   - ✅ `test_generate_handles_empty_compendium`

**Coverage Goal:** >90% line coverage

### Integration Tests (`tests/test_execute_generate_encounter.py`)

1. **Executor Integration**
   - Verify `execute_generate_encounter()` calls generator
   - Verify placement tokens are created
   - Verify encounter returned with correct structure

2. **End-to-End**
   - Mock Foundry + compendium query
   - Generate encounter
   - Verify tokens placed
   - Verify combat started

### Performance Tests

- Generation time < 2 seconds (including compendium query)
- Memory usage < 50MB for 100-creature batch

---

## Implementation Phases

### Phase 1: MVP (Complete)
**Branch:** `feature/compendium-encounter-generator`

**Deliverables:**
- [x] `combat/compendium_generator.py` — Core generator
- [x] `tests/test_compendium_generator.py` — Unit tests
- [x] Update `execute_generate_encounter()` in executors.py
- [x] All Phase 1 tests passing

**Success Criteria:**
- 20+ unit tests passing
- No hallucinated monsters (all from compendium)
- Encounters respect difficulty ±10%
- Positioning tactical (front/back separated)

---

### Phase 2: Polish & Caching (Future)
**Branch:** `feature/compendium-caching` (new)

**Deliverables:**
- Monster compendium LRU cache (avoid repeated queries)
- Role detection (melee, ranged, caster)
- Environmental filtering (undead in crypt, aquatic in water)
- Better selection algorithm (avoid mono-type encounters)

**Success Criteria:**
- Repeated generation < 200ms (cache hit)
- Diverse encounters (no 5 goblins)
- Environment-aware placement

---

### Phase 3: Multi-System Support (Future)
**Branch:** `feature/compendium-multi-system`

**Deliverables:**
- Pathfinder 1e/2e support
- Sword & Sorcery/OSR systems
- Streaming generation (progressive UI feedback)

---

## Rollout Plan

### Step 1: Code Review
- Audit Phase 1 implementation
- Verify test coverage
- Check for regressions

### Step 2: Canary Testing
- Test with sample party (levels 1, 5, 10, 20)
- Verify encounter difficulty accuracy
- Measure generation time

### Step 3: Documentation
- Update README with new `execute_generate_encounter(difficulty=...)`
- Add troubleshooting guide (compendium not found, etc.)

### Step 4: Merge to Master
- Merge `feature/compendium-encounter-generator` → master
- Tag release version
- Notify users of improvement

---

## Known Limitations & Future Work

### Phase 1 Limitations (Acceptable)
- ⚠️ No multiattack damage calculation (not needed for balanced selection)
- ⚠️ Positioning is basic (not optimized for terrain)
- ⚠️ No caching (each call queries compendium)
- ⚠️ D&D 5e only (no Pathfinder, etc.)

### Phase 2 Improvements
- Monster caching (5-min TTL)
- Role-based positioning (melee front-center, ranged back-corners)
- Environmental awareness (crypt → undead, water → aquatic)

### Phase 3 Improvements
- Multi-system support
- Streaming generation (progressive UI feedback)
- Encounter dashboard (admin panel widget)

---

## Error Handling & Logging

### Logging Levels

**DEBUG:**
```
[CompendiumEncounter] Queried 15 candidates from dnd5e.monsters
[CompendiumEncounter] Placed Goblin at (150, 120)
```

**INFO:**
```
[CompendiumEncounter] Generated medium encounter: 3 combatants (Goblin, Bugbear, Ogre), XP: 625
[CompendiumEncounter] Deployed 3 tokens, started encounter
```

**WARNING:**
```
[CompendiumEncounter] No monsters found in compendium query; returning empty encounter
[CompendiumEncounter] Compendium pack 'dnd5e.monsters' not found
```

**ERROR:**
```
[CompendiumEncounter] Query failed: ExecuteJS timeout after 5s
[CompendiumEncounter] Generation failed: <exception message>
```

### Exception Handling

- **CompendiumNotFound:** Log warning, return empty encounter
- **QueryTimeout:** Log error, return empty encounter
- **InvalidPartyLevel:** Clamp to [1, 20], log debug
- **OutOfBounds Placement:** Clamp coordinates, continue

---

## Acceptance Criteria (Per Agent)

### For All Agents
- [ ] Code follows existing style (4-space indent, type hints, docstrings)
- [ ] All new methods have docstrings
- [ ] Unit tests written before code (TDD approach preferred)
- [ ] Tests pass: `pytest tests/test_compendium_generator.py -v`
- [ ] No import errors: `python3 -c "from combat.compendium_generator import CompendiumEncounterGenerator"`
- [ ] Logging uses module-level logger: `logger = logging.getLogger(__name__)`
- [ ] No external dependencies added (only stdlib + existing imports)

### For Implementation Agent
- [ ] `combat/compendium_generator.py` complete with all 5 modules
- [ ] XP_VALUES dict populated for CR 0–20
- [ ] `generate()` method returns correct structure
- [ ] Error handling for missing compendium/timeout

### For Testing Agent
- [ ] 20+ unit tests covering all code paths
- [ ] Edge cases tested (empty, 1 creature, max creatures)
- [ ] Integration test with mocked Foundry
- [ ] Coverage report: >90% lines

### For Integration Agent
- [ ] `execute_generate_encounter()` updated to call generator
- [ ] Schema updated (add `difficulty` param)
- [ ] Backward compatible (old code still works)
- [ ] E2E test passing (generator → foundry → combat)

---

## Communication & Handoff

### Daily Standup Template
```
Agent: [name]
Status: [In Progress | Blocked | Complete]
Completed: [what got done]
Blockers: [none | issue description]
Next: [what's next]
```

### Code Review Checklist
- [ ] Code compiles/runs without errors
- [ ] Tests pass
- [ ] Docstrings present and clear
- [ ] Logging appropriate
- [ ] No security issues
- [ ] Follows spec

### Merge Criteria
- [ ] All unit tests passing
- [ ] Integration test passing (if applicable)
- [ ] Code reviewed by 1 other agent
- [ ] No merge conflicts
- [ ] Spec requirements met

---

## References

### Existing Code
- `combat/difficulty.py` — DynamicDifficulty, PartyComposition
- `combat/mechanics.py` — CombatMechanics, TacticalAnalysis
- `foundry/client.py` — FoundryClient API
- `actions/executors.py` — Action execution pattern

### Foundry Compendium Docs
- Compendium Index API: https://foundryvtt.com/api/classes/client.CompendiumIndex.html
- Game Objects: https://foundryvtt.com/api/global.html#GameData

### D&D 5e XP Table
- CR 0–20 XP values (hardcoded in XP_VALUES dict)
- Challenge Rating definition: DMG pg. 274

---

## Questions & Escalation

**If you get stuck:**
1. Check existing code (combat/, foundry/, actions/)
2. Review this spec section again
3. Add issue as comment in code with `TODO` or `FIXME`
4. Escalate to the user for clarification

**Common Issues:**
- "Compendium not found" → Verify D&D 5e module installed
- "execute_js timeout" → Query might be too large; paginate results
- "Positioning out of bounds" → Check scene dimensions; clamp coordinates

---

## Sign-Off

**Approved By:** User  
**Date:** 2026-06-30  
**Revision:** 1.0  

**Next Review:** After Phase 1 completion (1 week estimated)
