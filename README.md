# FoundryVTT AI Gamemaster

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org)
[![Node 24+](https://img.shields.io/badge/Node-24%2B-green.svg)](https://nodejs.org)
[![Go 1.26+](https://img.shields.io/badge/Go-1.26%2B-cyan.svg)](https://golang.org)

An AI-powered **autonomous Game Master** for D&D 5e in FoundryVTT. Players chat normally inside Foundry; the AI listens, makes narrative and mechanical decisions, and executes them — managing NPCs, running combat, controlling scene atmosphere, and narrating with character voices — all unattended.

The **admin panel** (`http://localhost:18080`) is a web dashboard where the human GM monitors the session, builds campaigns, reviews decisions, and tests AI responses without running a full game.

---

## Features

- **Chat-driven** — Reads player messages from Foundry, responds with narrative and game actions
- **Action execution** — ~47 schema-validated actions (narrate, speak as NPC, roll dice, move tokens, apply conditions, play sounds, switch scenes, and more) dispatched from LLM output
- **Campaign builder** — Scan world, generate full campaign via LLM, deploy scenes/NPCs/journals/quests to Foundry; extend an existing campaign's arc or tear it down
- **Lore enrichment** — Fold another source (a PDF, a folder of PDF / `.md` / `.txt` files, or Foundry journals) into a campaign that already exists, without regenerating it. New world and history material is appended to `Worldbuilding.md` / `History.md`, and NPCs, locations, factions and artifacts are matched to existing ones and filled in or added. Existing content wins; contradictions go to the canon review queue
- **Campaign-gated startup** — The engine boots without holding a Foundry connection, so the admin panel is usable while Foundry is down. The engine connects when you build or start a campaign. You create the Foundry world yourself; the AI-GM does not create worlds
- **Campaign auto-optimizer** — Analyzes newly generated (or existing) scenes/encounters/quests and enriches them with module-based features (walls, lighting, calendar events, loot tables, etc.) based on what's installed in the target world
- **Asset generation** — AI-generated battle maps (muted D&D palette, 128 px grid, 2× hires detail pass) and NPC portraits via ComfyUI (SDXL) or oMLX
- **Cinematic art** — Optional establishing stills (z-image turbo, 1280×704) and short video clips (LTX-Video 2B, 4 s) for social scenes (taverns, settlements, temples, etc.), deployed as Storyteller Cinema backdrops
- **Player NPC chat** — `/npc <name>: <message>` lets players talk directly to any NPC; conversations are grounded in the NPC's record, goals, and nearby vault lore, and logged as events
- **Undo** — `/gm undo` or `POST /api/undo` reverses the AI's last HP change, token move, condition, or exhaustion change
- **Character backstory** — `POST /api/backstory` and a character-sheet button generate a lore-grounded backstory from the campaign world and vault
- **Procedural generation** — NPCs, quests, and treasure generated on demand and **deployed directly to Foundry** (actors placed, tokens placed, journals created)
- **Compendium-backed encounters** — Encounters are built from real monster stat blocks in the world's own D&D 5e compendiums, balanced against DMG XP-budget tables (not hallucinated monster names), with varied group shapes (solo/duo/group/horde)
- **Player pacing** — Players get time to act between GM beats and combat turns; token and placeable edits count as table activity
- **Combat automation** — NPC turns run on a bounded LLM loop (generic fallback attack on timeout, so combat never freezes); PC turns also time out to avoid AFK deadlocks; live tactical awareness (cover, flanking, reach); combat state mirrored into a real Foundry `Combat` document
- **Foundry module integrations** — Auto-detects installed modules (midi-qol, DAE, AutoAnimations, item-piles, Simple Calendar, Storyteller Cinema, StoryTeller X, quest log, and 19 more) and adapts generated content and combat behavior to use them
- **World CLI** — Optional typed, validated, dry-runnable command surface (`fvtt-world-cli`); routes reads, entity writes, scene activation, combat commands, file uploads, and wall/light/sound placement through it when enabled
- **Narration (TTS)** — Local neural narration via any OpenAI-compatible `/v1/audio/speech` server (Kokoro, Voxtral, etc.), with 15 character-archetype voices auto-assigned to the GM and each NPC by class/personality — or a zero-server browser fallback using the Web Speech API
- **Semantic lore system** — Automatic entity extraction from campaign events, vault-backed context injection, and query caching (150x+ faster repeats) to keep the AI grounded in session history
- **Action audit trail** — Every dispatched action is recorded with its parameters and outcome; mechanical changes (hit points, conditions, resources, rests, encounters) are flagged as consequential and logged at INFO, plus appended to the replayable event log
- **Context management** — Conversation history with token-aware trimming and periodic reinforcement to prevent LLM drift
- **Scene automation** — NPC placement, fog of war, hazard visualization, ambient sound, GM macro generation
- **Rules engine** — Full D&D 5e reference (skills, DCs, conditions, spells, proficiency)
- **Immersion** — Token effects, particle effects, vision/lighting, weather, soundscapes

---

## Quick Start

### Prerequisites
- **FoundryVTT v14** + D&D 5e system
- **Python 3.11–3.14**, **Node.js 24+**
- **Chromium for Playwright** — installed with `playwright install chromium` (the engine drives its own headless GM browser)
- **Go 1.26+** (`brew install go`) — only if you run the legacy relay (`FOUNDRY_BACKEND=relay`)
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
| `FOUNDRY_BACKEND` | `playwright` (default): the engine drives a headless GM browser itself. `relay`: the legacy path through the Go relay, deprecated |
| `FOUNDRY_URL` | Foundry server the engine logs into (default `http://localhost:30000`) |
| `FOUNDRY_USERNAME` / `FOUNDRY_PASSWORD` | The dedicated GM account the engine plays as (e.g. `ai-gm`). Used by the playwright backend. In relay mode, credentials live in the relay instead |
| `CAMPAIGN_VAULT_PATH` | Obsidian vault path (default: `~/Vaults/MyStuff/Dungeons_and_Dragons`) |
| `LLM_COMBAT_TIMEOUT` / `PC_TURN_TIMEOUT` | Seconds before NPC/PC turn fallback kicks in (default `60`/`180`) |
| `COMFYUI_URL` | ComfyUI endpoint (default `http://127.0.0.1:18188`) |
| `TTS_ENABLED` / `TTS_ENGINE` / `TTS_URL` / `TTS_MODEL` | Narration — `TTS_ENGINE=server` talks to a local `/v1/audio/speech` server, `TTS_ENGINE=browser` uses the client's Web Speech API instead |
| `ALLOW_EXECUTE_JS` | Opt-in flag for the raw JS execution action (off by default — see Troubleshooting/Security below) |
| `ADMIN_HOST` | Bind address (default `127.0.0.1` — the API is loopback-only unless you change it) |
| `ADMIN_TOKEN` | Optional bearer token; set it when `ADMIN_HOST` exposes the API on the LAN. Required on `/api/*` and the admin WebSocket when set; store it in the admin panel browser as localStorage key `aigm_admin_token` |
| `CORS_ORIGINS` | Comma-separated trusted browser origins; never use `*` on a LAN deployment |
| `CINEMA_ART_ENABLED` | Generate cinematic establishing stills for social scenes during campaign builds (default `false`). Needs ComfyUI with z-image turbo models |
| `WORLD_CLI_WRITES_ENABLED` | Route entity writes, scene activation, combat commands, and wall/light/sound placement through `fvtt-world-cli` when available (default `false`; the relay is the fallback in relay mode) |
| `VAULT_EMBEDDINGS_ENABLED` | Semantic vault search (default on). Needs `pip install -r ai-engine/requirements-embeddings.txt`; without it the engine logs a warning and falls back to BM25 keyword search |

**What `ADMIN_TOKEN` does and does not cover.** When set, it is required on
`/api/*` and the admin WebSocket. It is *not* applied to the static admin panel
at `/admin` or to generated narration audio at `/audio/<name>` — both stay
reachable by anyone who can reach the port. The panel is inert without an
authenticated `/api/*`, and audio filenames are unguessable, so this is a
deliberate trade-off rather than an oversight; if that is not acceptable for
your network, keep `ADMIN_HOST=127.0.0.1` and reach the panel over SSH port
forwarding instead of exposing it.

In Foundry, set the same token in the **AI GM — Control Panel** module
settings. That setting is client-scoped, so it lives only in the GM's browser
and is never replicated to player clients.

`ai-engine/config.py` has the full list of ~95 settings (LLM tuning, image-gen provider, chat/context limits, GM pacing, etc.) if you need to go beyond the defaults.

### Start
```bash
./start.sh
```

| Service | URL |
|---------|-----|
| Admin Panel | http://localhost:18080 |
| Admin API | http://localhost:18080/api |
| Liveness / readiness | http://localhost:18080/health / http://localhost:18080/ready |
| Relay Dashboard (relay mode only) | http://localhost:13010 |

### Connect to FoundryVTT

The engine connects to Foundry when you build or start a campaign, not at boot, so the
admin panel is usable while Foundry is down.

You create the Foundry world yourself — the AI-GM attaches to a world, it does not create one:

1. In FoundryVTT v14, create your world with the D&D 5e system, and create a dedicated GM user for the engine (e.g. `ai-gm`) with a password
2. Put that user's name and password in `ai-engine/.env` as `FOUNDRY_USERNAME` and `FOUNDRY_PASSWORD`, and leave `FOUNDRY_BACKEND=playwright`
3. Open the world in Foundry (the engine logs into the world Foundry is already serving)
4. In the admin panel, build or start the campaign. The engine logs in as the GM user and links the world to the campaign on first success. If Foundry is serving a different world than the campaign's linked world, the start is refused and tells you which world to switch to

> **Deprecated: the `foundryvtt-rest-api` module.** The playwright backend does not use it, so you do not need to install it. It exists only for the legacy relay path (`FOUNDRY_BACKEND=relay`), which is deprecated and will be removed. If you are on relay mode, the steps are: install the [foundryvtt-rest-api](https://github.com/ThreeHats/foundryvtt-rest-api) module, pair it with the relay in the relay dashboard (http://localhost:13010), and set the world's login credentials there.

---

## Architecture

```
FoundryVTT (players + scenes + NPCs)
         ▲ GM session: headless Chromium logged in as the GM user
         │ (page.evaluate, Foundry hooks; no relay, no Foundry module)
AI Engine  :18080  (Python / FastAPI, main.py is a thin lifespan/wiring layer)
  ├── foundry/           PlaywrightTransport: login, reconnect, event channels,
  │                      page-side ops ported from the REST module's handlers
  ├── api/routes/        19 routers — backstory, camera, campaign, canon, combat,
  │                      control, downtime, immersion, npc, procedural, rules,
  │                      scene, session, session_control, setup, system, undo,
  │                      world_cli
  ├── LLM Manager        local or remote LLM
  ├── Chat Listener      player messages → AI
  ├── Semantic RAG       entity extraction, vault injection, query caching
  ├── Action Audit       consequential classification + per-action recording
  ├── Action Dispatcher  ~47 schema-validated executors
  ├── Cinematic Artist   establishing stills & clips for social scenes
  ├── Campaign Builder   scan → generate → deploy → auto-optimize
  ├── Combat Loop        NPC/PC turns, timeout + fallback, module-aware
  ├── Module Registry    25 addon integrations, hook-based
  ├── TTS Service        pluggable narration, archetype voice assignment
  ├── State Tracker      mode, scene, combat, snapshots
  └── Persistence        SQLite session history
         │
Admin Panel  :18080/admin  (React + Zustand SPA, JavaScript/Vite)

Legacy (deprecated, FOUNDRY_BACKEND=relay only):
  Embedded Go Relay :13010 ◄──► FoundryVTT via the foundryvtt-rest-api module
```

---

## Project Structure

```
foundryvtt-ai-gm/
├── ai-engine/
│   ├── main.py               # FastAPI app + lifespan wiring (~400 lines)
│   ├── config.py             # Pydantic settings (~95 fields)
│   ├── api/
│   │   ├── deps.py           # AppState, ApiError, require_foundry
│   │   └── routes/           # 19 routers: backstory, camera, campaign, canon,
│   │                         # combat, control, downtime, immersion, npc,
│   │                         # procedural, rules, scene, session,
│   │                         # session_control, setup, system, undo, world_cli
│   ├── actions/               # ~47 execute_* action executors, schemas, dispatcher, undo
│   ├── campaign/
│   │   ├── orchestrator.py    # build/extend/teardown/deploy pipeline
│   │   ├── generator.py       # LLM campaign-structure generation
│   │   ├── cinematic_art.py    # establishing stills & clips (z-image, LTX-Video)
│   │   ├── modules/           # 27 Foundry addon integrations + hook registry
│   │   ├── auto_optimizer.py  # scene/encounter/quest enrichment
│   │   └── module_discovery.py# LLM-driven module capability discovery
│   ├── combat/
│   │   ├── loop.py            # NPC/PC turn loop, timeout + fallback
│   │   ├── compendium_generator.py  # real-monster, DMG-balanced encounters
│   │   ├── tactics.py          # cover/flanking/line-of-sight
│   │   └── difficulty.py
│   ├── context/              # History window, reinforcement, summarization
│   ├── downtime/             # Between-session player actions, resolved offline
│   ├── evals/                # 30-scenario replay corpus + scoring harness
│   ├── events/               # Append-only event log, replay-to-projection
│   ├── foundry/              # PlaywrightTransport (default), page-side ops, chat listener;
│   │                         # relay WebSocket client kept for FOUNDRY_BACKEND=relay
│   ├── relay_proc/           # Legacy: spawns/manages the Go relay (relay mode only, deprecated)
│   ├── immersion/            # Sound, effects, particles, vision, macros
│   ├── llm/                  # LLM manager, system prompts
│   ├── npc/                  # Personality system, registry
│   ├── orchestrator/         # SceneDirector — which ready NPC acts this tick
│   ├── persistence/          # SQLite (sessions, events, history)
│   ├── procedural/           # NPC, quest, treasure generators
│   ├── referee/              # Rules authority between narrator LLM and dispatch
│   ├── rules/                # D&D 5e rules database + engine
│   ├── scene/                # Scene awareness, token positioning
│   ├── state/                # Game state tracker + models
│   ├── tts/                  # TTS service, voice archetype assigner
│   ├── utils/                # Path safety, token counting
│   ├── vault/                # Vault indexing, embeddings, semantic RAG
│   ├── world/                # Settlement model + generator
│   ├── world_tick/           # Off-session world clock
│   ├── worldclock/           # Advances world time, NPC goals, settlement schedules
│   ├── admin-panel/          # React SPA (JavaScript, Vite + Zustand)
│   └── tests/                # 313 test files (90% line + branch coverage floor)
├── docs/
│   ├── index.md              # Landing page & overview
│   ├── README.md             # Docs guide & website build instructions
│   ├── getting-started/      # Installation & quickstart
│   ├── user-guide/           # How to play (sessions, combat, settlements)
│   ├── features/             # Feature deep-dives (generation, lore, audit trail)
│   ├── api/                  # REST endpoints & integrations
│   ├── troubleshooting/      # FAQs & common issues
│   ├── ROADMAP.md            # Positioning, architecture decisions, backlog status
├── relay/                # Legacy Go relay (git submodule, deprecated — see Acknowledgments)
├── data/                 # Runtime data (relay DB and credentials in relay mode)
├── .github/workflows/    # CI (fast-tier) + nightly live-Foundry E2E
├── mkdocs.yml            # MkDocs configuration (Material theme)
├── run.sh                # Install dependencies
└── start.sh              # Start the AI engine
```

---

## Testing

The E2E harness drives the full pipeline — session start, player messages, encounter, combat turns, idle pacing, pause/resume, and more — using scripted mocks. No live relay, LLM API, or FoundryVTT instance needed.

```bash
cd ai-engine && .venv/bin/python -m pytest tests/test_e2e_harness.py -v
```

The **eval harness** (`ai-engine/evals/`) replays a 30-scenario corpus through the same pipeline against a real model and emits a scored report (hard checks, canon contradiction rate, drift vs frozen baselines). The **contradiction rate** — how often a generated turn contradicts canonised fact or prior events, detected by authored canon patterns, an event-log vitality scan, and an optional LLM judge (`--judge`) — is the v2.0 north-star metric; it is published in `ai-engine/evals/METRICS.md` and tracked across builds in `ai-engine/evals/metrics/history.jsonl` (`--record`). One command:

```bash
cd ai-engine && python -m evals.replay --backend scripted   # CI gate, no model needed
cd ai-engine && python -m evals.replay --backend live       # measures the real model
```

See `ai-engine/evals/README.md` for the corpus format and how to add scenarios.

`ai-engine/tests/` has 313 files in total (97.7% line coverage, 90% branch coverage floor enforced in CI). Beyond the E2E harness, notable suites:

- **Combat**: `test_combat_foundry_sync.py`, `test_combat_tactics.py`, `test_compendium_generator.py`, `test_compendium_integration.py`, `test_initiative.py`, `test_dnd5e_activities.py`, `test_attack_with_item.py`
- **Actions/dispatch**: `test_action_validation_and_dispatch.py`, `test_move_token_resolution.py`, `test_play_sound.py`, `test_skill_check_player_defer.py`
- **Campaign**: `test_campaign_count_compliance.py`, `test_campaign_restart_and_portraits.py`, `test_orchestrator_assets.py`, `test_campaign_connection_lifecycle.py`
- **Modules/registry**: `test_module_registry.py`, `test_foundry_client_world_info.py`
- **Reliability**: `test_reader_concurrency.py`, `test_retry_dedup.py`, `test_actor_resolution.py`, `test_echo_suppression.py`, `test_gm_pacing.py`, `test_context_window_manager.py`, `test_client_reconnect_supervisor.py`

Run the full suite (or any subset) the same way:

```bash
cd ai-engine && .venv/bin/python -m pytest tests -v
```

### CI/CD

- **`.github/workflows/ci.yml`** — runs on every push/PR: `ai-engine-tests` (pytest), `relay-checks` (Go `go test` + TypeScript `tsc --noEmit` + a Jest subset that doesn't need live infra), `admin-panel-build` (Vite production build).
- **`.github/workflows/nightly-e2e.yml`** — runs nightly on the self-hosted runner labelled `x86-localai01` against a real dockerized FoundryVTT instance, for live-relay coverage the fast tier can't provide. It builds the module from [upstream main](https://github.com/ThreeHats/foundryvtt-rest-api), the same source end users install from, so a breaking upstream change surfaces here first; foundryvtt.com credentials come from the `FOUNDRY_USERNAME` / `FOUNDRY_PASSWORD` repo secrets.

---

## Troubleshooting

**AI not responding to chat:**
1. Verify LLM is running: `curl http://localhost:8800/v1/models`
2. Check the engine is connected to Foundry: `curl http://localhost:18080/api/status` (`"connected": true`)
3. Check logs: `tail -f ai-engine/ai-gm.log | grep -i "error\|timeout\|unparseable"`. An `unparseable reply` warning with `finish_reason=stop` means the model returned no JSON in its content; the engine reads the reasoning field when content is empty, and logs the finish reason and field lengths when neither has JSON

**Foundry login fails (playwright backend):**
The engine logs in at `FOUNDRY_URL/join` as `FOUNDRY_USERNAME`. Check that the user exists in the world, that `FOUNDRY_PASSWORD` in `ai-engine/.env` is current, and that the world is open in Foundry. A failed login is retried in the background and then surfaces as a `ConnectionError` on the next call. Logs: `grep -i "playwright" ai-engine/ai-gm.log`.

**Campaign start refused: "Foundry is serving '<A>' but campaign is linked to '<B>'":**
Foundry serves one world at a time. Switch the open world in Foundry to the campaign's linked world, then start the campaign again.

**Browser drops or reconnects (playwright backend):**
The engine relaunches and logs back in when the headless browser exits, which is logged as `Playwright browser disconnected`. A call made while the browser is down reconnects first. A write that was in flight when the browser died is not replayed, so check the world if an action looks half-applied. Persistent drops point to the browser build; the engine uses the full Chromium build, not `chrome-headless-shell`, which exits about 30 s after launch.

**Campaign build fails mid-way:**
`build_campaign()` writes an atomic checkpoint at `campaign_assets/<campaign>/build_checkpoint.json` after asset generation. Retrying the same named campaign and prompt resumes from that checkpoint without repeating the LLM generation or ComfyUI asset work; a successful build removes the checkpoint. Check `ai-engine/ai-gm.log` for which phase failed before retrying.

**Combat freezes on NPC turn:**
The LLM call is bounded by `LLM_COMBAT_TIMEOUT` (default 60s). If the LLM is slow, the NPC will fall back to a generic attack automatically. A stalled PC turn similarly times out after `PC_TURN_TIMEOUT` (default 180s) instead of blocking the encounter. If you see persistent freezes past these bounds, the setting isn't being picked up — check `ai-engine/.env`.

**Relay connection refused (relay mode only, `FOUNDRY_BACKEND=relay`):**
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
On the playwright backend, a browser drop is detected at once and reconnected in the background (2, 5, 10, 30 and 60 s backoff), and event channels are re-installed after each reconnect, so player, roll, combat and scene events resume. Check `tail -f ai-engine/ai-gm.log | grep -i "disconnect\|reconnect"`. RPC reply timeouts are governed by `relay_rpc_timeout` (default 45s) in `ai-engine/config.py`.

In relay mode, a supervisor heals dropped WebSocket connections on a ~10s interval and relaunches the headless session if the relay reports no connected client.

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
- **[Features](docs/features/overview.md)** — Deep-dives: campaign generation, combat AI, living world, lore system, action audit trail
- **[Troubleshooting](docs/troubleshooting/faq.md)** — FAQs and common issues
- **[API Reference](docs/api/rest-endpoints.md)** — REST endpoints for integrations

### For Developers & Contributors

- [Roadmap & Positioning](docs/ROADMAP.md) — What this project is, the architecture decisions behind it, and backlog status
- [Modular Architecture Split](docs/architecture-refactor.md) — Why routes live in `ai-engine/api/routes/` and `main.py` still owns `lifespan`
- [ComfyUI Setup](ai-engine/campaign/workflows/SETUP_GUIDE.md) — Image generation setup and troubleshooting

---

## Acknowledgments

The legacy relay (`relay/`, a git submodule) is forked from [ThreeHats/foundryvtt-rest-api-relay](https://github.com/ThreeHats/foundryvtt-rest-api-relay) — MIT licensed, see `relay/LICENSE`. It paired with the [foundryvtt-rest-api](https://github.com/ThreeHats/foundryvtt-rest-api) Foundry module (same author) as the WebSocket/REST bridge for the engine. The engine now drives Foundry directly, and that bridge is deprecated and will be removed. All credit for the original relay and module design goes to ThreeHats: this project's engine and its Foundry-side handling are built on the operations that module and relay defined.

---

## Recent Changes

### v1.3 (2026-10-11) — Playwright backend, relay deprecated

The engine now drives Foundry itself, through a headless Chromium logged in as the GM user. This replaces the Go relay and the `foundryvtt-rest-api` Foundry module as the default path.

- **Default transport:** `FOUNDRY_BACKEND=playwright`. The relay path stays available as `FOUNDRY_BACKEND=relay` and is deprecated; it will be removed along with the relay submodule.
- **No Foundry module required** for the default path. The operations the module provided are ported into the engine (`ai-engine/foundry/playwright_ops*.js`).
- **Reconnect:** a browser drop is reconnected in the background, and a call made while the browser is down reconnects first. A write in flight when the browser dies is never replayed.
- **Campaign start** verifies the world Foundry is serving rather than launching one, and refuses to run against a different world than the campaign's linked world.
- **Reasoning fallback:** when a model returns its JSON in the reasoning field and leaves content empty, the engine reads the reasoning field.
- **Embeddings:** a configurable fallback provider (`VAULT_EMBEDDINGS_FALLBACK_PROVIDER`) when the primary provider is unreachable.
- **Testing:** the default-backend change is verified by the full test suite and by unattended live runs on a test world.

Known gaps: dnd5e below 5 and the `ownedByUserId` search filter are not ported and raise explicit errors. The dashboard routes that assume the relay process are still in place and will be reviewed before the relay is removed.

### v1.2 (2026-10-02)

#### Cinematic art & Storyteller integration

Campaigns now generate establishing stills for social scenes (taverns, settlements, temples, courts, etc.) via ComfyUI's z-image turbo model (1280×704, ~40 s on Apple Silicon). Optional video clips use LTX-Video 2B (832×480, 4 s, ~30 s). The stills deploy as **Storyteller Cinema** backdrops with cinematic view mode enabled; the campaign prologue opens as a **StoryTeller X** book when that module is active. Enable with `CINEMA_ART_ENABLED=true` in `.env`.

#### World CLI

An optional second command surface beside the relay: the `fvtt-world-cli` daemon provides typed, validated, dry-runnable access to the open GM session. The engine's `foundry/world_cli.py` client routes reads, entity CRUD, scene activation, combat commands (initiative, end encounter), file uploads, and wall/light/sound placement through it — with the relay as automatic fallback when a write fails. One-step pairing returns the relay seed. Deploy ends with a broken-file audit. Off by default (`WORLD_CLI_WRITES_ENABLED`).

#### Player-facing features

- **`/npc` chat** — Players talk directly to any NPC (`/npc <name>: <message>`); the NPC answers from its record, goals, memory and nearby vault lore. Conversations are logged as `NPC_CONVERSED` events and announced at session start.
- **`/gm undo`** — Reverses the AI's last HP change, token move, condition or exhaustion change. Also available as `POST /api/undo` and a control-panel button.
- **Character backstory** — `POST /api/backstory` and a character-sheet button generate a lore-grounded backstory drawn from the campaign world and vault.
- **Player pacing** — Players get time to act between GM narrative beats and combat turns; token and placeable edits count as table activity so an active table isn't interrupted.

#### Visual quality

- Maps use a muted D&D palette at a 128 px grid with a detail pass; 2× hires upscale runs at 2048×1536 with tiled VAE.
- NPC portraits are framed as one head-and-shoulders subject at 512×640.
- Battlemaps use a flat top-down style that fills the frame, padding 0, with one retry on generation failure.

#### Combat

- Flanking now requires an ally roughly directly across the target (correct angle calculation).
- `start_encounter` rolls initiative before the first turn.
- Condition undo settles correctly on dnd5e 6.

#### Test coverage

83% → 97.7% line coverage; branch coverage now measured too. Coverage floor raised to 90% (enforced in CI). 313 test files, ~30 bugs found and fixed during the coverage push.

#### Security

Cinematic art file names are contained to the asset directory using the `realpath`+`startswith` pattern CodeQL recognizes.

### v1.1.1 and earlier

<details><summary>Expand prior changelog</summary>

#### Campaign-gated lifecycle

- **Deferred connection** — The relay process and Foundry WebSocket no longer start at engine boot; they come up when a campaign is built or started, so the admin panel works while the relay is down.
- **Automatic world creation removed** — You create and pair the Foundry world yourself.

#### Lore enrichment

`POST /api/campaign/enrich` adds a further source to an existing campaign — see [Campaign Generation](docs/features/campaign-generation.md#enriching-an-existing-campaign).

#### Reconnect supervisor

Self-healing supervisor reconnects a dropped socket on a ~10s interval, even while the session is idle.

#### Modular architecture

`main.py` (was 3,435 lines) is now a ~420-line lifespan/wiring module; route handlers moved into 19 focused routers under `api/routes/` (~5,170 lines total). `ModuleIntegration` hook registry covers 27 Foundry addons.

#### Combat & encounters

Compendium-backed encounters, midi-qol / DAE / AutoAnimations awareness, PC turn timeout, live Foundry Combat sync.

#### Narration

15-archetype TTS system backed by any OpenAI-compatible local TTS server, with a browser-only Web Speech API fallback.

#### Semantic lore system

Automatic entity extraction, vault-backed context injection, and query caching (150x+ faster repeats).

#### Action audit trail & unattended play

Every action logged with parameters and outcome. Constrain before dispatch, record after. See `docs/features/action-audit-trail.md`.

</details>

---

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.

The embedded relay (`relay/`) is a git submodule forked from [ThreeHats/foundryvtt-rest-api-relay](https://github.com/ThreeHats/foundryvtt-rest-api-relay) and also MIT licensed.

---

## Contributing

Contributions are welcome. Please open an issue to discuss new features or bug fixes before submitting a pull request. See `docs/ROADMAP.md` for positioning and `docs/architecture-refactor.md` for architecture notes.

---

## Support & Issues

- **Bug reports:** Open an issue with reproduction steps, logs, and your environment
- **Feature requests:** Describe the use case and how it should work
- **Troubleshooting:** See [Troubleshooting](docs/troubleshooting/faq.md) in the docs or check existing issues

---

## Citation

If you use FoundryVTT AI GM in your research or publications, please cite:

```bibtex
@software{kennedy2025aigm,
  author = {Kennedy, Chris},
  title = {FoundryVTT AI Gamemaster},
  year = {2026},
  url = {https://github.com/cjkennedy1972/foundryvtt-ai-gm},
  note = {MIT License}
}
```
