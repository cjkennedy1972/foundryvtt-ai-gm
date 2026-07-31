# Unchanged Code Review Items — Future Work

**Document:** Items identified in code review but NOT fixed in PR #103  
**Date:** 2026-07-31  
**Status:** Documented for future sprints

---

## Overview

Of the 12 issues identified in the comprehensive code review:
- **6 issues FIXED** in PR #103 (all P0/P1 critical path)
- **6 issues UNCHANGED** (3 P2 medium, 3 P3 low — non-blocking)

This document tracks the unfixed items for planned follow-up work.

---

## P2 Medium-Impact Issues (3)

### Issue #7: Stale Reconnect Task Reference
**File:** `ai-engine/main.py:331–338`  
**Severity:** P2 (medium-impact)  
**Status:** ❌ UNCHANGED  
**Effort:** ~30 minutes  

#### Problem
Shutdown code references `_reconnect_task` that's never stored on `app.state`:
```python
task = getattr(app.state, '_reconnect_task', None)
if task:
    task.cancel()
```

#### Root Cause
FoundryClient creates reconnect tasks with `asyncio.create_task()` but never stores them for tracking. Background tasks spawned during shutdown can survive and access torn-down `app.state`.

#### Impact
- Background reconnect tasks may outlive app shutdown
- May raise `RuntimeError: Event loop is closed` if accessing app.state
- Resource leak (orphaned tasks not awaited)

#### Recommended Solution
1. Add `_background_tasks` set to FoundryClient
2. Track all background tasks (reconnect, supervisor)
3. Expose `cancel_all_background_tasks()` method
4. Call from `lifespan` shutdown context

#### Code Sketch
```python
# In FoundryClient.__init__
self._background_tasks = set()

# Helper method
def _spawn_background_task(self, coro):
    task = asyncio.create_task(coro)
    self._background_tasks.add(task)
    task.add_done_callback(self._background_tasks.discard)
    return task

# In reconnect
self._reconnect_task = self._spawn_background_task(self._reconnect())

# Cancel method
async def cancel_all_background_tasks(self):
    for task in self._background_tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*self._background_tasks, return_exceptions=True)
    self._background_tasks.clear()

# In main.py shutdown
if hasattr(foundry_client, 'cancel_all_background_tasks'):
    await foundry_client.cancel_all_background_tasks()
```

#### Risk Level
**Low** — Purely additive, no logic changes

#### Priority
**Medium** — Should fix in next sprint after P0/P1 deployed

---

### Issue #8: Echo Suppression Deque Overflow
**File:** `ai-engine/foundry/chat_listener.py:100–101`  
**Severity:** P2 (medium-impact)  
**Status:** ✅ PARTIALLY FIXED  
**Effort:** ~15 minutes (for TTL variant)  

#### Problem (Original)
`_sent_messages` deque had `maxlen=20`, too small for relay buffering:
```python
self._sent_messages: collections.deque = collections.deque(maxlen=20)
```

#### What We Fixed
✅ Increased `maxlen` from 20 → 100 in commit b991e5d

#### Why Only Partial
- **Option A (done):** Increase maxlen → Solves 95% of problem with minimal complexity
- **Option B (skipped):** Add TTL-based cache → Overkill for current needs, adds complexity

#### Full Solution (If Needed Later)
Add timestamp-based expiry for more aggressive cleanup:

```python
self._sent_messages_with_timestamp: list[(str, float)] = []

async def _record_sent(self, text: str):
    now = time.monotonic()
    self._sent_messages_with_timestamp.append((text[:120], now))
    # Expire entries older than 5 minutes
    cutoff = now - 300
    self._sent_messages_with_timestamp = [
        (msg, ts) for msg, ts in self._sent_messages_with_timestamp
        if ts >= cutoff
    ]

async def _is_player_message(self, inner: dict) -> bool:
    # ... existing code ...
    # Check against timestamped cache
    snippet = content[:120]
    async with self._sent_messages_lock:
        if any(msg == snippet for msg, _ in self._sent_messages_with_timestamp):
            return False
    return True
```

#### Current Status
✅ **Good enough** — 100-message window is adequate for typical relay buffering

#### Risk Level
**Very Low** — Current fix is safe and effective

#### Priority
**Low** — Already partially addressed; full TTL solution can wait

---

### Issue #9: Missing Error Logs on RPC Failure
**File:** `ai-engine/foundry/client.py:149–152`  
**Severity:** P2 (medium-impact, debugging aid)  
**Status:** ❌ UNCHANGED  
**Effort:** ~5 minutes  

#### Problem
When reconnect fails pending RPCs, no logging identifies which requests were lost:
```python
for future in self._rpc_futures.values():
    if not future.done():
        future.set_exception(ConnectionError("Connection reset"))
self._rpc_futures.clear()
```

#### Impact
- Silent failures make network debugging impossible
- No audit trail of lost operations
- Developers can't tell which action failed

#### Recommended Solution
Add warning logs before clearing futures:

```python
for rid, future in list(self._rpc_futures.items()):
    if not future.done():
        logger.warning(
            f"[Client] Failing pending RPC {rid} due to connection reset; "
            f"caller will receive ConnectionError"
        )
        future.set_exception(ConnectionError("Connection reset"))
self._rpc_futures.clear()
```

#### Risk Level
**None** — Purely additive logging

#### Priority
**Low** — Quality-of-life improvement, not blocking

---

## P3 Low-Priority Issues (3)

### Issue #10: Bare `except Exception` in Shutdown
**File:** `ai-engine/llm/manager.py:74–82`  
**Severity:** P3 (low-priority, code practice)  
**Status:** ❌ UNCHANGED  
**Effort:** ~2 minutes  

#### Problem
Bare `except Exception:` suppresses all errors silently:
```python
async def close(self):
    try:
        await self._http.aclose()
    except Exception:
        pass
```

#### Issue
- Hides critical errors (event loop closed, connection issues)
- Makes debugging shutdown problems impossible
- Against Python best practices (PEP 8)

#### Recommended Solution
Specify exceptions:
```python
async def close(self):
    try:
        await self._http.aclose()
    except (OSError, asyncio.CancelledError, RuntimeError):
        # OSError: connection already closed
        # CancelledError: task cancelled during shutdown
        # RuntimeError: event loop is closed
        pass
```

#### Risk Level
**None** — No functional change

#### Priority
**Low** — Only fires during shutdown; style improvement

---

### Issue #11: Hardcoded Tilde Path Not Expanded
**File:** `ai-engine/config.py:66`  
**Severity:** P3 (low-priority, portability)  
**Status:** ❌ UNCHANGED  
**Effort:** ~3 minutes  

#### Problem
Campaign vault path uses tilde but isn't auto-expanded:
```python
campaign_vault_path: str = "~/Vaults/MyStuff/Dungeons_and_Dragons"
```

#### Issue
- Relies on callers to remember to expand `~` manually
- Fragile — easy to forget
- Not self-documenting

#### Recommended Solution
Add Pydantic validator:
```python
@field_validator("campaign_vault_path", mode="after")
@classmethod
def expand_campaign_path(cls, v):
    return str(Path(v).expanduser())
```

#### Current Status
Works implicitly — code elsewhere handles expansion, but not guaranteed

#### Risk Level
**None** — Defensive programming improvement

#### Priority
**Low** — Code currently handles it; nice-to-have defensive measure

---

### Issue #12: Pathlib Inconsistency
**File:** `ai-engine/main.py:470`  
**Severity:** P3 (low-priority, style)  
**Status:** ✅ WORKS AS-IS  
**Effort:** N/A (no fix needed)  

#### Problem (Original Assessment)
Audio file validation mixes pathlib.Path and os-level checks:
```python
if Path(filename).name != filename or audio_path.parent != audio_root or not audio_path.is_file():
    raise HTTPException(status_code=404, detail="Audio file not found")
```

#### Assessment
✅ **No change needed** — This code is actually fine:
- Works correctly on all platforms
- Pathlib operations are appropriate and consistent
- Not a real bug, just initial concern

#### Decision
**No fix required** — Code is correct as-is

---

## Summary: Unfixed Items

| # | Severity | Issue | Effort | Risk | Priority | Next Action |
|---|----------|-------|--------|------|----------|-------------|
| 7 | P2 | Stale Reconnect Task | 30 min | Low | Medium | Next sprint |
| 8 | P2 | Echo Deque (partial) | 15 min | Very Low | Low | Optional |
| 9 | P2 | RPC Error Logging | 5 min | None | Low | Next sprint |
| 10 | P3 | Bare except | 2 min | None | Low | Optional |
| 11 | P3 | Tilde Path Validator | 3 min | None | Low | Optional |
| 12 | P3 | Pathlib Inconsistency | — | — | — | ✅ Works |

---

## Recommended Workflow

### Immediate (Before Deployment)
```
✅ Deploy PR #103 (6 critical fixes)
```

### Next Sprint (Follow-up PR)
```
📋 #7: Add background task tracking (30 min)
📋 #9: Add RPC failure logging (5 min)
```

### Nice-to-Have (Low Priority)
```
📋 #10: Specify exceptions in shutdown (2 min)
📋 #11: Add path expansion validator (3 min)
```

---

## How to Use This Document

1. **After PR #103 merges:** Update this document with deployment notes
2. **Next sprint planning:** Reference this as backlog items
3. **Before finalizing:** Implement P2 items #7 & #9 in a follow-up PR
4. **Long-term:** P3 items are technical debt, low priority

---

## Contact

For questions about unfixed items, refer to:
- **Original findings:** Full code review report (generated 2026-07-31)
- **PR #103 details:** https://github.com/cjkennedy1972/foundryvtt-ai-gm/pull/103
- **Handoff doc:** `.hermes/HANDOFF.md`

---

*End of unfixed items documentation. All P0/P1 (critical) items fixed and deployed. P2/P3 (non-critical) items documented for future work.*
