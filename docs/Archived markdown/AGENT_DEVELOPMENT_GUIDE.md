# Agent Development Guide — Compendium Encounter Generator

**For:** Team of locally running agents  
**Purpose:** Distributed development using spec-driven architecture  
**Status:** Ready for assignment  

---

## What You Have

### 1. Complete Architecture Specification
**File:** `SPEC_COMPENDIUM_ENCOUNTER_GENERATOR.md`

This document contains everything an agent needs to know:
- System architecture diagram
- Module specifications (exact method signatures)
- Data structures (dataclasses, dicts, return types)
- Integration points (how it connects to existing code)
- Testing requirements (20+ tests needed)
- 3-phase rollout plan
- Error handling and logging standards

**How to use it:**
- Each agent reads the spec section for their assigned task
- Spec is the source of truth (not the code, not assumptions)
- If something's unclear, ask the user via `[QUESTION]` comments in TASKS_COMPENDIUM_ENCOUNTER.md

---

### 2. Task Breakdown with Assignments
**File:** `TASKS_COMPENDIUM_ENCOUNTER.md`

11 specific tasks divided into 5 groups:

| Group | Tasks | Effort | Status |
|-------|-------|--------|--------|
| **A** (Core) | A1-A4 Query, Selection, Positioning, Notes | 7h | 🟠 Queued |
| **B** (Async) | B1 generate() orchestrator | 3h | 🟠 Queued |
| **C** (Integration) | C1-C2 Executor + Schema | 2h | 🟠 Queued |
| **D** (Testing) | D1-D2 Unit + Integration tests | 5h | 🟠 Queued |
| **E** (Review) | E1-E2 Code audit + Canary | 4h | 🟠 Queued |

**How to use it:**
- Agents claim tasks by updating "Assigned To" field
- Each task has dependencies (can parallelize where possible)
- Follow the example workflow (Section: "How to Pick a Task")
- Update status as you progress (🟡 In Progress → 🟢 Complete)

---

### 3. Skeleton Implementation (Ready for Coding)
**File:** `ai-engine/combat/compendium_generator.py`

Contains:
- `Monster` dataclass (complete)
- `CompendiumEncounterGenerator` class (all method signatures)
- `XP_VALUES` mapping (CR 0-20, complete)
- Docstrings for every method (implementation to come)
- Import statements and logger setup

**What agents need to do:**
- Each agent fills in ONE method (or group of methods)
- Tests are already written (fail initially, pass when implemented)
- TDD approach: tests → implementation

---

### 4. Test Suite (23 Tests, All Ready)
**File:** `ai-engine/tests/test_compendium_generator.py`

Contains:
- ✅ 10 unit tests for individual modules (currently all passing with mocks)
- ✅ 8 validation tests (edge cases, bounds, constraints)
- ✅ 5 async integration tests (full pipeline)

**What agents need to know:**
- Tests are ALREADY WRITTEN — don't change them (unless spec changes)
- Tests use TDD: they define expected behavior
- Run: `pytest tests/test_compendium_generator.py -v`
- Goal: all 23 tests passing (green)

---

## How to Assign Tasks to Agents

### Option 1: Sequential (Recommended for Team Sync)
```
Day 1:  Assign A1 (Query) + D1 (Unit Tests)
        Both agents work independently, both needed for Day 2

Day 2:  Assign A2 (Selection), A3 (Positioning), A4 (Notes)
        Can parallelize; all depend on A1 completing

Day 3:  Assign B1 (Main generate() method)
        Orchestrates A1-A4; can begin once A1 is done

Day 4:  Assign C1 (Executor Integration) + D2 (Integration Tests)
        Final integration

Day 5:  Assign C2 (Schema) + E1 (Code Review) + E2 (Canary)
        Final polish and testing
```

### Option 2: Parallel (Maximum Speed)
- Assign A1, A2, A3, A4, D1 concurrently (no dependencies on each other)
- Day 2: Once A1-A4 done, assign B1 + D2
- Day 3: Once B1 done, assign C1 + C2 + E1 + E2

---

## Agent Responsibilities (TDD Workflow)

Each agent follows this pattern for their assigned task(s):

### Step 1: Understand the Spec
```
1. Read SPEC_COMPENDIUM_ENCOUNTER_GENERATOR.md
   - Find your task's section (e.g., "Query Module")
   - Read the full section (algorithm, constraints, error handling)

2. Read TASKS_COMPENDIUM_ENCOUNTER.md
   - Find your task (e.g., "A1: Query Module")
   - Review acceptance criteria
   - Review test outline
```

### Step 2: Examine Existing Tests
```bash
cd ai-engine
grep -A 20 "def test_query" tests/test_compendium_generator.py
# Understand what the test expects
```

### Step 3: Write/Update Tests (TDD)
```bash
# If test doesn't exist, write it based on spec
# If test exists, review it and ensure it matches spec
pytest tests/test_compendium_generator.py -v
# Tests should FAIL (red) at this point
```

### Step 4: Implement Code
```python
# Implement ONE method in combat/compendium_generator.py
# Make tests pass

# Follow these rules:
# - Use type hints
# - Add docstrings
# - Use logging (logger.debug, logger.warning, logger.error)
# - Match existing code style (4-space indent)
# - Don't add dependencies (only stdlib + existing imports)
```

### Step 5: Verify Tests Pass
```bash
pytest tests/test_compendium_generator.py -v
# All your tests should be GREEN
```

### Step 6: Commit Code
```bash
git add ai-engine/combat/compendium_generator.py tests/test_compendium_generator.py
git commit -m "feat(A1): implement query module for compendium encounter generator

- Query dnd5e.monsters via execute_js
- Map CR to XP values
- Return Monster objects
- Handle errors gracefully

[Describe any edge cases or decisions]"
```

### Step 7: Update Task Status
```bash
# Edit TASKS_COMPENDIUM_ENCOUNTER.md
# Update: "Assigned To: [Your Name]"
# Update: "Status: ✅ Complete"
git add TASKS_COMPENDIUM_ENCOUNTER.md
git commit -m "chore: mark task A1 complete"
```

---

## Coordination & Communication

### Daily Standup (Required)
Each agent posts to the branch comments:
```
[STANDUP] Agent Name
Status: [In Progress | Blocked | Complete]
Task: A1 (Query Module)
Completed Today:
  - Implemented _query_compendium() method
  - 4 unit tests passing
Blockers: None
Next: Optimize compendium query performance

[If blocked, add details so others can help]
```

### Questions? Ask via [QUESTION] Tags
```
[QUESTION] Agent Alpha: Does the compendium pack ID change between D&D 5e versions?

[Expected to be answered within 1 hour by the user]
```

### Code Review Checklist
Before marking a task complete, verify:
- [ ] All tests pass: `pytest tests/test_compendium_generator.py -v`
- [ ] Code compiles: `python3 -c "from combat.compendium_generator import ..."`
- [ ] Docstrings present on all methods
- [ ] Logging appropriate (debug/info/warn/error used correctly)
- [ ] No new external dependencies
- [ ] Follows existing code style
- [ ] Spec requirements met

---

## Example: Task A1 (Query Module)

### Spec (From SPEC_COMPENDIUM_ENCOUNTER_GENERATOR.md)
```
Method: _query_compendium(max_cr: float, environment: Optional[str]) -> List[Monster]

Purpose: Query Foundry D&D 5e compendium for monsters.

Implementation:
1. Execute JavaScript to query compendium index
2. Filter by CR (0.125 to max_cr + 2)
3. Extract name, cr, uuid
4. Map CR to XP (use XP_VALUES dict)
5. Return List[Monster]

Error Handling:
- If compendium not found: log warning, return []
- If query timeout: log error, return []
- If CR not in XP_VALUES: skip that monster, log debug

Constraints:
- Max 20 monsters returned
- No external dependencies
```

### Test (From tests/test_compendium_generator.py)
```python
async def test_query_compendium_returns_monsters():
    """_query_compendium should return Monster objects from compendium."""
    gen = CompendiumEncounterGenerator(foundry=AsyncMock())
    
    # Mock the JS query result
    gen.foundry.execute_js = AsyncMock(return_value=[
        {"name": "Goblin", "cr": 0.125, "uuid": "uuid1"},
        {"name": "Bugbear", "cr": 1, "uuid": "uuid2"},
    ])
    
    result = await gen._query_compendium(max_cr=5, environment=None)
    
    assert len(result) == 2
    assert result[0].name == "Goblin"
    assert result[0].cr == 0.125
    assert result[0].xp == 25
    assert result[1].name == "Bugbear"
    assert result[1].xp == 200
```

### Implementation (What You Write)
```python
async def _query_compendium(
    self, max_cr: float, environment: Optional[str] = None
) -> List[Monster]:
    """Query Foundry D&D 5e compendium for monsters."""
    try:
        # Build JavaScript query
        js_query = f"""
        (async () => {{
            const pack = game.packs.get('dnd5e.monsters');
            if (!pack) return [];
            
            const index = await pack.getIndex({{
                fields: ['system.details.cr']
            }});
            
            return index
                .filter(m => {{
                    const cr = m.system?.details?.cr ?? 0;
                    return cr >= 0.125 && cr <= {max_cr + 2};
                }})
                .slice(0, 20)
                .map(m => ({{
                    name: m.name,
                    cr: m.system?.details?.cr ?? 0,
                    uuid: m.uuid
                }}));
        }})()
        """
        
        # Execute query
        monsters_raw = await self.foundry.execute_js(js_query)
        
        if not monsters_raw:
            logger.debug("[CompendiumEncounter] No monsters found in query")
            return []
        
        # Convert to Monster objects
        monsters = []
        for m in monsters_raw:
            cr = m.get("cr", 0)
            xp = self.XP_VALUES.get(cr, 0)
            
            if xp > 0:  # Only include if we know the XP value
                monsters.append(Monster(
                    name=m.get("name", "Unknown"),
                    cr=cr,
                    xp=xp,
                    uuid=m.get("uuid", "")
                ))
            else:
                logger.debug(f"[CompendiumEncounter] Unknown CR {cr}, skipping")
        
        logger.debug(f"[CompendiumEncounter] Queried {len(monsters)} monsters")
        return monsters
    
    except Exception as e:
        logger.error(f"[CompendiumEncounter] Query failed: {e}", exc_info=True)
        return []
```

### Run Tests
```bash
$ pytest tests/test_compendium_generator.py::test_query_compendium_returns_monsters -v
PASSED test_query_compendium_returns_monsters
```

### Commit
```bash
$ git commit -m "feat(A1): implement query module for compendium encounter generator

- Query dnd5e.monsters via execute_js
- Map CR to XP values  
- Return Monster objects
- Handle errors gracefully (no compendium, timeout, unknown CR)"
```

---

## Merge & Rollout Plan

### Once All Tasks Complete (All ✅)

1. **Code Review (E1 Agent)**
   - Audit all code for style, security, performance
   - Verify spec compliance
   - Approve or request changes

2. **Canary Testing (E2 Agent)**
   - Test with 5 party scenarios (levels 1, 5, 10, 20 + edge case)
   - Verify XP accuracy, positioning, performance
   - Report results

3. **Merge to Master**
   ```bash
   git checkout master
   git pull origin master
   git merge feature/compendium-encounter-generator
   git push origin master
   ```

4. **Tag Release**
   ```bash
   git tag v1.1.0-compendium-encounters
   git push origin v1.1.0-compendium-encounters
   ```

---

## Success Criteria (Definition of Done)

### Phase 1 Complete When:
- [ ] All 11 tasks marked ✅ Complete
- [ ] All 23 unit tests passing
- [ ] All 5 integration tests passing
- [ ] Code review approved (E1)
- [ ] Canary testing passed (E2)
- [ ] No merge conflicts
- [ ] Branch merged to master
- [ ] Release tagged

---

## Troubleshooting

### Common Issues

**"I don't understand the spec"**
- Re-read the relevant section
- Look at the example code
- Post a [QUESTION] in TASKS_COMPENDIUM_ENCOUNTER.md
- Ask in standup

**"Test is failing but I don't know why"**
- Print intermediate values in your code
- Compare actual vs. expected (from test)
- Check spec for constraints
- Ask another agent for a second opinion

**"My commit has conflicts"**
- Pull latest from feature branch
- Resolve conflicts manually (or ask for help)
- Re-run tests
- Recommit

**"Performance is slow"**
- Profile: which step is slow?
- Optimize that step
- Benchmark before/after
- Document optimization in commit message

---

## Timeline Estimate

| Phase | Duration | Agents | Output |
|-------|----------|--------|--------|
| Parallel A1+D1 | 1 day | 2 agents | Query + test framework |
| Parallel A2/A3/A4 | 1 day | 3 agents | Selection, positioning, notes |
| B1 | 0.5 day | 1 agent | Main orchestrator |
| Parallel D2+C1 | 0.5 day | 2 agents | Integration tests + executor |
| C2+E1+E2 | 0.5 day | 3 agents | Schema, review, canary |
| **Total** | **~3-4 days** | **Up to 5 agents** | **Phase 1 complete** |

---

## Questions?

Use the [QUESTION] format in TASKS_COMPENDIUM_ENCOUNTER.md:

```
[QUESTION] Agent Alpha: How do I test the query if the compendium pack doesn't exist locally?

Answer will appear here within 1 hour.
```

---

## Next Steps

1. **User assigns tasks** — Each agent claims a task from TASKS_COMPENDIUM_ENCOUNTER.md
2. **Agents read spec** — Each reviews the relevant SPEC section
3. **Agents code** — Follow TDD workflow (tests → implementation → commit)
4. **Daily standup** — Each agent posts progress
5. **Merge & test** — Once all tasks complete, merge and run canary
6. **User audits** — You review the work, request changes if needed

---

**Ready to begin? Assign tasks!**

Pick agents, assign tasks from TASKS_COMPENDIUM_ENCOUNTER.md, and they can start immediately.

**Current Status:** 🔴 Not Started (Awaiting task assignments)
