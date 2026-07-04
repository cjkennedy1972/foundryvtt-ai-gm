# Safety Safeguards — FoundryVTT AI GM

Comprehensive fail-fast validation throughout the application to catch configuration errors, missing dependencies, and invalid operations before they cause runtime failures or silent data corruption.

## Philosophy

**Fail Fast, Fail Loud**: Better to crash with a clear error message at startup or action time than to silently drop operations or execute with stale/invalid data. All safeguards are **non-recoverable** — they indicate a configuration or deployment issue that must be fixed.

---

## 1. Startup Validation (config.py)

**When**: Application startup, before any operations run.  
**Failures**: Prevent app launch with clear guidance.

### Required Settings

| Setting | Constraint | Error Message |
|---------|-----------|---------------|
| `model` | Non-empty string | "model cannot be empty — set MODEL env var (e.g. claude-3-5-sonnet-20241022)" |

### Validated Settings

| Setting | Constraint | Error | Severity |
|---------|-----------|-------|----------|
| `temperature` | 0.0 ≤ x ≤ 2.0 | "temperature must be between 0.0 and 2.0, got {value}" | FATAL |
| `admin_port` | 1024 ≤ x ≤ 65535 | "admin_port must be between 1024 and 65535, got {value}" | FATAL |
| `relay_url` | Valid URL (http/https/ws/wss) | "relay_url must be a valid URL, got: {value}" | FATAL |
| `relay_ws_url` | Valid WebSocket URL (ws/wss) | "relay_ws_url must be a WebSocket URL (ws:// or wss://), got: {value}" | FATAL |

### Startup Warnings

| Setting | Condition | Warning |
|---------|-----------|---------|
| `allow_execute_js` | true | "WARNING: allow_execute_js=true — arbitrary JavaScript execution is enabled!" |
| `llm_api_key` | empty | "WARNING: llm_api_key is not set — LLM features will fail at runtime" |

---

## 2. Combat System Safeguards (combat/loop.py)

**When**: Combat start (any source: `/combat/start` endpoint, Foundry event, GM command).  
**Failures**: Refuse to start combat, return clear error list to GM.

### Token Disposition Validation

**The Issue**: Tokens with undefined `disposition` silently default to NPC status, causing player characters to be AI-controlled.

**The Fix**: FAIL FAST
- Scan all tokens before combat starts
- Reject any token with `disposition = None`
- Return error listing misconfigured tokens by name and ID
- Force GM to fix Foundry token settings and retry

**Error Format**:
```
Combat cannot start due to misconfigured tokens:
  - Player Name (token-id-123): disposition is undefined (check Foundry token disposition setting)
  - Boss Enemy (token-id-456): disposition is undefined (...)

In Foundry, set disposition (friendly/neutral/hostile) for all combatants.
```

---

## 3. Action Validation Safeguards (actions/dispatcher.py)

**When**: Every action sent from LLM (before execution).  
**Failures**: Reject action, return error to caller with reason.

### Strict Schema Validation

1. **Required Fields**: All required fields in action schema must be present and valid type
2. **No Unknown Fields**: Reject any field not in the schema (catches LLM hallucinations)
3. **Type Safety**: Pydantic coercion disabled — types must match exactly
4. **Damage Clamping**: numeric fields clamped to game-safe ranges (e.g., 0-200 damage)

**Error Examples**:
- `"Unknown fields in action 'move_token': invalid_param, typo_field"`
- `"Validation error for action 'update_hp': value is not a valid integer"`

---

## 4. Action Execution Safeguards (actions/executors.py)

**When**: Handler is about to execute in Foundry.  
**Failures**: Raise `ExecutionError`, caught by dispatcher and returned to caller.

### Available Safeguard Functions

```python
def _require(condition: bool, message: str):
    """FAIL-FAST: Raise ExecutionError if condition is False."""
    if not condition:
        raise ExecutionError(message)

def _require_foundry_connected(foundry: FoundryClient):
    """Ensure Foundry client is connected before executing action."""
    _require(
        foundry and foundry.is_connected,
        "Foundry is not connected — cannot execute action. Check relay connection."
    )
```

### Usage in Executors

```python
async def execute_move_token(token_id: str, x: int, y: int, foundry: FoundryClient):
    _require_foundry_connected(foundry)
    _require(token_id, "token_id cannot be empty")
    _require(0 <= x < 10000, f"x coordinate out of bounds: {x}")
    
    # ... safe to execute now ...
```

---

## 5. Scene Cache TTL (scene/awareness.py)

**When**: Scene is accessed after > 5 minutes in cache.  
**Failures**: Reload scene from Foundry (transparent to caller).

### Stale Detection

- Each cached scene has timestamp `_cached_at`
- Access triggers staleness check: `age > SCENE_CACHE_TTL_SECONDS (300s)`
- Stale scenes automatically reload from Foundry
- LRU eviction still applies (max 10 scenes in memory)

**Benefit**: Prevents stale scene state (tokens moved, walls changed, etc.) from affecting LLM context or combat decisions.

---

## 6. Global State Cleanup (main.py)

**When**: Application startup.  
**Removed**: Unused global variable declarations.

- Removed dead `global` statements that were never accessed
- All components wired through `AppState` dependency injection
- Cleaner code, no ambiguity about where objects come from

---

## Testing the Safeguards

### Combat Disposition

```bash
# This should FAIL with disposition error:
curl -X POST http://localhost:18080/api/combat/start \
  -H "Content-Type: application/json" \
  -d '{"tokens": [{"id": "t1", "name": "Player", "disposition": null}]}'

# Expected: Error listing misconfigured tokens
```

### Config Validation

```bash
# This should FAIL at startup:
MODEL="" RELAY_URL="invalid" python ai-engine/main.py

# Expected: ValueError at startup with clear guidance
```

### Action Validation

```bash
# This should FAIL with unknown field error:
# (LLM sends action with hallucinated field)
{"type": "move_token", "token_id": "t1", "x": 100, "y": 200, "magic_wand": true}

# Expected: "Unknown fields in action 'move_token': magic_wand"
```

---

## Trade-offs

| Benefit | Cost |
|---------|------|
| Clear errors at startup | No graceful degradation (fail fast) |
| No silent failures | User must fix config before retrying |
| Prevents AI takeover of PCs | Stricter validation = fewer edge cases allowed |
| Stale data detection | Scene reload latency (but only if stale) |

**Decision**: Safety over convenience. A crashing app with a clear error is better than a running app with corrupted game state.

---

## Future Additions

- [ ] Health checks for Foundry, LLM, ComfyUI at startup
- [ ] Rate limiting on API endpoints (per-IP, per-endpoint)
- [ ] Audit logging for sensitive operations (JS execution, settings changes)
- [ ] Transaction rollback for failed multi-step operations
- [ ] State consistency checker (run on startup and periodically)
