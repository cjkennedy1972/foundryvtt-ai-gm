# Implementation Tasks: From Architectural Decisions

## Decisions Applied

| Decision | You Said | Recommendation | Action |
|----------|----------|-----------------|--------|
| **NPC autonomy** | Flavor (don't impact campaign outcome) | Correct design | ✅ No retry feedback needed |
| **Event-sourcing consumer** | Recommend best approach | **Audit trail first** | Build session replay/debug tool |
| **History tagging** | Use best approach | **No action needed** | NPCs not in shared history today |
| **Scene automation** | Complete it | Verify + wire if needed | Audit: fog/hazard/sound/macros |

---

## Task 1: NPC Retry Feedback — SKIP ✅

**Decision:** NPCs are flavor, fail safely. No self-correction loop needed.

**Action:** Document this as intentional design. Add comment to `NPCAgent.act()`:

```python
async def act(self, session_id: str, triggering_event: dict) -> List[Ruling]:
    """Ask the NPC-tier model for this NPC's response to *triggering_event*.
    
    Referee rejection is final (fails safe). Unlike player LLM, NPCs don't get
    same-turn retry feedback because they are flavor, not campaign-critical.
    A rejected NPC simply doesn't act that turn.
    """
```

**Effort:** 0 (documentation only)
**Test:** Existing NPC tests already verify fail-safe behavior
**Impact:** Clarifies design intent; unblocks scope creep

---

## Task 2: Event-Sourcing Consumer — BUILD AUDIT TRAIL ✅

**Recommendation:** Implement **audit trail / session replay** as the primary consumer.

**Why this over the other two:**
- **Real-time projection**: Expensive rebuild every turn; no observed use case
- **NPC memory fuel**: Blocked by NPC identity mapping; future work
- **Audit trail**: Zero cost (events already logged); high value immediately (session debugging, GM audit, replay for learning)

### 2a: Session Replay API

**File:** `events/replay.py` (new)

```python
class SessionReplay:
    """Query and replay session events for debugging and audit."""
    
    def __init__(self, event_store: EventStore):
        self.event_store = event_store
    
    async def get_session_transcript(self, session_id: str, limit: int = None) -> List[dict]:
        """All events in human-readable form (narration, NPC names, action summaries)."""
        events = await self.event_store.get_events(session_id, limit=limit)
        return [self._humanize(e) for e in events]
    
    async def get_state_at_time(self, session_id: str, timestamp: float) -> dict:
        """World state after all events up to timestamp."""
        events = await self.event_store.get_events(session_id)
        return await self.event_store.replay(session_id, events_before=timestamp)
    
    async def find_events_by_type(self, session_id: str, event_type: str) -> List[dict]:
        """All ACTION_RESOLVED, NPC_MOVED, etc. in a session."""
        pass
    
    def _humanize(self, event: dict) -> dict:
        """Convert event to human-readable format."""
        event_type = event.get("type")
        if event_type == "action_resolved":
            return {"action": event["payload"]["action_type"], "success": event["payload"]["success"]}
        elif event_type == "npc_moved":
            return {"npc": event["payload"]["npc_id"], "location": event["payload"]["location"]}
        # ... etc
        return event
```

**Effort:** 1 day
**Integration points:**
- ChatListener: expose replay via `/gm session replay` command
- Admin panel: new "Session Audit" tab showing transcript
- Tests: `test_session_replay_queries.py` with 5–6 tests

**Test examples:**
```python
async def test_replay_transcript_humanizes_events():
    """Events are human-readable: 'NPC Mara moved to tavern', not JSON."""

async def test_replay_state_at_time_returns_world_before_timestamp():
    """Query world state at any point in session history."""

async def test_replay_find_events_by_type_returns_matching():
    """Find all ACTION_RESOLVED or NPC_MOVED events."""
```

### 2b: GM Command Integration

**File:** `foundry/chat_listener.py` (add to `/gm` commands)

```python
# In _handle_gm_command, add:
elif command == "session replay":
    transcripts = await SessionReplay(self._event_store).get_session_transcript(session_id, limit=20)
    await self.foundry.chat_message(
        "📜 Last 20 events:\n" + "\n".join(f"- {t}" for t in transcripts),
        speaker="GM"
    )

elif command.startswith("session state at "):
    time_ago_str = command[len("session state at "):].strip()  # "5 minutes ago"
    timestamp = parse_relative_time(time_ago_str)  # helper
    state = await SessionReplay(self._event_store).get_state_at_time(session_id, timestamp)
    await self.foundry.chat_message(f"🌍 World state {time_ago_str}: {state}", speaker="GM")
```

**Effort:** 0.5 days
**Test:** 2–3 tests for command parsing and output format

**Summary:** By end of 2a+2b, GMs can:
- `/gm session replay` — see last 20 events
- `/gm session state at 10 minutes ago` — rewind world state
- Use this for debugging "why did that NPC act twice?"

---

## Task 3: NPC History Tagging — NO ACTION ✅

**Finding:** Investigation shows NPC actions are **not** currently appended to `_conversation_history` (the shared LLM context). They're logged to events but isolated from the player LLM's context.

**Conclusion:** No tagging needed. The problem doesn't manifest in current code.

**Design property to document:** Add to `npc/agent.py`:

```python
class NPCAgent:
    """NPC autonomous turns. 
    
    NPC actions are logged to the event store but NOT added to the player LLM's
    conversation history, keeping NPC turns isolated from player context inflation.
    If future work adds NPC turn recording to conversation history, add metadata
    tagging ({actor: 'npc'}) to enable context builders to filter them out.
    """
```

**Effort:** 0 (documentation only)
**Impact:** Prevents future regressions by clarifying the design boundary

---

## Task 4: Scene Automation — VERIFY & WIRE ✅

**Status check:** All features exist in code.
- ✅ Fog of war (vision/fog_of_war.py, tests)
- ✅ Hazard effects (environmental_save action, tests)
- ✅ Ambient sounds (place_sounds action)
- ✅ GM macros (immersion/macros.py, registration system)

**Outstanding:** Verify they're accessible to the LLM and wired into action dispatch.

### 4a: Audit: Are scene automation actions in system prompt?

Check `llm/system_prompts.py` for:
- `update_vision` — ✅ found
- `environmental_save` — ✅ found
- `place_sounds` — ✅ found
- `execute_macro` or macro actions — ⏳ **CHECK**

**Task:** If GM macros aren't in system prompt, add them:

```python
# In system_prompts.py ACTIONS section:
| `execute_macro` | `macro_id`, `overrides` (dict, optional) | Execute a registered GM macro (automation, music cues, ambient effects setup). |
```

### 4b: Test: Are scene automation actions dispatch-able?

Check `actions/executor.py` (or dispatcher) for handlers:
- `update_vision` handler — ✅
- `environmental_save` handler — ✅
- `place_sounds` handler — ✅
- `execute_macro` handler — ⏳ **CHECK**

**Task:** If macro execution isn't wired, add it:

```python
async def execute_macro(action: dict) -> dict:
    """Execute a registered GM macro."""
    macro_id = action.get("macro_id")
    overrides = action.get("overrides", {})
    # Call macro manager to execute
    return {"type": "execute_macro", "macro_id": macro_id, "success": True}
```

### 4c: Integration test

Add test in `tests/test_scene_automation_integration.py`:

```python
async def test_lm_can_call_scene_automation_actions():
    """LLM can propose update_vision, place_sounds, environmental_save, execute_macro."""
    # (mock LLM, dispatcher, verify all 4 action types dispatch successfully)
```

**Effort:** 1 day (audit + add GM macro support if missing + test)
**Result:** Scene automation is user-accessible; no features left untested

---

## Summary of All Tasks

| Task | Effort | Impact | Priority |
|------|--------|--------|----------|
| **1. NPC Retry Feedback** | 0 | Clarify design | P0 (doc only) |
| **2a. Session Replay API** | 1d | Audit/debug capability | P1 |
| **2b. GM Command Integration** | 0.5d | User-facing UX | P1 |
| **3. History Tagging** | 0 | Prevent regression | P0 (doc only) |
| **4a. Macro prompt audit** | 0.5d | Feature discovery | P1 |
| **4b. Macro dispatch wiring** | 0.5d | Enable macros | P1 |
| **4c. Integration test** | 0.5d | Verification | P1 |
| **TOTAL** | **3.5 days** | Complete P0 gaps + audit trail + scene automation | |

---

## Execution Plan: Week 1 After PR #106 Merge

**Monday–Tuesday (2 days):**
- Audit scene automation actions (prompt + dispatch)
- Add macro support if missing
- Write integration test
- Merge macro fixes

**Wednesday–Thursday (1.5 days):**
- Implement SessionReplay API
- Add `/gm session replay` command
- Write tests

**Friday (0.5 day):**
- Documentation updates (NPC autonomy, history tagging)
- Code review + buffer

**By end of week:** PR #106 merged + P0 fully complete + audit trail working + scene automation verified.

---

## After This Week: Unblock P1 (Living Settlement)

Once P0 is complete, proceed with **NPC identity mapping** (2 days) to unblock Living Settlement generation (5 days).

Dependency:
```
P0 Complete (End of Week 1)
  ↓
NPC id Mapping (2 days)
  ↓
Living Settlement (5 days)
  ↓
P1 Complete (Week 3)
```

---

## Handoff Ready?

Once confirmed, I can start **immediately** with:
1. Verify PR #106 test suite passes
2. Merge PR #106
3. Start Task 4a (scene automation audit)

Ready?
