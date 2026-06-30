# Task Breakdown — Compendium Encounter Generator

**Branch:** `feature/compendium-encounter-generator`  
**Status:** In Progress  
**Target Completion:** End of week  

---

## Available Tasks (Pick One)

### Task Group A: Core Implementation

#### A1: Query Module Implementation
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 2-3 hours  
**Dependencies:** None  

**Objective:**
Implement `_query_compendium()` method in `CompendiumEncounterGenerator`.

**Specification:**
- Read SPEC section "Query Module"
- Method signature: `async def _query_compendium(self, max_cr: float, environment: Optional[str]) -> List[Monster]`
- Query Foundry D&D 5e compendium via `self.foundry.execute_js()`
- Return list of `Monster` objects (max 20)
- Handle edge cases: no compendium, timeout, missing CR values

**Acceptance Criteria:**
- [ ] Method defined in `combat/compendium_generator.py`
- [ ] Queries `dnd5e.monsters` compendium
- [ ] Returns `List[Monster]` with name, cr, xp, uuid
- [ ] XP values mapped from CR (use XP_VALUES dict)
- [ ] Logging at DEBUG/WARN/ERROR levels
- [ ] Tested in unit test: `test_query_compendium_*`

**Testing:**
- Test with mock Foundry returning 5 monsters
- Test with empty compendium (returns [])
- Test with timeout (returns [] after 5s)

**Example Commit:**
```
feat: implement query module for compendium encounter generator

- Query dnd5e.monsters via execute_js
- Map CR to XP values
- Return Monster objects
- Handle errors gracefully
```

---

#### A2: Greedy Selection Module Implementation
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 2 hours  

**Objective:**
Implement `_select_monsters_greedy()` method.

**Specification:**
- Read SPEC section "Selection Module"
- Method signature: `def _select_monsters_greedy(self, candidates: List[Monster], budget: float, max_creatures: int) -> List[Monster]`
- Two-pass algorithm: (1) variety, (2) fill budget
- No external dependencies

**Acceptance Criteria:**
- [ ] Method defined
- [ ] Pass 1: Select unique monster names first (variety)
- [ ] Pass 2: Fill remaining budget with duplicates
- [ ] Result sorted by CR (descending)
- [ ] Total XP ≤ budget
- [ ] Count ≤ max_creatures
- [ ] Tested in 4+ unit tests

**Testing:**
- `test_greedy_selection_fits_budget`
- `test_greedy_selection_prefers_variety`
- `test_greedy_selection_empty_input`
- `test_greedy_selection_max_creatures_respected`

---

#### A3: Tactical Positioning Module Implementation
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 2-3 hours  

**Objective:**
Implement `_position_tactically()` method.

**Specification:**
- Read SPEC section "Positioning Module"
- Method signature: `def _position_tactically(self, monsters: List[Monster]) -> List[Dict[str, Any]]`
- Separate front-line (CR ≤ 1) and back-line (CR > 1)
- Position with Y-axis spread, clamp to bounds
- Return list of placement dicts

**Acceptance Criteria:**
- [ ] Method defined
- [ ] Front-line at x=150, back-line at x=500
- [ ] Y-axis spread: `(index + 1) * (scene_height / (count + 1))`
- [ ] All coordinates within [0, scene_width] × [0, scene_height]
- [ ] Returns dicts with: uuid, name, x, y, hidden
- [ ] Tested in 4+ unit tests

**Testing:**
- `test_positioning_within_bounds`
- `test_positioning_front_back_separation`
- `test_positioning_y_spread`
- `test_positioning_metadata`

---

#### A4: Notes Generation Module Implementation
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 1 hour  

**Objective:**
Implement `_generate_notes()` method.

**Specification:**
- Read SPEC section "Notes Generation Module"
- Method signature: `def _generate_notes(self, monsters: List[Monster], placements: List[Dict]) -> str`
- Return format: `"{count} combatants: {names}. Positioned: {front} front, {back} ranged."`

**Acceptance Criteria:**
- [ ] Returns readable string
- [ ] Includes creature count
- [ ] Lists monster names (comma-separated)
- [ ] Describes positioning (front vs. back)
- [ ] Handles empty encounter ("Empty encounter.")
- [ ] Tested in 3+ unit tests

**Testing:**
- `test_notes_includes_count`
- `test_notes_describes_positioning`
- `test_notes_empty_encounter`

---

### Task Group B: Async Orchestration

#### B1: Main `generate()` Method Implementation
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 2-3 hours  
**Dependencies:** A1, A2, A3, A4 (but can start in parallel)

**Objective:**
Implement main `async def generate(...)` method that orchestrates all 5 modules.

**Specification:**
- Read SPEC section "CompendiumEncounterGenerator.generate(...)"
- Signature: `async def generate(party_level, party_size, difficulty, environment, max_creatures) -> Dict`
- Steps: calculate budget → query → select → position → generate notes
- Return full encounter dict

**Acceptance Criteria:**
- [ ] Method defined, async
- [ ] Calls `_query_compendium()`
- [ ] Calls `_select_monsters_greedy()`
- [ ] Calls `_position_tactically()`
- [ ] Calls `_generate_notes()`
- [ ] Returns correct dict structure
- [ ] Logging at key steps (INFO level)
- [ ] Handles exceptions gracefully (logs ERROR, returns error dict)
- [ ] Tested in 5+ unit tests

**Testing:**
- `test_generate_returns_valid_structure`
- `test_generate_respects_difficulty`
- `test_generate_with_environment`
- `test_generate_empty_compendium`
- `test_generate_performance` (< 2 seconds)

---

### Task Group C: Integration & Schema

#### C1: Update `execute_generate_encounter()` Executor
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 1-2 hours  
**Dependencies:** B1 (core generator working)

**Objective:**
Update `actions/executors.py` to use `CompendiumEncounterGenerator`.

**Specification:**
- Read SPEC section "Integration with execute_generate_encounter()"
- Update function signature to add `difficulty` parameter
- Instantiate `CompendiumEncounterGenerator`
- Call `generate()` with user inputs
- Deploy tokens to Foundry (reuse existing `place_token` logic)
- Return encounter data

**Acceptance Criteria:**
- [ ] Function updated
- [ ] Calls `CompendiumEncounterGenerator(foundry)`
- [ ] Calls `await gen.generate(...)`
- [ ] Deploys tokens via `foundry.place_token()`
- [ ] Starts combat via `foundry.start_encounter()`
- [ ] Returns correct structure: `{"type": "generate_encounter", "encounter": {...}, ...}`
- [ ] Backward compatible (existing code still works)
- [ ] Logging at INFO level
- [ ] Tested in integration test

**Testing:**
- `test_execute_generate_encounter_calls_generator`
- `test_execute_generate_encounter_deploys_tokens`
- `test_execute_generate_encounter_starts_combat`

---

#### C2: Update Action Schema
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 30 minutes  
**Dependencies:** C1

**Objective:**
Update `GenerateEncounterAction` in `actions/schemas.py` to include `difficulty` field.

**Specification:**
- Add `difficulty: Optional[str]` field with regex validation
- Allowed values: "trivial", "easy", "medium", "hard", "deadly"
- Default: "medium"
- Max length: 10 characters

**Acceptance Criteria:**
- [ ] Field added to `GenerateEncounterAction`
- [ ] Type: `Optional[str]`
- [ ] Validation: regex pattern `^(trivial|easy|medium|hard|deadly)$`
- [ ] Default: "medium"
- [ ] No extra fields allowed (extra="forbid")
- [ ] Tested in schema test

**Testing:**
- `test_generate_encounter_action_valid_difficulties`
- `test_generate_encounter_action_invalid_difficulty_rejected`
- `test_generate_encounter_action_default_medium`

---

### Task Group D: Testing

#### D1: Unit Test Suite — Selection & Positioning
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 3-4 hours  
**Dependencies:** None (write tests first)

**Objective:**
Write comprehensive unit tests for selection and positioning modules.

**File:** `tests/test_compendium_generator.py`

**Tests Required:**
- Selection: 4+ tests (budget, variety, max_creatures, empty)
- Positioning: 4+ tests (bounds, front/back, y-spread, metadata)
- Notes: 3+ tests (count, positioning, empty)

**Acceptance Criteria:**
- [ ] File created: `tests/test_compendium_generator.py`
- [ ] 15+ total unit tests
- [ ] All tests pass: `pytest tests/test_compendium_generator.py -v`
- [ ] Coverage > 90% for compendium_generator.py
- [ ] Tests follow existing style (assert statements, clear names)
- [ ] Each test has docstring explaining what it verifies

**Test Template:**
```python
def test_selection_respects_budget():
    """Greedy selection should not exceed XP budget."""
    gen = CompendiumEncounterGenerator(foundry=MagicMock())
    candidates = [...]
    budget = 500
    
    selected = gen._select_monsters_greedy(candidates, budget, max_creatures=5)
    
    assert sum(m.xp for m in selected) <= budget
```

---

#### D2: Integration Test Suite — Full Pipeline
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 2-3 hours  
**Dependencies:** C1 (executor updated)

**Objective:**
Write integration tests for the full generate → deploy → combat pipeline.

**File:** `tests/test_compendium_integration.py` (new)

**Tests Required:**
- Generator → Foundry deployment
- Executor integration
- Encounter difficulty accuracy
- Error handling

**Acceptance Criteria:**
- [ ] File created: `tests/test_compendium_integration.py`
- [ ] 5+ integration tests
- [ ] All tests pass
- [ ] Mock Foundry client used (no real API calls)
- [ ] Tests cover happy path + error cases

**Test Outline:**
```python
async def test_full_pipeline_easy_encounter():
    """Easy encounter generation should be < 200 XP."""
    # Mock Foundry
    # Call execute_generate_encounter(party_level=3, party_size=4, difficulty="easy")
    # Assert result XP < 200
    # Assert tokens placed
    # Assert encounter started

async def test_deployment_failure_handling():
    """If Foundry not connected, should return encounter data anyway."""
    # ...
```

---

### Task Group E: Documentation & Review

#### E1: Code Review & Audit
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 2 hours  
**Dependencies:** A1-C2 (all code complete)

**Objective:**
Audit the complete implementation for correctness, style, and performance.

**Checklist:**
- [ ] All code follows project style (4-space indent, type hints, docstrings)
- [ ] No external dependencies added
- [ ] All logging uses module-level logger
- [ ] Error handling present (try/except, graceful degradation)
- [ ] No SQL injection risks (N/A — no SQL)
- [ ] No security vulnerabilities
- [ ] Performance acceptable (< 2s generation)
- [ ] Tests passing (20+)
- [ ] Code coverage > 90%
- [ ] No merge conflicts
- [ ] Spec requirements met

**Deliverable:**
- Review summary document (pass/fail per criterion)
- List of issues (if any) for rework
- Sign-off: APPROVED or NEEDS REWORK

---

#### E2: Integration Testing & Canary
**Status:** ⏳ Available  
**Assigned To:** [Open]  
**Effort:** 2 hours  
**Dependencies:** All Phase 1 complete

**Objective:**
Test the feature end-to-end with various party levels and difficulties.

**Test Scenarios:**
- Party level 1, size 4, difficulty "easy"
- Party level 5, size 4, difficulty "medium"
- Party level 10, size 4, difficulty "hard"
- Party level 20, size 4, difficulty "deadly"
- Edge case: party level 1, size 1

**For Each Scenario:**
- [ ] Generate encounter
- [ ] Verify XP within expected range (±10%)
- [ ] Verify creature count 1–5
- [ ] Verify all creatures are real D&D 5e monsters
- [ ] Verify positioning within scene bounds
- [ ] Measure generation time (should be < 2s)

**Deliverable:**
- Test report with 5 scenario results
- Performance metrics
- Pass/fail for each scenario

---

## Task Assignments & Schedule

### Week 1
| Date | Task | Assigned | Status |
|------|------|----------|--------|
| Mon | A1 (Query) | [TBD] | 🟡 In Progress |
| Mon | D1 (Unit Tests) | [TBD] | 🟡 In Progress |
| Tue | A2 (Selection) | [TBD] | 🟠 Queued |
| Tue | A3 (Positioning) | [TBD] | 🟠 Queued |
| Tue | D2 (Integration Tests) | [TBD] | 🟠 Queued |
| Wed | A4 (Notes) | [TBD] | 🟠 Queued |
| Wed | B1 (generate() method) | [TBD] | 🟠 Queued |
| Thu | C1 (Executor Integration) | [TBD] | 🟠 Queued |
| Thu | C2 (Schema Update) | [TBD] | 🟠 Queued |
| Fri | E1 (Code Review) | [TBD] | 🟠 Queued |
| Fri | E2 (Canary Testing) | [TBD] | 🟠 Queued |

### Legend
- 🟢 Complete
- 🟡 In Progress
- 🟠 Queued
- 🔴 Blocked

---

## How to Pick a Task

1. **Check the status** — Find a task marked 🟠 Queued or 🟡 In Progress
2. **Review dependencies** — Verify required tasks are complete
3. **Comment in this file** — Update "Assigned To: [Your Name]"
4. **Read the spec** — Review SPEC_COMPENDIUM_ENCOUNTER_GENERATOR.md section
5. **Write tests first** (TDD) — Start with unit tests
6. **Implement code** — Make tests pass
7. **Commit** — Create clear commit message (see examples in each task)
8. **Mark complete** — Update status to 🟢 Complete

---

## Example Workflow (Task A1)

### Step 1: Claim the Task
```
Repo: TASKS_COMPENDIUM_ENCOUNTER.md
Line 24: "Assigned To: [OpenQuery Agent Alpha]"
```

### Step 2: Create Feature Branch (if needed)
```bash
git checkout feature/compendium-encounter-generator
git checkout -b task/a1-query-module
```

### Step 3: Review Spec
- Read: SPEC_COMPENDIUM_ENCOUNTER_GENERATOR.md → "Query Module"
- Understand: Compendium query, Monster dataclass, error handling

### Step 4: Write Tests First (TDD)
```python
# In tests/test_compendium_generator.py

async def test_query_returns_monsters():
    """_query_compendium should return Monster objects."""
    gen = CompendiumEncounterGenerator(foundry=AsyncMock())
    gen.foundry.execute_js = AsyncMock(return_value=[
        {"name": "Goblin", "cr": 0.125, "uuid": "uuid1"},
        {"name": "Bugbear", "cr": 1, "uuid": "uuid2"},
    ])
    
    result = await gen._query_compendium(max_cr=5, environment=None)
    
    assert len(result) == 2
    assert result[0].name == "Goblin"
    assert result[0].cr == 0.125
    assert result[0].xp == 25
```

### Step 5: Implement Code
```python
# In combat/compendium_generator.py

async def _query_compendium(self, max_cr: float, environment: Optional[str]) -> List[Monster]:
    """Query Foundry D&D 5e compendium for monsters."""
    try:
        js_query = f"""..."""
        monsters_raw = await self.foundry.execute_js(js_query)
        
        monsters = []
        for m in monsters_raw:
            cr = m.get("cr", 0)
            xp = self.XP_VALUES.get(cr, 0)
            if xp > 0:
                monsters.append(Monster(
                    name=m.get("name", "Unknown"),
                    cr=cr,
                    xp=xp,
                    uuid=m.get("uuid", "")
                ))
        
        logger.debug(f"Queried {len(monsters)} monsters")
        return monsters
    
    except Exception as e:
        logger.error(f"Query failed: {e}", exc_info=True)
        return []
```

### Step 6: Run Tests
```bash
pytest tests/test_compendium_generator.py::test_query_returns_monsters -v
pytest tests/test_compendium_generator.py -v
```

### Step 7: Commit
```bash
git add combat/compendium_generator.py tests/test_compendium_generator.py
git commit -m "feat(A1): implement query module for compendium encounter generator

- Query dnd5e.monsters via execute_js
- Map CR to XP values
- Return Monster objects
- Handle errors gracefully

Fixes compendium encounter generation."
```

### Step 8: Update Task Status
```
Repo: TASKS_COMPENDIUM_ENCOUNTER.md
Line 24: "Assigned To: Query Agent Alpha"
Line 22: "Status: ✅ Complete"
```

---

## Troubleshooting

### "Module not found"
```
Error: from combat.compendium_generator import CompendiumEncounterGenerator
```
**Solution:** Check file exists and is in correct location:
```
ai-engine/combat/compendium_generator.py
```

### "Test failures"
```
FAILED test_selection_respects_budget - AssertionError: ...
```
**Solution:**
1. Read test docstring (what should it do?)
2. Read spec section (expected behavior?)
3. Debug: print intermediate values
4. Fix implementation

### "Merge conflicts"
**Solution:**
1. Coordinate with other agents via TASKS_COMPENDIUM_ENCOUNTER.md comments
2. Test locally: `pytest tests/test_compendium_generator.py -v`
3. Resolve conflicts, run tests again
4. Commit merge

### "Performance issue"
```
Generation time: 5.2 seconds (target: < 2s)
```
**Solution:**
1. Profile: measure which step is slow (query, selection, positioning)
2. Optimize slowest step (usually query)
3. Consider caching in Phase 2

---

## Sign-Off & Completion

**Task Group Completion Criteria:**

- [ ] All 11 tasks assigned and in progress (or complete)
- [ ] All code compiles and runs without errors
- [ ] All 20+ unit tests passing
- [ ] Integration tests passing
- [ ] Code review approved (E1)
- [ ] Canary testing passed (E2)
- [ ] No merge conflicts
- [ ] Branch ready to merge: `feature/compendium-encounter-generator` → `master`

**Final Sign-Off:**
- [ ] User approves Phase 1 deliverables
- [ ] Merge to master
- [ ] Tag release

---

## Questions?

Add a comment in this file with `[QUESTION]` prefix and your agent name. Example:

```
[QUESTION] Agent Alpha: Does the compendium pack ID change between D&D 5e versions?
```

The user will respond with clarifications.

---

**Last Updated:** 2026-06-30  
**Completion Target:** End of Week  
**Status:** 🔴 Not Started
