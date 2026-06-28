# FoundryVTT AI GM — Code Review

**Date:** 2026-06-28  
**Scope:** Full codebase audit — backend (`ai-engine/`), Foundry integration, state management, persistence, combat, scene awareness, campaign loading, LLM orchestration.  
**Files read:** `main.py`, `config.py`, `foundry/client.py`, `foundry/chat_listener.py`, `actions/dispatcher.py`, `actions/schemas.py`, `actions/executors.py`, `llm/manager.py`, `state/models.py`, `state/tracker.py`, `persistence/db.py`, `context/loader.py`, `combat/loop.py`, `combat/mechanics.py`, `scene/awareness.py`, `procedural/generator.py`, `relay_proc/manager.py`

---

## 1. Architecture & Design

### 1.1 Overall Assessment
This is a **well-architected** system. The separation of concerns across modules is excellent:

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app, lifecycle, WS broadcast, API endpoints |
| `config.py` | All configuration via `pydantic-settings` with `.env` |
| `foundry/client.py` | WebSocket relay protocol, RPC send/receive, channel subscriptions |
| `actions/` | Dispatcher + 30+ action executors (roll, speak, move_token, etc.) |
| `state/` | Game state tracking, combat state management |
| `persistence/db.py` | SQLite with WAL mode, write locks, retention policies |
| `context/loader.py` | Obsidian vault → campaign context → LLM system prompt |
| `combat/loop.py` | Turn-based NPC/AI combat loop |
| `scene/awareness.py` | Scene caching (LRU), token/familiarity tracking |
| `procedural/generator.py` | NPC, encounter, treasure, and quest generation |
| `relay_proc/manager.py` | Embedded relay server (managed subprocess) |

**Strengths:**
- Clean async/await pattern throughout — no blocking calls
- Pydantic models for all API contracts (request/response)
- Dependency injection via FastAPI `Depends()` on endpoints
- Self-healing: reconnection loops, headless session relaunch, stale session cleanup
- Comprehensive error handling — most operations catch exceptions and log them

### 1.2 Issues

**P1: Module-level global mutable state in `main.py`**
```python
global db, foundry_client, llm_manager, action_dispatcher
global state_tracker, chat_listener, campaign_loader
global context_manager, combat_loop, scene_awareness
global relay_manager
```

This creates a **dual-state problem**: FastAPI endpoints use `AppState` instances from dependency injection (which have `Optional[T]` attributes), but the startup code assigns to module-level `global` variables that **nobody else can access** (the endpoints never read them). The `AppState` is used for `Depends(get_app_state)`, which gives each request a separate `AppState` instance. However, the actual workers (chat listener, combat loop) reference the globals — these are completely different objects.

**Fix:** Remove the `global` declarations and wire all components through the `AppState` instance. Or, remove the `AppState` class and access globals directly from endpoints.

**P2: Race condition on `combat_loop` start**
Multiple code paths can trigger `start_combat_loop`:
- `/api/combat/start` endpoint
- Foundry event `combat-event`
- The `setup_scene` executor can auto-start

The `if self._running` guard exists, but there's no lock preventing concurrent scheduling. Two concurrent calls both pass the guard, both initialize state, and both enter `_process_turns` — resulting in corrupted turn order.

**Fix:** Add an `asyncio.Lock` around the combat start sequence, or use a one-shot state machine.

**P3: Hardcoded admin credentials**
```python
relay_admin_email: str = "aigm@local.host"
relay_admin_password: str = ""  # auto-generated and persisted if empty
```

The `aigm@local.host` email is hardcoded, and if no password is set, it remains empty (the relay auto-generates one at runtime, which is fine for development but not auditable). The admin key handling (storing in `.env` or process env) needs documentation.

---

## 2. Security Review

### 2.1 Critical: `execute_js` Action

```python
async def execute_execute_js(code: str, ...) -> dict:
    if not getattr(_settings, "allow_execute_js", False):
        # ...blocked...
```

This is **correctly gated** behind `allow_execute_js=false` by default. However:
- The settings can be changed at runtime via `/api/settings` without validation
- The settings are returned in `/api/settings` GET (keys masked, but other fields could leak configuration)
- Even when disabled, the route is **registered on the OpenAPI schema**, which leaks the existence of arbitrary JS execution to any API explorer

**Recommendation:** Conditionally register the route. Or add a rate limit and audit log.

### 2.2 Escaping in Dynamic JavaScript

Multiple executors construct JavaScript strings using `json.dumps()` for user-controlled values:

```python
# executors.py — move_token
want = json.dumps(str(token_id))
js = f"const want={want};..."

# client.py — set_active_scene
want = json.dumps(scene_name)
js = f"const want={want};const norm=s=>..."

# chat_listener.py — several LLM-generated JS payloads
```

**This is reasonably safe** because `json.dumps()` properly escapes strings. However, there are a few cases where the JS is constructed with `{json.dumps(something)}` where `something` is an **LLM-generated prompt** — if the LLM output contains template strings like `;someCode();` within the string, `json.dumps()` would escape the quotes, so this is actually safe.

### 2.3 Path Traversal Prevention

`load_custom_campaign` uses `validate_contained_path()` to verify files stay within the vault. **Good.**

However, `apply_retention_policy` in `persistence/db.py` uses raw SQL with no parameters for the `MIN_RECENT_MESSAGES_PER_SESSION` constant — this is an integer, so SQL injection isn't possible, but the query has no `LIMIT` on how many rows can be deleted per execution. On a long-running session with millions of messages, a single retention run could hold the write lock for minutes.

**Fix:** Add a `LIMIT` clause or use pagination within the retention job.

### 2.4 Secret Exposure in `/api/settings` GET

The `GMSettings` model has `llm_api_key` and `relay_api_key` as fields, but the endpoint explicitly returns `""` for them:

```python
return GMSettings(
    llm_api_key="",  # Never return actual key
    relay_api_key="",  # Never return actual key
    ...
)
```

This is correct. However, the POST `/api/settings` endpoint updates these values without re-validating or hashing them:

```python
if settings_data.llm_api_key:
    settings.llm_api_key = settings_data.llm_api_key
    state.llm_manager.model = settings_data.model
```

**Risk:** The key is stored in the `Settings` object (which may be pickled, logged, or dumped in error traces). Consider hashing or using a secrets store for sensitive keys.

---

## 3. Error Handling & Robustness

### 3.1 Exception Swallowing

Multiple places catch exceptions and silently continue:

```python
# config.py — settings have no defaults for required fields
# model: str = ""  # <-- empty string is a valid but useless value
```

When `model` is empty, the LLM manager will fail on first call, but there's no startup-time validation.

**Fix:** Use `BaseSettings` validator to require `model` when LLM features are used.

### 3.2 RPC Timeout Handling

```python
# client.py — _send()
try:
    result = await asyncio.wait_for(future, timeout=timeout)
except asyncio.CancelledError:
    self._rpc_futures.pop(request_id, None)
    raise
except asyncio.TimeoutError:
    self._rpc_futures.pop(request_id, None)
    raise ConnectionError(f"RPC request {request_id} timed out")
```

This correctly re-raises `CancelledError` (preventing shutdown storms) but re-raises `TimeoutError` as a `ConnectionError`. The `_send_with_retry` method catches `ConnectionError` only when `"timed out" not in str(e)` — this is a **string-match heuristic** for timeout detection. Fragile but works.

### 3.3 WebSocket Close Handling

```python
# client.py — _reader_loop
except websockets.exceptions.ConnectionClosed:
    logger.warning("Relay connection closed")
    self._connected = False
```

This catches `ConnectionClosed` but **does not attempt reconnect** — it relies on the background `_reconnect_loop()` in `main.py` to notice `self._connected == False` and retry. This is correct but could be tighter: the reader should set a flag that the reconnect loop checks, or the reader itself should attempt a reconnect (as the current code does via `ensure_connected()`).

### 3.4 Missing `finally` Blocks

Several executors lack `finally` blocks for resource cleanup:

```python
# executors.py — generate_map
gen_result = await app_state.map_generator.generate_map(...)  # no close()
# ...
# No cleanup if an exception occurs between generation and scene creation
```

Map generators that hold file handles or HTTP connections should be closed in a `finally` block.

---

## 4. Performance

### 4.1 Database — SQLite

The database module is **well-implemented**:
- WAL mode enabled (`PRAGMA journal_mode=WAL`)
- Write lock serialization (`asyncio.Lock`)
- Indexed foreign key queries
- Automatic retention policy on startup

**Issue:** The retention policy query uses a subquery with `ROW_NUMBER()` which, while partitioned by session, will scan all old rows before the window function filter. On a database with millions of conversation entries, this could be slow.

**Fix:** Consider an incremental cleanup approach (process batches of 1000 rows per execution).

### 4.2 Context Window Management

```python
# config.py
max_context_tokens: int = 50000
```

With 50k tokens for context, the combined campaign files, NPC data, and combat state could exceed this. The `context/loader.py` uses a simple keyword search for SRD:

```python
# _chunk_text uses ~6 char/token ratio — rough but functional
```

**Risk:** If a campaign has many files totaling 200k+ characters of markdown, the context window will be overwhelmed, causing expensive LLM calls that get truncated.

**Fix:** Implement a priority-based context compression: recent messages > NPC data > SRD > worldbuilding.

### 4.3 Scene Cache

The LRU scene cache (`MAX_CACHED_SCENES = 10`) is reasonable but could be improved:
- No TTL/expiration — scenes could become stale if Foundry modifies them externally
- No cache warming — frequently visited scenes are evicted first if the player briefly visits another scene

**Fix:** Add a TTL (e.g., 5 minutes) or a priority weight (access frequency).

---

## 5. Bugs & Anti-Patterns

### 5.1 Bug: Disposition Default in Combat Loop

```python
# combat/loop.py
if disp is not None and disp >= 0:  # Explicit friendly/neutral → PC/ally
    self._pc_tokens.append(token)
else:  # Hostile or unknown → AI-controlled
    self._npc_tokens.append(token)
```

**Problem:** If `disposition` is `None` (unset in Foundry), the token defaults to **NPC status**, meaning it will be AI-controlled. This is stated in the comment but is dangerous — if a GM forgets to set disposition on a player token, that character becomes AI-controlled.

**Severity:** Medium-High. Could cause unexpected behavior in a live game.

**Fix:** Log a warning when disposition is `None` and query Foundry for the actual owner.

### 5.2 Bug: Race Condition in `_reader_loop`

The WebSocket reader loop sets `self._connected = False` on `ConnectionClosed` but **doesn't set the RPC futures to failed**. The `_reconnect_loop` only checks `self._connected`, so any pending RPCs will hang indefinitely until the reconnect succeeds (or the 10-second sleep cycle retries).

Actually, looking more closely, the `_reader_loop` **does** fail pending futures:
```python
for future in self._rpc_futures.values():
    if not future.done():
        future.set_exception(ConnectionError("Reader loop exited"))
```

This is correct, but the comment says "fail all pending RPC futures so callers don't hang" — this is only reached if the reader **exits** (not just closes). The `ConnectionClosed` handler only sets `self._connected = False`. This means RPC calls in flight during a disconnect will **not fail** until the reader exits entirely — which might be never if the connection is just flapping.

**Fix:** In the `ConnectionClosed` handler, also fail pending RPC futures.

### 5.3 Anti-Pattern: Inline JS Strings

The codebase is full of inline JavaScript strings embedded in Python:

```python
# client.py
js = f"await canvas.scene.update({{background:{{src:{json.dumps(background_src)}}}}});return 'ok'"

# executors.py
js = f"const m=game.modules.get('aigm-tts');if(m&&m.api){{m.api.speakAll({payload_js});return{{ok:true}};}}return{{ok:false,error:'aigm-tts module not active'}};"
```

While functional, this makes JS maintenance difficult and obscures the logic. Consider a separate `.js` file with template parameters.

### 5.4 Anti-Pattern: Magic Numbers

```python
MAX_CACHED_SCENES = 10
MIN_RECENT_MESSAGES_PER_SESSION = 100
_DEFAULT_PC_TIMEOUT = 180
RELAUNCH_COOLDOWN = 30.0
CONTEXT_MAX_CHARS = 50_000
```

Most are in `config.py`, but several are hardcoded constants in module bodies. Move all tunables to config.

---

## 6. Missing Features / Gaps

1. **No unit tests** — 0 test files in the entire codebase. Critical for an AI-dependent system where LLM responses can change.
2. **No API documentation** (except OpenAPI auto-generation) — no `/docs` or `/redoc` configured in FastAPI.
3. **No health check** that checks LLM connectivity or ComfyUI status.
4. **No migration system** for the SQLite schema — adding new columns requires manual SQL.
5. **No rate limiting** on API endpoints (except `llm_min_call_interval` for the LLM itself).
6. **No structured logging** — all logs are plain text, making log aggregation difficult.
7. **No observability** — no metrics, tracing, or alerting hooks.
8. **No graceful shutdown** — the `lifespan` shutdown handler doesn't wait for in-flight RPCs to complete.

---

## 7. Recommendations (Prioritized)

### High Priority

1. **Remove `global` declarations from `main.py`** (P1 above) — this is the single biggest architectural issue. All component references should go through the `AppState` instance.

2. **Add unit tests** — at minimum: dispatcher validation, RPC send/receive, persistence layer, combat loop state machine.

3. **Add input validation** for `/api/settings` POST — reject empty `model` strings, validate URL formats, ensure `allow_execute_js` changes are logged.

4. **Fix disposition default** in combat — log a warning when a token has no disposition, default to hostile rather than NPC.

### Medium Priority

5. **Replace string-match timeout detection** with an exception subclass (e.g., `RelayTimeoutError`).

6. **Add a TTL to scene cache** (5 minutes expiry, purge stale entries on access).

7. **Create a dedicated `.well-known/health` endpoint** that checks LLM, Foundry, and ComfyUI connectivity, not just component initialization.

8. **Add structured logging** (JSON format) for easier log aggregation in production.

### Low Priority

9. **Move inline JS to template files** for maintainability.
10. **Add rate limiting** middleware to API endpoints (e.g., per-IP, per-endpoint).
11. **Add migration system** for the SQLite schema (e.g., `alembic`).
12. **Add graceful shutdown** that waits for in-flight RPCs.
13. **Document the protocol** (relay WebSocket format) so others can integrate.

---

## 8. Positive Highlights

Despite the issues above, this is a **high-quality codebase** for its domain:

- **Protocol awareness:** The WebSocket relay protocol is well-documented in the class docstring
- **Reconnection handling:** Multi-layered (reader loop, reconnect task, headless session relaunch)
- **Action safety:** Pydantic schema validation before every action execution
- **Combat logic:** Real initiative reading from Foundry, proper PC/NPC distinction, fallback behavior
- **Persistence:** WAL mode, write locks, retention policy — production-grade SQLite usage
- **Scene awareness:** LRU caching with fallback to live reload
- **TTS integration:** Dual-mode (server + browser) with volume control and voice assignment
- **API design:** Clean REST endpoints with error response models

The codebase shows significant thought about edge cases (stale tokens, dead connections, replayed events) and handles them gracefully.

---

## 9. Summary Scorecard

| Category | Score | Notes |
|---|---|---|
| Architecture | 8/10 | Modular, clean separation, but dual-state globals need fixing |
| Security | 6/10 | Gate on `execute_js` is correct, but inline JS is fragile, no rate limiting |
| Error Handling | 7/10 | Comprehensive catch blocks, but some exception paths incomplete |
| Performance | 7/10 | WAL mode, write locks, caching — but retention queries could be slow |
| Testing | 1/10 | No tests at all |
| Documentation | 4/10 | Good inline comments, but no API docs or deployment guide |
| Maintainability | 6/10 | Inline JS and magic numbers hurt readability |

**Overall: 6/10** — A strong foundation with clear production potential once the structural issues (globals, testing, documentation) are addressed.