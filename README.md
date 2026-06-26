# FoundryVTT AI Gamemaster

An AI-powered D&D 5e GM integrated with FoundryVTT. Players chat normally inside Foundry; the AI listens, makes narrative and mechanical decisions via LLM, and acts — narration, NPC dialogue, dice rolls, token movement, combat, scene changes, and more.

The **admin panel** (`http://localhost:18080`) is a web dashboard for the human GM to monitor the session, adjust settings, test responses, and build campaigns.

---

## Features

- **Chat-driven** — Reads player messages from Foundry, responds with narrative and game actions
- **Action execution** — Narrates, speaks as NPCs, rolls dice, moves tokens, plays sounds, switches scenes, applies conditions
- **Campaign builder** — Scan world, generate full campaign via LLM, deploy scenes/NPCs/journals/quests to Foundry
- **Asset generation** — AI-generated battle maps and NPC portraits via ComfyUI (SDXL) or oMLX
- **Procedural generation** — Encounters, NPCs, quests, and treasure generated on demand and **deployed directly to Foundry** (actors placed, tokens placed, journals created)
- **Combat automation** — NPC turns handled by LLM with configurable timeout and generic fallback; pre-combat snapshots saved for rollback
- **Campaign checkpointing** — Build pipeline checkpoints each phase; crashed deployments resume from last completed step
- **Context management** — Conversation history with token-aware trimming and periodic reinforcement to prevent LLM drift
- **Scene automation** — NPC placement, fog of war, hazard visualization, ambient sound, GM macro generation
- **Rules engine** — Full D&D 5e reference (skills, DCs, conditions, spells, proficiency)
- **Immersion** — Token effects, particle effects, vision/lighting, weather, soundscapes

---

## Quick Start

### Prerequisites
- **FoundryVTT v14** + D&D 5e system
- **Python 3.11+**, **Node.js 18+**, **Go 1.26+** (`brew install go`)
- **Google Chrome** (required for relay's headless browser)
- **LLM service** — local inference (Qwen, LLaMA, etc.) or remote API (OpenRouter, Anthropic, etc.)
- **ComfyUI** (optional, for AI-generated maps and portraits)

### Install
```bash
git clone --recursive git@github.com:cjkennedy1972/foundryvtt-ai-gm.git
cd foundryvtt-ai-gm
chmod +x run.sh start.sh
./run.sh
```

### Configure

Edit `ai-engine/.env`:

| Variable | Description |
|----------|-------------|
| `LLM_BASE_URL` | LLM endpoint (e.g. `http://localhost:18800/v1`) |
| `LLM_API_KEY` | API key for remote LLM (leave empty for local) |
| `CAMPAIGN_VAULT_PATH` | Obsidian vault path (default: `~/Vaults/MyStuff/Dungeons_and_Dragons`) |
| `LLM_COMBAT_TIMEOUT` | Seconds before NPC fallback behavior kicks in (default: `60`) |
| `COMFYUI_BASE_URL` | ComfyUI endpoint (default: `http://localhost:18188`) |

Relay credentials are provisioned automatically on first launch.

### Start
```bash
./start.sh
```

| Service | URL |
|---------|-----|
| Admin Panel | http://localhost:18080 |
| Admin API | http://localhost:18080/api |
| Relay Dashboard | http://localhost:3010 |
| WebSocket | ws://localhost:13010/ws/api |

### Connect to FoundryVTT

1. In FoundryVTT, install the [foundryvtt-rest-api](https://github.com/ThreeHats/foundryvtt-rest-api) module
2. Configure it to connect to `ws://localhost:3010/ws/api`
3. Approve the pairing in the relay dashboard at http://localhost:3010

---

## Architecture

```
FoundryVTT (players + scenes + NPCs)
         │ WebSocket
Embedded Go Relay  :3010
  REST bridge, headless Chrome
         │ WebSocket + REST
AI Engine  :18080  (Python / FastAPI)
  ├── LLM Manager        local or remote LLM
  ├── Chat Listener      player messages → AI
  ├── Action Executor    narrate / roll / move / combat
  ├── Campaign Builder   scan → generate → deploy
  ├── Combat Loop        NPC turns, timeout + fallback
  ├── State Tracker      mode, scene, combat, snapshots
  └── Persistence        SQLite session history
         │
Admin Panel  :18080/admin  (React SPA)
```

---

## Project Structure

```
foundryvtt-ai-gm/
├── ai-engine/
│   ├── main.py              # FastAPI server, 50+ endpoints
│   ├── config.py            # Pydantic settings
│   ├── actions/             # Action executors, schemas, dispatcher
│   ├── campaign/            # Builder, generator, orchestrator, map gen
│   ├── combat/              # Turn loop (with timeout/fallback), difficulty
│   ├── context/             # History window, reinforcement, summarization
│   ├── foundry/             # WebSocket client + chat listener
│   ├── immersion/           # Sound, effects, particles, vision, macros
│   ├── llm/                 # LLM manager, system prompts
│   ├── npc/                 # Personality system, registry
│   ├── persistence/         # SQLite (sessions, events, history)
│   ├── procedural/          # Encounter, NPC, quest, treasure generators
│   ├── rules/               # D&D 5e rules database + engine
│   ├── scene/               # Scene awareness, token positioning
│   ├── state/               # Game state tracker + models
│   └── utils/               # Path safety, token counting
├── docs/
│   ├── api.md               # Full Admin API reference
│   └── advanced.md          # Procedural gen, combat, scene automation, tips
├── admin-panel/             # React SPA (TypeScript)
├── relay/                   # Go relay (git submodule)
├── data/                    # Runtime data (relay DB, credentials)
├── run.sh                   # Install dependencies
└── start.sh                 # Start relay + AI engine
```

---

## Testing

The E2E harness drives the full pipeline — session start, player messages, encounter, combat turns, idle pacing, pause/resume, and more — using scripted mocks. No live relay, LLM API, or FoundryVTT instance needed.

```bash
cd ai-engine && venv/bin/python -m pytest tests/test_e2e_harness.py -v
```

Or as a standalone script:

```bash
cd ai-engine && venv/bin/python tests/test_e2e_harness.py
```

---

## Troubleshooting

**AI not responding to chat:**
1. Verify LLM is running: `curl http://localhost:18800/v1/models`
2. Check relay is connected to Foundry: http://localhost:3010 → Clients
3. Check logs: `tail -f ai-engine/ai-gm.log | grep -i "error\|timeout"`
4. Verify status: `curl http://localhost:18080/api/status`

**Campaign build fails mid-way:**
The build pipeline checkpoints each phase. Simply re-run the build — it will skip completed phases and retry from where it stopped. The checkpoint file is at `campaign_assets/<campaign-name>/build_checkpoint.json`.

**Combat freezes on NPC turn:**
The LLM call is bounded by `LLM_COMBAT_TIMEOUT` (default 60s). If the LLM is slow, the NPC will fall back to a generic attack automatically. If you see persistent 120s freezes, your timeout setting isn't being picked up — check `ai-engine/.env`.

**Relay connection refused:**
```bash
curl http://localhost:3010/api/health   # Is relay running?
./start.sh                              # Start if not
tail -f data/relay/relay.log            # Check for errors
```

**ComfyUI health check fails:**
```bash
python main.py --port 18188             # Start ComfyUI
curl http://localhost:18188/api/health  # Verify
```

**Connection lost after ~9 minutes:**
WebSocket keepalive timeout. Check `tail -f ai-engine/ai-gm.log | grep -i "disconnect\|close"`. Increasing `REQUEST_TIMEOUT` in `ai-engine/foundry/client.py` to `60` can help.

**Maps not appearing on scenes:**
```bash
ls -la ai-engine/campaign_assets/<campaign>_maps/   # Files generated?
tail -f ai-engine/ai-gm.log | grep -i "upload"     # Upload errors?
```

---

## Docs

- [API Reference](docs/api.md) — All admin endpoints with examples
- [Advanced Guide](docs/advanced.md) — Procedural gen, combat, scene automation, ComfyUI, Obsidian vault, AI tuning
- [ComfyUI Setup](ai-engine/campaign/workflows/SETUP_GUIDE.md) — Image generation setup and troubleshooting

---

## Recent Changes

### June 2026 — Reliability & Deployment (PR #68)

- **Procedural deployment** — `generate_encounter`, `generate_npc`, `generate_treasure`, and `generate_quest` now deploy directly to Foundry: actors are created, tokens placed on the active scene, and journal entries created. No more text-only responses.
- **Campaign build checkpointing** — The 6-phase build pipeline writes a checkpoint after each phase. A crashed build resumes from the last completed step on re-run.
- **Combat LLM timeout** — NPC turns are now bounded by `LLM_COMBAT_TIMEOUT` (default 60s). On timeout, a generic fallback (move + basic attack) fires automatically — combat never freezes.
- **Pre-combat snapshots** — Full token and actor state is snapshotted before combat begins. Accessible via `GET /api/combat/snapshot` for manual rollback reference.
- **Asset validation** — After map/portrait generation, the pipeline validates all referenced files exist before proceeding to upload, surfacing missing assets as warnings instead of silently corrupting campaign JSON.

### Earlier 2026

- **Map upload bug fixes** — Fixed percent-encoded map paths, sequential → parallel uploads (4x faster), missing `portraits_attached` field in API response
- **Bounded memory growth** — LRU scene cache, deque-bounded collections, rolling conversation window, 30-day SQLite retention
- **Dependency injection** — All endpoints use injected `app.state`; removed module globals
- **Async correctness** — Added missing `await` calls across chat listener, main, and combat loop
- **Action validation** — Pydantic schemas for all LLM-produced actions with bounds checking
- **Path safety** — Fixed path traversal vulnerabilities in vault loading
