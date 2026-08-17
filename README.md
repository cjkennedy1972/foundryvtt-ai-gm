# FoundryVTT AI Gamemaster

An AI-powered D&D 5e GM integrated with FoundryVTT. Players chat normally inside Foundry; the AI listens, makes narrative and mechanical decisions via LLM, and acts — narration, NPC dialogue, dice rolls, token movement, combat, scene changes, and more.

The **admin panel** (`http://localhost:18080`) is a web dashboard for the human GM to monitor the session, adjust settings, test responses, and build campaigns.

---

## Features

- **Chat-driven** — Reads player messages from Foundry, responds with narrative and game actions
- **Action execution** — ~50 schema-validated actions (narrate, speak as NPC, roll dice, move tokens, apply conditions, play sounds, switch scenes, and more) dispatched from LLM output
- **Campaign builder** — Scan world, generate full campaign via LLM, deploy scenes/NPCs/journals/quests to Foundry; extend an existing campaign's arc or tear it down
- **Campaign-gated startup & world provisioning** — The engine boots without holding a Foundry connection, so the admin panel is usable while the relay is down. Connecting, launching a world, and (for new campaigns) cloning a pre-configured **template world** all happen when you build or start a campaign — see [World Template Cloning](docs/WORLD_TEMPLATE_CLONING.md)
- **Campaign auto-optimizer** — Analyzes newly generated (or existing) scenes/encounters/quests and enriches them with module-based features (walls, lighting, calendar events, loot tables, etc.) based on what's installed in the target world
- **Asset generation** — AI-generated battle maps and NPC portraits via ComfyUI (SDXL) or oMLX
- **Procedural generation** — NPCs, quests, and treasure generated on demand and **deployed directly to Foundry** (actors placed, tokens placed, journals created)
- **Compendium-backed encounters** — Encounters are built from real monster stat blocks in the world's own D&D 5e compendiums, balanced against DMG XP-budget tables (not hallucinated monster names), with varied group shapes (solo/duo/group/horde)
- **Combat automation** — NPC turns run on a bounded LLM loop (generic fallback attack on timeout, so combat never freezes); PC turns also time out to avoid AFK deadlocks; live tactical awareness (cover, flanking, reach); combat state mirrored into a real Foundry `Combat` document
- **Foundry module integrations** — Auto-detects installed modules (midi-qol, DAE, AutoAnimations, item-piles, Simple Calendar, quest log, and 19 more) and adapts generated content and combat behavior to use them
- **Narration (TTS)** — Local neural narration via any OpenAI-compatible `/v1/audio/speech` server (Kokoro, Voxtral, etc.), with 15 character-archetype voices auto-assigned to the GM and each NPC by class/personality — or a zero-server browser fallback using the Web Speech API
- **Semantic lore system** — Automatic entity extraction from campaign events, vault-backed context injection, and query caching (150x+ faster repeats) to keep the AI grounded in session history
- **Approval workflow** — Consequential actions (treasure grants, stat changes, level-ups) require GM approval or auto-approve after 20 seconds for unattended play
- **Context management** — Conversation history with token-aware trimming and periodic reinforcement to prevent LLM drift
- **Scene automation** — NPC placement, fog of war, hazard visualization, ambient sound, GM macro generation
- **Rules engine** — Full D&D 5e reference (skills, DCs, conditions, spells, proficiency)
- **Immersion** — Token effects, particle effects, vision/lighting, weather, soundscapes

---

## Quick Start

### Prerequisites
- **FoundryVTT v14** + D&D 5e system
- **Python 3.11–3.14**, **Node.js 24+**, **Go 1.26+** (`brew install go`)
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

Edit `ai-engine/.env`. The essentials to get a session running:

| Variable | Description |
|----------|-------------|
| `MODEL` | LLM model name — **required**, startup fails without it |
| `LLM_BASE_URL` | LLM endpoint (e.g. `http://localhost:8800/v1`) |
| `LLM_API_KEY` | API key for remote LLM (leave empty for local) |
| Relay Foundry credentials | Managed exclusively in the relay dashboard/database; the AI engine does not read Foundry usernames or passwords from `.env` |
| `FOUNDRY_DATA_PATH` | Foundry user-data directory used for template-world cloning (default: `~/Library/Application Support/FoundryVTT/Data`) |
| `FOUNDRY_WORLD_TEMPLATE_ID` | Template world cloned when a new campaign requests automatic world creation (default: `_ai-gm-template`) |
| `CAMPAIGN_VAULT_PATH` | Obsidian vault path (default: `~/Vaults/MyStuff/Dungeons_and_Dragons`) |
| `LLM_COMBAT_TIMEOUT` / `PC_TURN_TIMEOUT` | Seconds before NPC/PC turn fallback kicks in (default `60`/`180`) |
| `COMFYUI_URL` | ComfyUI endpoint (default `http://127.0.0.1:18188`) |
| `TTS_ENABLED` / `TTS_ENGINE` / `TTS_URL` / `TTS_MODEL` | Narration — `TTS_ENGINE=server` talks to a local `/v1/audio/speech` server, `TTS_ENGINE=browser` uses the client's Web Speech API instead |
| `ALLOW_EXECUTE_JS` | Opt-in flag for the raw JS execution action (off by default — see Troubleshooting/Security below) |
| `ADMIN_HOST` | Bind address (default `127.0.0.1` — the API is loopback-only unless you change it) |
| `ADMIN_TOKEN` | Optional bearer token; set it when `ADMIN_HOST` exposes the API on the LAN. Required on `/api/*` and the admin WebSocket when set; store it in the admin panel browser as localStorage key `aigm_admin_token` |
| `CORS_ORIGINS` | Comma-separated trusted browser origins; never use `*` on a LAN deployment |

Relay credentials are provisioned automatically on first launch. `ai-engine/config.py` has the full list of ~80 settings (LLM tuning, relay internals, image-gen provider, chat/context limits, GM pacing, etc.) if you need to go beyond the defaults.

### Start
```bash
./start.sh
```

| Service | URL |
|---------|-----|
| Admin Panel | http://localhost:18080 |
| Admin API | http://localhost:18080/api |
| Liveness / readiness | http://localhost:18080/health / http://localhost:18080/ready |
| Relay Dashboard | http://localhost:13010 |
| WebSocket | ws://localhost:13010/ws/api |

### Connect to FoundryVTT

The engine no longer connects to Foundry at boot. The relay process and the Foundry
connection are **campaign-gated** — they come up when you build or start a campaign,
so the admin panel is usable while the relay is down. Two paths:

**A. Bring your own world (default).** For a campaign whose world you manage by hand:

1. In FoundryVTT, install the [foundryvtt-rest-api](https://github.com/ThreeHats/foundryvtt-rest-api) module and open your world
2. Point the module at your local relay and pair it: generate a code in the relay dashboard (http://localhost:13010), enter it in the module, then set the world's login credentials under Credentials
3. In the admin panel, build or start the campaign — the engine attaches to that live world and links it to the campaign on first success

**B. Automatic world creation (opt-in).** Enable **Create world** in the Campaign
Builder and the engine clones a pre-configured **template world** (base modules
enabled, relay URL set) instead of a blank one, then launches it headless. Prepare
the template once — see [World Template Cloning](docs/WORLD_TEMPLATE_CLONING.md).

---

## Architecture

```
FoundryVTT (players + scenes + NPCs)
         │ WebSocket
Embedded Go Relay  :13010
  REST bridge, headless Chrome (managed by ai-engine/relay_proc)
         │ WebSocket + REST
AI Engine  :18080  (Python / FastAPI, main.py is a thin lifespan/wiring layer)
  ├── api/routes/        10 routers — campaign, combat, control, immersion,
  │                      npc, procedural, rules, scene, session, system
  ├── LLM Manager        local or remote LLM
  ├── Chat Listener      player messages → AI
  ├── Semantic RAG       entity extraction, vault injection, query caching
  ├── Approval Workflow  consequential action gating, timeout auto-approval
  ├── Action Dispatcher  ~50 schema-validated executors
  ├── Campaign Builder   scan → generate → deploy → auto-optimize
  ├── Combat Loop        NPC/PC turns, timeout + fallback, module-aware
  ├── Module Registry    25 addon integrations, hook-based
  ├── TTS Service        pluggable narration, archetype voice assignment
  ├── State Tracker      mode, scene, combat, snapshots
  └── Persistence        SQLite session history
         │
Admin Panel  :18080/admin  (React + Zustand SPA, JavaScript/Vite)
```

---

## Project Structure

```
foundryvtt-ai-gm/
├── ai-engine/
│   ├── main.py               # FastAPI app + lifespan wiring (~590 lines)
│   ├── config.py             # Pydantic settings (~80 fields)
│   ├── api/
│   │   ├── deps.py           # AppState, ApiError, require_foundry
│   │   └── routes/           # campaign, combat, control, immersion, npc,
│   │                         # procedural, rules, scene, session, system
│   ├── actions/               # ~50 execute_* action executors, schemas, dispatcher
│   ├── campaign/
│   │   ├── orchestrator.py    # build/extend/teardown/deploy pipeline
│   │   ├── generator.py       # LLM campaign-structure generation
│   │   ├── modules/           # 25 Foundry addon integrations + hook registry
│   │   ├── auto_optimizer.py  # scene/encounter/quest enrichment
│   │   └── module_discovery.py# LLM-driven module capability discovery
│   ├── combat/
│   │   ├── loop.py            # NPC/PC turn loop, timeout + fallback
│   │   ├── compendium_generator.py  # real-monster, DMG-balanced encounters
│   │   ├── tactics.py          # cover/flanking/line-of-sight
│   │   └── difficulty.py
│   ├── context/                # History window, reinforcement, summarization
│   ├── foundry/                 # WebSocket/REST client, chat listener, JS snippets
│   ├── relay_proc/              # Spawns/manages the embedded Go relay subprocess
│   ├── immersion/                # Sound, effects, particles, vision, macros
│   ├── llm/                      # LLM manager, system prompts
│   ├── npc/                       # Personality system, registry
│   ├── persistence/                # SQLite (sessions, events, history)
│   ├── procedural/                  # NPC, quest, treasure generators
│   ├── rules/                        # D&D 5e rules database + engine
│   ├── scene/                         # Scene awareness, token positioning
│   ├── state/                          # Game state tracker + models
│   ├── tts/                             # TTS service, voice archetype assigner
│   ├── utils/                            # Path safety, token counting
│   ├── admin-panel/                       # React SPA (JavaScript, Vite + Zustand)
│   └── tests/                              # 81 test files
├── docs/
│   ├── index.md                  # Landing page & overview
│   ├── README.md                 # Docs guide & website build instructions
│   ├── mkdocs.yml                # MkDocs configuration (Material theme)
│   ├── getting-started/          # Installation & quickstart
│   ├── user-guide/               # How to play (sessions, combat, settlements)
│   ├── features/                 # Feature deep-dives (generation, lore, approval)
│   ├── api/                      # REST endpoints & integrations
│   ├── troubleshooting/          # FAQs & common issues
│   ├── archived/                 # Development docs (implementation guides, architecture)
├── relay/                # Go relay (git submodule, forked — see Acknowledgments)
├── data/                 # Runtime data (relay DB, credentials)
├── .github/workflows/    # CI (fast-tier) + nightly live-Foundry E2E
├── run.sh                # Install dependencies
└── start.sh              # Start relay + AI engine
```

---

## Testing

The E2E harness drives the full pipeline — session start, player messages, encounter, combat turns, idle pacing, pause/resume, and more — using scripted mocks. No live relay, LLM API, or FoundryVTT instance needed.

```bash
cd ai-engine && .venv/bin/python -m pytest tests/test_e2e_harness.py -v
```

`ai-engine/tests/` has 81 files in total. Beyond the E2E harness, notable suites:

- **Combat**: `test_combat_foundry_sync.py`, `test_combat_tactics.py`, `test_compendium_generator.py`, `test_compendium_integration.py`, `test_initiative.py`, `test_dnd5e_activities.py`, `test_attack_with_item.py`
- **Actions/dispatch**: `test_action_validation_and_dispatch.py`, `test_move_token_resolution.py`, `test_play_sound.py`, `test_skill_check_player_defer.py`
- **Campaign**: `test_campaign_count_compliance.py`, `test_campaign_restart_and_portraits.py`, `test_orchestrator_assets.py`, `test_campaign_connection_lifecycle.py`, `test_world_template_clone.py`
- **Modules/registry**: `test_module_registry.py`, `test_foundry_client_world_info.py`
- **Reliability**: `test_reader_concurrency.py`, `test_retry_dedup.py`, `test_actor_resolution.py`, `test_echo_suppression.py`, `test_gm_pacing.py`, `test_context_window_manager.py`, `test_client_reconnect_supervisor.py`

Run the full suite (or any subset) the same way:

```bash
cd ai-engine && .venv/bin/python -m pytest tests -v
```

### CI/CD

- **`.github/workflows/ci.yml`** — runs on every push/PR: `ai-engine-tests` (pytest), `relay-checks` (Go `go test` + TypeScript `tsc --noEmit` + a Jest subset that doesn't need live infra), `admin-panel-build` (Vite production build).
- **`.github/workflows/nightly-e2e.yml`** — self-hosted, runs nightly against a real dockerized FoundryVTT instance for full live-relay coverage the fast tier can't provide.

---

## Troubleshooting

**AI not responding to chat:**
1. Verify LLM is running: `curl http://localhost:8800/v1/models`
2. Check relay is connected to Foundry: http://localhost:13010 → Clients
3. Check logs: `tail -f ai-engine/ai-gm.log | grep -i "error\|timeout"`
4. Verify status: `curl http://localhost:18080/api/status`

**Campaign build fails mid-way:**
`build_campaign()` runs its phases (scan → generate → save → assets → deploy → enrich) top-to-bottom in one call; a failed phase logs a warning and the pipeline degrades gracefully where it can, but there is currently no per-phase checkpoint file to resume from — a crashed build needs a full re-run. Check `ai-engine/ai-gm.log` for which phase failed before retrying.

**Combat freezes on NPC turn:**
The LLM call is bounded by `LLM_COMBAT_TIMEOUT` (default 60s). If the LLM is slow, the NPC will fall back to a generic attack automatically. A stalled PC turn similarly times out after `PC_TURN_TIMEOUT` (default 180s) instead of blocking the encounter. If you see persistent freezes past these bounds, the setting isn't being picked up — check `ai-engine/.env`.

**Relay connection refused:**
```bash
curl http://localhost:13010/api/health   # Is relay running?
./start.sh                               # Start if not
tail -f data/relay/relay.log             # Check for errors
```

**ComfyUI health check fails:**
```bash
python main.py --port 18188             # Start ComfyUI
curl http://localhost:18188/api/health  # Verify
```

**Connection lost / dropped session:**
A reconnect supervisor proactively heals dropped connections on a ~10s interval — even while the session is idle and nothing is being sent — so pushed player/roll/combat events aren't silently missed after a drop. Reconnects use exponential backoff and will relaunch the headless Foundry session if the relay reports no connected client. Check `tail -f ai-engine/ai-gm.log | grep -i "disconnect\|reconnect\|supervisor\|close"`. RPC reply timeouts are governed by `relay_rpc_timeout` (default 45s) in `ai-engine/config.py`.

**Maps not appearing on scenes:**
```bash
ls -la ai-engine/campaign_assets/<campaign>_maps/   # Files generated?
tail -f ai-engine/ai-gm.log | grep -i "upload"     # Upload errors?
```

**"Raw JS execution disabled" errors:**
The `execute_js` action is gated behind `ALLOW_EXECUTE_JS` (default `false`) since it's reachable from player chat via the LLM and is a prompt-injection risk if left on. Set `ALLOW_EXECUTE_JS=true` in `ai-engine/.env` only if you understand and accept that risk.

---

## Documentation

The documentation is organized into **user-facing guides** and **developer references**. Build a website with MkDocs:

```bash
pip install mkdocs mkdocs-material
mkdocs serve              # Preview locally
mkdocs build              # Generate static site
```

### For Players & GMs

- **[Getting Started](docs/getting-started/installation.md)** — Installation and quickstart
- **[User Guide](docs/user-guide/overview.md)** — How to play: sessions, combat, settlements, NPCs
- **[Features](docs/features/overview.md)** — Deep-dives: campaign generation, combat AI, living world, lore system, approval workflow
- **[Troubleshooting](docs/troubleshooting/faq.md)** — FAQs and common issues
- **[API Reference](docs/api/rest-endpoints.md)** — REST endpoints for integrations

### For Developers & Contributors

- [Archived Development Docs](docs/archived/) — Planning documents, architecture decisions, implementation guides, code review findings
- [ComfyUI Setup](ai-engine/campaign/workflows/SETUP_GUIDE.md) — Image generation setup and troubleshooting

---

## Acknowledgments

The embedded relay (`relay/`, a git submodule) is forked from [ThreeHats/foundryvtt-rest-api-relay](https://github.com/ThreeHats/foundryvtt-rest-api-relay) — MIT licensed, see `relay/LICENSE`. It pairs with the [foundryvtt-rest-api](https://github.com/ThreeHats/foundryvtt-rest-api) Foundry module (same author, referenced in Quick Start above); together they're the WebSocket/REST bridge this project's AI engine talks to. All credit for the original relay/module design and implementation goes to ThreeHats — this project builds the AI GM on top of that bridge.

---

## Recent Changes

### Campaign-gated lifecycle & world provisioning

- **Deferred connection** — The relay process and Foundry WebSocket no longer start at engine boot; they come up when a campaign is built or started, so the admin panel works while the relay is down. Relay start/stop from the dashboard no longer forces the Foundry desktop app up or down.
- **Template-world cloning** — Automatic world creation clones a pre-configured template world (base modules enabled, relay URL set, unpaired) instead of Foundry's blank `createWorld`, so every new campaign world starts with the same base module configuration. Module config lives in the world's LevelDB settings store, so this is a filesystem clone (`foundry/world_template.py`), not a create flag. New settings: `FOUNDRY_DATA_PATH`, `FOUNDRY_WORLD_TEMPLATE_ID`. See [World Template Cloning](docs/WORLD_TEMPLATE_CLONING.md).
- **Manual pairing default** — New campaigns default to a world you create and pair by hand; automatic cloning is opt-in via **Create world** in the Campaign Builder.

### Reconnect supervisor

The Foundry client runs a self-healing supervisor that proactively reconnects a dropped socket on a ~10s interval, even while the session is idle. Inbound player/roll/combat events are pushed, so an idle drop previously went unnoticed until the next outbound request — an autonomous GM that silently stops receiving events looks dead at the table. The supervisor starts on first connect (staying campaign-gated) and stops on intentional disconnect.

### Modular architecture

`main.py` (was 3,435 lines) is now a ~590-line lifespan/wiring module; its route handlers moved into focused routers under `api/routes/` (now 10 routers, 98 handlers, 3,447 lines total — `control.py` was added after this refactor). `campaign/orchestrator.py`'s 47 inline `"module-id" in mods` checks were replaced by a `ModuleIntegration` hook registry (`campaign/modules/`), which has since grown to cover 25 Foundry addons. TTS playback and large JS snippets were extracted out of `actions/executors.py` into `tts/playback.py` and `foundry/scripts.py`.

### Combat & encounters

- **Compendium-backed encounters** — `combat/compendium_generator.py` replaces hallucinated monster names with real stat blocks pulled from the world's own compendiums, balanced against DMG CR/XP tables with randomized encounter "shape" (solo/duo/group/horde).
- **midi-qol / DAE / AutoAnimations awareness** — the combat loop detects these (and CombatBooster) at combat start and adjusts both its own logic and the NPC-turn LLM prompt accordingly; dnd5e 5.x `system.activities` schema is built directly for module compatibility.
- **PC turn timeout** — an AFK or lost player message no longer stalls the whole encounter.
- **Live Foundry Combat sync** — the loop's turn order is mirrored into a real Foundry `Combat` document (best-effort; the Python-side state remains authoritative).

### Foundry module integrations & campaign auto-optimizer

25 addon integrations (midi-qol, DAE, AutoAnimations, item-piles, lootsheet-simple, Simple Calendar, RPGX Quest Log, Vision-5e, Fog Weaver, and more) now shape generated NPCs/journals/scenes automatically. A new auto-optimizer (`campaign/auto_optimizer.py`) can analyze existing or newly generated scenes/encounters/quests and enrich them with whatever those modules provide.

### Narration

TTS moved from a fixed 6-voice OpenAI-style scheme to a 15-archetype system (8 male, 7 female) so distinct D&D classes actually sound distinct, backed by any OpenAI-compatible local TTS server (evaluated Kokoro, Spark-TTS, and Voxtral — Kokoro won on speed and real voice-parameter support). A browser-only fallback mode using the Web Speech API remains available when no TTS server is running.

### Admin panel

Sidebar navigation regrouped by session phase (get oriented → build/manage a campaign → run a live session → dev tooling → settings). Saved Campaigns and Campaign Start merged into one Campaigns page; NPC Manager gained a formatted detail view instead of raw JSON; Session Viewer and GM Chat separated so live-session tooling doesn't crowd one page.

### CI/CD

Added `.github/workflows/ci.yml` (fast-tier: ai-engine pytest, relay Go/TS/Jest checks, admin-panel build) and `nightly-e2e.yml` (self-hosted, live dockerized FoundryVTT run) — previously there was no automated test gate on push/PR.

### Security

`execute_js` (arbitrary JavaScript execution in the Foundry client) is now gated behind `ALLOW_EXECUTE_JS`, off by default, since it's reachable from player chat via the LLM.

### Semantic lore system

A semantic RAG system with entity extraction automatically learns session history, stores it in a searchable vault, and injects relevant lore into the AI's context before each decision. Query results are cached (150x+ faster repeats) to keep the GM responsive during active play. The system is vault-agnostic — Obsidian, plain files, or filesystem stores all work.

### Approval workflow & unattended play

Consequential actions (granting items, changing stats, level-ups) now gate behind a GM approval workflow. For attended play, the GM can approve/reject via the admin API. For **unattended play** (autonomous AI), actions auto-approve after 20 seconds with a warning log, allowing the story to continue without blocking. The approval mode is configurable per campaign.

### Documentation & website

Complete user-facing documentation (17 markdown files) organized into Getting Started, User Guide, Features, API, and Troubleshooting sections. Configured for MkDocs website generation (Material theme, dark mode, search). Development docs (implementation guides, architecture notes, code reviews) moved to `docs/archived/` and kept accessible to contributors but invisible to end-users.

### Reliability (carried forward from the last README update)

Reader-loop deadlock fixed (relay events run on a dedicated worker instead of inline in the WebSocket reader), narration turns are serialized with a single turn lock, retries no longer re-narrate already-delivered dialogue, `update_hp` resolves hallucinated actor identifiers against the live actor list, and dropped relay connections are ridden out with reconnect + headless-session relaunch.
