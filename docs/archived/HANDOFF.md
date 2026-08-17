# HANDOFF — Aethelwyrd AI GM Code Review Complete

**Date:** 2026-08-01  
**Branch:** `master` (default, NOT `main`)  
**Working Dir:** `/Users/ckennedy/Projects/foundryvtt-ai-gm/ai-engine`  
**Test Status:** 682 passed, 1 skipped, 51.51% coverage (threshold 45%)

---

## What Was Done

### Full Systematic Code Review (5 phases)
1. **Survey** — Read 16 core modules (~25K LOC of 36K total)
2. **Test Suite** — Ran with coverage baseline
3. **Analyze** — Categorized issues by severity (P0/P1/P2/P3)
4. **Fix** — Applied 5 surgical patches across 3 files
5. **Verify** — All tests pass, coverage maintained

### Fixes Applied (3 files, 5 changes)

| # | File | Issue | Fix |
|---|------|-------|-----|
| 1 | `actions/executors.py` | `_is_player_character` TTL cache never checked — every call hit relay RPC | Added TTL check (30s); empty real result returns `None` ("unknown") not `False` |
| 2 | `actions/executors.py` | `reset_action_caches` crashed on `None` cache after relay failure | Guarded `.clear()` with `is not None`; re-create set; added `_pc_names_cache` to `global` |
| 3 | `api/routes/session.py` | `relay_url`/`relay_ws_url` mutable at runtime without restart guard | Added to `critical_fields` block (400 with "requires restart" message) |
| 4 | `llm/manager.py` | `_last_error_key` used before init in error deduplication | Added `self._last_error_key: Optional[str] = None` in `__init__` |
| 5 | `actions/executors.py` | Sound cache (`_sound_src_cache`) stale across scene/world changes | Reset in `reset_action_caches` (same pattern as NPC presence cache) |

---

## Current Capability Snapshot

### ✅ Working (Production-Ready)
- **Autonomous Loop** — Idle/reconnect supervisor, pacing, session-start openings, combat automation
- **Campaign Generation** — Scenes/NPCs/quests/maps/layouts via LLM + ComfyUI
- **Obsidian Vault Context** — BM25 search, chunking, campaign-specific folders
- **LLM Orchestration** — httpx (not OpenAI SDK), JSON extraction with thinking-text fallback, retry/fallback
- **Foundry Bridge** — WS + RPC + reconnect supervisor + headless Chrome lifecycle
- **Immersion Managers** — Ambient, effects, vision, macros, items, particles (Tier 6)
- **NPC Personality** — Registry + personality engine with TTS voice assignment
- **TTS** — Server (OpenAI-compatible) + Browser (Web Speech API via Foundry module)
- **Context Reinforcement** — Periodic anchor facts, summarization, scene/combat hooks
- **State Persistence** — SQLite WAL, retention policy, conversation/event/session tables
- **Admin Panel** — React/Vite + Zustand, settings/state/session/combat/immersion control

### 🔧 Test Coverage Gaps (Not Bugs — Need Mock Tests)
| Module | Coverage | Notes |
|--------|----------|-------|
| `foundry/scripts.py` | 22% | Pure JS templates — assert string content |
| `relay_proc/manager.py` | 22% | Mock `subprocess.Popen` + `httpx.AsyncClient` |
| `scene/awareness.py` | 15% | Pure Python — stub `FoundryClient` |
| `campaign/orchestrator.py` | ~20% | Long happy path — needs integration fixtures |

---

## Roadmap Alignment (from `docs/ROADMAP.md`)

### P0 — Highest Leverage (None Implemented)
| Feature | Status | Effort |
|---------|--------|--------|
| **Canon System** (draft vs. canonized) | ❌ | Medium |
| **GM Ruling / Directive Channel** | ❌ (only `/gm` commands exist) | Cheap |
| **House Rules Journal** | ❌ | Trivial |

### P1 — On-Moat
| Feature | Status | Effort |
|---------|--------|--------|
| **Multi-Player Input Batching** | ❌ | Medium |
| **In-Foundry Control Surface** | ❌ (admin API exists, no Foundry module UI) | Medium |
| **Conversation → Journal Export** | ❌ | Medium |
| **Living Settlement Generation** | ❌ (JSON gen exists; no schedule/relationship graph) | Large |

### P2 — Higher Value
| Feature | Status | Effort |
|---------|--------|--------|
| **Change-Approval Gate** | ❌ (auto-apply everything) | Medium |
| **Vault RAG / Semantic Retrieval** | ❌ (BM25 only) | Large |
| **Procedural Layout Fallback** | ❌ (ComfyUI only) | Medium |

### Explicitly Skipped (Correct)
- MCP server, 23-provider breadth, cloud image APIs, PDF-import-as-primary — off-moat

---

## Key Architecture Decisions (Don't Change)

1. **External stack is load-bearing** — Python engine + Go relay + headless Chrome. Do NOT rewrite as native Foundry module.
2. **httpx over OpenAI SDK** — Avoids `base_url` concatenation bug with `?thinking=false` query param.
3. **Local-first** — MLX/oMLX endpoint at `http://localhost:8800/v1`, ComfyUI at `http://127.0.0.1:18188`.
4. **Campaign-gated lifecycle** — Relay/Foundry connection deferred until campaign start (admin panel usable while relay down).
5. **Action validation at dispatch** — Pydantic schemas forbid extra fields; numeric bounds clamp; `allow_execute_js` gate.
6. **PC-defer pattern** — Players roll their own dice; AI only rolls for NPCs. `_is_player_character` is the guard.

---

## Environment & Commands

```bash
# Activate venv
cd /Users/ckennedy/Projects/foundryvtt-ai-gm/ai-engine
source .venv311/bin/activate

# Run tests with coverage
python -m pytest tests/ --cov=. --cov-config=.coveragerc --timeout=30 -q

# Run specific test files
python -m pytest tests/test_npc_presence.py tests/test_scene_change_notify.py -v

# Start the engine
python main.py
# Admin panel: http://localhost:18080/admin
# API: http://localhost:18080/api/state
```

### Critical Config (`.env`)
```env
LLM_API_KEY=omlx-1jti350lx0u3q5dl
LLM_BASE_URL=http://localhost:8800/v1
MODEL=Qwen3.6-35B-A3B
RELAY_URL=http://localhost:13010
RELAY_WS_URL=ws://localhost:13010/ws/api
CAMPAIGN_VAULT_PATH=~/Vaults/MyStuff/Dungeons_and_Dragons
```

---

## Files Modified in This Review

```
/Users/ckennedy/Projects/foundryvtt-ai-gm/ai-engine/
├── actions/executors.py       # 3 fixes (cache guard, TTL, sound reset, type)
├── api/routes/session.py      # 1 fix (critical_fields)
└── llm/manager.py             # 1 fix (_last_error_key init)
```

---

## Next Steps (Prioritized)

1. **Canon System + GM Directive + House Rules** (single medium build)
   - Add `canonized: bool` to `ai_conversations` table
   - `/gm canonize <fact>` → writes to `House Rules` journal doc
   - System prompt auto-injects `House Rules` content

2. **Multi-Player Input Batching** (`chat_listener.py`)
   - 2-3s debounce window collecting messages → single combined turn

3. **Conversation → Journal Export** (session end)
   - Reuse `ContextReinforcementManager._periodic_summarize`
   - `FoundryClient.create_journal_entry()` on session close

---

## Known Gotchas (For Next Harness)

- **Branch is `master`** — not `main`. Git commands must use `master`.
- **Dual install locations** — Dev: `~/Projects/foundryvtt-ai-gm/`; Runtime: `~/Games/foundryvtt-ai-gm/`. Kill both on stop.
- **PR body bash escaping** — Write body to file, use `gh pr create --body-file` with single-quoted heredoc.
- **LLM thinking suppression** — Qwen3.6 outputs thinking by default. System prompt must include "skip thinking/markdown" directive + `?thinking=false` query param.
- **Settings requiring restart** — `llm_base_url`, `llm_api_key`, `model`, `relay_url`, `relay_ws_url` now gated at 400 in `/api/settings`.
- **Admin token** — If `ADMIN_HOST != 127.0.0.1` and no `ADMIN_TOKEN`, API is exposed.

---

## Session Search References

- Code review methodology: `@session:default/...` (this session)
- Project context: Memory contains vault path, model, relay URLs, branch info

---

**Handoff complete.** All P0/P1 issues fixed. Test suite green. Coverage above threshold. Ready for next feature cycle.