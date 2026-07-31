# Code Review Fixes — Handoff Document

**Date:** 2026-07-31  
**Reviewer:** Code Reviewer (staff-level)  
**Developer:** (assigned)  
**Status:** Ready for implementation  

---

## Summary

The `foundryvtt-ai-gm` codebase contains **6 critical concurrency bugs** that pose data loss and reliability risks. This document hands off the fixes to a developer for implementation and testing.

**Total Issues:** 12 (2 P0, 4 P1, 3 P2, 3 P3)  
**Critical Path:** 6 issues (2 P0 + 4 P1) must be fixed before production.

---

## Critical Issues Requiring Fixes

### P0 Blockers (2)

1. **API Rate Limiter Dict Race Condition** (`main.py:381–392`)
   - Concurrent requests modify `_api_rate` without synchronization
   - Risk: `RuntimeError: dict changed size during iteration` or unbounded memory growth
   - Fix: Add `asyncio.Lock` guard on all `_api_rate` access

2. **WebSocket State Access Without Guard** (`main.py:496`)
   - Direct access to `websocket.app.state` without null check
   - Risk: `AttributeError` if app context is not initialized
   - Fix: Add `hasattr(websocket.app, 'state')` check

### P1 High-Impact (4)

3. **Event Queue Drain Race** (`foundry/client.py:157–159`)
   - Reconnect drains queue while `_event_worker_task` may still be running
   - Risk: Events lost or processed twice
   - Fix: Ensure worker exits before draining queue

4. **Turn Count Increment Without Lock** (`llm/manager.py:198–209`)
   - `_turn_count` incremented without `_history_lock`
   - Risk: Reinforcement context injects at wrong intervals
   - Fix: Acquire `_history_lock` before incrementing and modulo check

5. **Chat Listener `_running` Unprotected** (`main.py:519, 529, 537–538`)
   - WebSocket handler writes `_running` without synchronization
   - Risk: Pause/resume race with active narration
   - Fix: Add `pause()` and `resume()` methods with locks in ChatListener

6. **Request ID Generation Non-Atomic** (`foundry/client.py:91–93`)
   - `_message_id` increment not protected; creates duplicate IDs under load
   - Risk: Response routing breaks; callers hang waiting for lost RPC
   - Fix: Add `asyncio.Lock` to protect ID generation

### P2 Medium (3)

7. **Stale Reconnect Task Reference** (`main.py:331–338`)
   - Shutdown tries to cancel `_reconnect_task` that's never stored on app.state
   - Risk: Orphaned tasks crash after shutdown
   - Fix: Track background tasks explicitly; expose `cancel_all_background_tasks()`

8. **Echo Suppression Deque Overflow** (`foundry/chat_listener.py:100–101`)
   - `_sent_messages` maxlen=20 too small; relay re-delivery exceeds buffer
   - Risk: Old messages trigger re-narration loops
   - Fix: Increase maxlen to 100 or add timestamp-based expiry

9. **Missing Error Logs on RPC Failure** (`foundry/client.py:149–152`)
   - RPC futures failed silently on reconnect
   - Risk: Silent failures hard to debug
   - Fix: Add logger.warning() when failing futures

### P3 Low (3)

10. **Bare `except Exception` in Shutdown** (`llm/manager.py:74–82`)
    - Fix: Use specific exceptions: `except (OSError, asyncio.CancelledError, RuntimeError):`

11. **Hardcoded Tilde Path Not Expanded** (`config.py:66`)
    - Fix: Add Pydantic validator to call `Path.expanduser()`

12. **Pathlib Inconsistency** (`main.py:470`)
    - Fix: Minor; already works. Document or keep as-is.

---

## Implementation Checklist

### Phase 1: P0 Fixes (Critical)

- [ ] **Issue #1:** Add `_api_rate_lock = asyncio.Lock()` to module-level. Guard all `_api_rate` access in `protect_api_resources` middleware.
- [ ] **Issue #2:** Add `hasattr(websocket.app, 'state')` check in `/api/ws` endpoint before accessing `state`.

### Phase 2: P1 Fixes (High-Impact)

- [ ] **Issue #3:** Cancel and await `_event_worker_task` before draining `_event_queue` in `connect()` reconnect path.
- [ ] **Issue #4:** Add `async with self._history_lock:` guard around `_turn_count` increment and modulo check in `_build_prompt_messages()`.
- [ ] **Issue #5:** Add `async def pause()` and `async def resume()` methods to ChatListener with internal lock. Update main.py to call these instead of direct attribute write.
- [ ] **Issue #6:** Add `_id_lock = asyncio.Lock()` to FoundryClient. Create `_next_request_id_async()` method. Update all `_send()` calls to use async ID generation.

### Phase 3: P2 Fixes (Medium)

- [ ] **Issue #7:** Track background tasks in FoundryClient or app.state. Add `cancel_all_background_tasks()` method. Call from lifespan shutdown.
- [ ] **Issue #8:** Increase `_sent_messages` maxlen from 20 to 100, or implement TTL-based cache with 5-minute expiry.
- [ ] **Issue #9:** Add `logger.warning(f"Failing pending RPC {rid}...")` before `future.set_exception()` on reconnect.

### Phase 4: P3 Fixes (Low)

- [ ] **Issue #10:** Specify exceptions in `llm/manager.py` close().
- [ ] **Issue #11:** Add Pydantic validator to `config.py` for `campaign_vault_path` expansion.
- [ ] **Issue #12:** No action needed (working correctly).

### Phase 5: Testing & Verification

- [ ] **Syntax check:** Run `ast.parse()` on all modified Python files. Verify no import errors.
- [ ] **Load test:** Multiple concurrent HTTP requests to `/api/*` endpoints while monitoring for dict errors.
- [ ] **Concurrency test:** Trigger rapid relay reconnects; verify no events are lost and no duplicates appear.
- [ ] **Pause/Resume test:** Call pause while narration is in flight; verify AI stops cleanly and resumes correctly.
- [ ] **Integration test:** Run existing test suite (`pytest`); verify no regressions.
- [ ] **Code review:** Run revised code through code-reviewer to ensure fixes are complete.

---

## Files to Modify

### Core Files

1. **`ai-engine/main.py`**
   - Add `_api_rate_lock` module-level variable
   - Guard `_api_rate` access in `protect_api_resources()`
   - Add `hasattr` check in `/api/ws` endpoint
   - Update pause/resume message handlers to call `chat_listener.pause()` / `resume()`

2. **`ai-engine/foundry/client.py`**
   - Add `_id_lock = asyncio.Lock()` to `__init__`
   - Create `_next_request_id_async()` method
   - Update `_send()` to use async ID generation
   - Add logging to RPC failure path
   - Synchronize event queue drain with worker lifecycle
   - Track background tasks for cancellation in shutdown

3. **`ai-engine/foundry/chat_listener.py`**
   - Add `_running_lock = asyncio.Lock()` to `__init__`
   - Add `async def pause()` method
   - Add `async def resume()` method
   - Increase `_sent_messages` maxlen from 20 to 100

4. **`ai-engine/llm/manager.py`**
   - Add `async with self._history_lock:` guard in `_build_prompt_messages()` around `_turn_count` increment and modulo check
   - Specify exceptions in `close()` method

5. **`ai-engine/config.py`**
   - Add Pydantic validator for `campaign_vault_path` to expand `~`

---

## Context for Developer

**Project:** Aethelwyrd AI GM — AI-powered D&D 5e gamemaster for FoundryVTT  
**Stack:** Python 3.11+ FastAPI, Go relay, React admin panel  
**Key Dependencies:** asyncio, httpx, websockets, pydantic  
**LLM Endpoint:** oMLX-compatible (Qwen3.6-35B) at http://localhost:8800/v1  
**Relay:** Go server at http://localhost:13010; WebSocket at ws://localhost:13010/ws/api  
**Config:** `.env` file with `LLM_API_KEY`, `LLM_BASE_URL`, `MODEL`  

**Testing Strategy:**
- Unit tests exist in `ai-engine/tests/`; run with `pytest`
- Focus regression testing on:
  - `test_relay_manager.py` (reconnection logic)
  - `test_gm_command_auth.py` (pause/resume)
  - `test_llm_prompt_builder.py` (context reinforcement)

**Known Pitfalls:**
- FastAPI dependency injection: untyped parameters cause 422 on HTTP. Use explicit `Request` type for HTTP-only deps.
- Qwen3.6 model outputs thinking blocks by default; system prompt must include `thinking=false` query param (already in code).
- Foundry relay re-delivers buffered events on reconnect; echo suppression deque must be large enough.

---

## Acceptance Criteria

- [ ] All P0/P1 fixes applied and code reviewed
- [ ] No new syntax errors; all files pass `ast.parse()`
- [ ] Existing test suite passes (zero regressions)
- [ ] Load test: 100 concurrent /api/ requests without dict iteration errors
- [ ] Concurrency test: 10 rapid reconnects with concurrent chat events; zero lost/duplicate events
- [ ] Pause/resume test: Narration pauses within 2s; resumes correctly
- [ ] Code review: All changes verified against original review findings

---

## Sign-Off

**Reviewer:** Code Reviewer  
**Assigned to:** (developer name)  
**Target completion:** 2026-08-02  
**Priority:** Critical (production blocker)  

---

## Next Steps

1. Developer clones/pulls latest `master` branch
2. Creates feature branch: `fix/concurrency-race-conditions`
3. Implements Phase 1–5 per checklist above
4. Runs test suite and load test locally
5. Opens PR with link to this handoff doc
6. Code reviewer validates fixes; merges to `master`
7. Deploy to production after merge

---

*End of handoff document.*
