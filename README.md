# FoundryVTT AI Gamemaster

An AI-powered D&D 5e Gamemaster integrated with FoundryVTT. Players interact directly within FoundryVTT's chat and scenes — the AI GM listens to player messages, makes narrative and mechanical decisions via LLM, and acts in Foundry (narration, NPC dialogue, dice rolls, combat management, scene changes).

The admin panel (`http://localhost:18080`) is a web dashboard for the human GM to monitor and control the AI — view session events, adjust AI settings, test responses, roll dice manually, search the SRD, and build/manage campaigns from Obsidian vault notes.

## Features

### AI Gamemaster
- **Chat-driven**: Listens to player messages in FoundryVTT chat; responds with narrative and actions
- **LLM-powered**: Configurable LLM backend (local or remote) for GM decisions and content generation
- **Action execution**: Narrates via chat, speaks as NPCs, rolls dice, manages combat, moves tokens, plays sound effects, switches scenes
- **Campaign context**: Automatically injects Obsidian vault notes (worldbuilding, NPCs, session plans, character hooks) into the LLM prompt
- **Game state tracking**: Monitors session number, mode (exploration/combat), current scene, HP, and encounter state
- **Context management**: Maintains conversation history with smart token trimming and reinforcement summarization
- **Resilient communication**: Handles relay latency and reconnection scenarios gracefully

### Admin Panel
- **Dashboard**: Real-time status (Connected/Disconnected, AI Active/Paused), stats (model, campaign, session, scene, mode), recent activity log
- **AI Settings**: Select model (Claude Sonnet 4, GPT-4o, Gemini, Llama), adjust temperature, set AI name and tone, configure relay connection
- **Session Viewer**: View game events and AI actions as they happen
- **NPC Manager**: View NPCs loaded from FoundryVTT, click to inspect details
- **GM Overrides**: Pause/resume AI, test chat responses manually, roll dice, search SRD rules

## Quick Start

### Prerequisites
- **FoundryVTT v14** with D&D 5e system installed
- **Python 3.11+**
- **Node.js 18+** (for relay frontend)
- **Go 1.26+** (builds the embedded relay; `brew install go`)
- **Google Chrome** (required for relay's headless browser)
- **LLM Service** — One of:
  - Local inference server (Qwen, LLaMA, etc.) on configurable port (default configured in `.env`)
  - Or remote LLM API (OpenRouter, Anthropic, etc.) with appropriate URL + API key
- **Obsidian vault** at `~/Vaults/MyStuff/Dungeons_and_Dragons/` (for campaign notes)
- **ComfyUI 0.24.1+** (optional, for AI-generated map and portrait images)
  - Requires checkpoint: `dDBattlemapsSDXL10_upscaleV10.safetensors`
  - Default URL: `http://localhost:18188` (configurable in `ai-engine/.env`)

The relay is no longer a separate app — its source lives in the `relay/` git
submodule, it is built by `run.sh`, and the AI Engine launches and supervises
it automatically.

### Install
```bash
git clone --recursive git@github.com:cjkennedy1972/foundryvtt-ai-gm.git
cd foundryvtt-ai-gm
chmod +x run.sh start.sh
./run.sh
```
(For an existing clone, `git submodule update --init relay` — run.sh does this too.)

### Configure
1. Edit `ai-engine/.env`:
   - `LLM_BASE_URL` — Point to your LLM service (e.g., `http://localhost:18800/v1` for local, or remote API URL). **Note:** The port depends on your LLM service configuration.
   - `LLM_API_KEY` — API key if using remote LLM (leave empty for local services)
   - `CAMPAIGN_VAULT_PATH` — Path to your Obsidian vault (default: `~/Vaults/MyStuff/Dungeons_and_Dragons`)
   - `RELAY_URL` and `RELAY_WS_URL` — Relay endpoints (auto-configured for localhost:13010)
   - `COMFYUI_BASE_URL` — ComfyUI endpoint (default: `http://localhost:18188`, only needed if generating maps)
2. Start your LLM service on the configured port before starting the campaign
3. (Optional) Start ComfyUI on the configured port if you want AI-generated maps and portraits
4. Relay API credentials are provisioned automatically on first launch

### Start

Before starting, ensure your LLM service is running on the configured port.

```bash
# Terminal 1: Start the relay + AI engine
./start.sh
```

**Service URLs:**
- **Admin Panel**: http://localhost:18080
- **Admin API**: http://localhost:18080/api  
- **Relay Dashboard**: http://localhost:3010 (or :13010 if reconfigured)
- **WebSocket**: ws://localhost:13010/ws/api

**Verify connectivity:**
```bash
# Check relay health
curl http://localhost:3010/api/health

# Test chat (once connected)
curl -X POST http://localhost:18080/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "speaker": "TestPlayer"}'
```

### Connect to FoundryVTT

The relay acts as a WebSocket bridge between FoundryVTT and the AI engine. The FoundryVTT client must be connected to the relay for the AI to receive messages.

**Setup steps:**

1. **Start the relay & AI engine** — `./start.sh`
2. **Open the relay dashboard** — http://localhost:3010
3. **Log in** using credentials from `data/relay/.secrets.env` (or create new admin)
4. **In FoundryVTT**, install the [foundryvtt-rest-api module](https://github.com/ThreeHats/foundryvtt-rest-api)
5. **Configure the module** to connect to `ws://localhost:3010/ws/api`
6. **Approve the pairing** in the relay dashboard when FoundryVTT requests access

Once connected, the AI engine will receive all chat messages and can respond via actions (narration, NPC dialogue, dice rolls, etc.).

### Setup ComfyUI (Optional — for AI-Generated Maps & Portraits)

The system can generate D&D battlemap and NPC portrait images using ComfyUI with SDXL. This is entirely optional but greatly enhances campaign immersion.

**Requirements:**
- ComfyUI 0.24.1+
- Checkpoint: `dDBattlemapsSDXL10_upscaleV10.safetensors` (download from Civitai or HuggingFace)

**Setup:**
1. Install ComfyUI: https://github.com/comfyanonymous/ComfyUI
2. Download `dDBattlemapsSDXL10_upscaleV10.safetensors` and place in `ComfyUI/models/checkpoints/`
3. Start ComfyUI: `python main.py --port 18188`
4. Verify setup: `cd ai-engine/campaign/workflows && python verify_comfyui_setup.py`
5. The AI engine will automatically generate maps and portraits during campaign building

**Configuration:**
- `COMFYUI_BASE_URL` in `ai-engine/.env` (default: `http://localhost:18188`)
- Complete workflow documentation: [ai-engine/campaign/workflows/README.md](ai-engine/campaign/workflows/README.md)
- Setup guide: [ai-engine/campaign/workflows/SETUP_GUIDE.md](ai-engine/campaign/workflows/SETUP_GUIDE.md)

**Map Generation Details:**
- Uses optimized SDXL workflow (dpmpp_3m_sde sampler + karras scheduler)
- Generation time: ~100s per map, ~80s per portrait on M-series Mac
- Maps are 1024×768, portraits are 512×768
- Automatically applied to campaign builder and custom scene generation

For detailed troubleshooting and performance tuning, see [ai-engine/campaign/workflows/QUICK_REFERENCE.md](ai-engine/campaign/workflows/QUICK_REFERENCE.md).

### Migrating from a standalone relay
If you previously ran `foundryvtt-rest-api-relay` separately and want to keep
your paired worlds, copy its `data/` contents (`relay.db`, `.secrets.env`) into
`data/relay/` before the first launch. To keep using an external relay instead,
set `RELAY_MANAGED=false` in `ai-engine/.env`.

## Admin API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Engine status (connected, ai_running, model, campaign) |
| GET | `/api/state` | Full game state (mode, scene, session, combat) |
| POST | `/api/settings` | Update AI settings (model, temperature, name, tone) |
| POST | `/api/state/update` | Manually update game state |
| POST | `/api/campaign/load` | Load a new campaign from vault files |
| GET | `/api/session/active` | Get active session info |
| POST | `/api/session/new` | Create a new session |
| GET | `/api/session/events` | Get session event history |
| GET | `/api/npcs` | List NPCs from Foundry |
| GET | `/api/srd/search` | Search SRD rules |
| POST | `/api/chat/test` | Test AI with a manual chat message |
| WS | `/admin/ws` | Real-time WebSocket for admin panel updates |

## Admin Panel Pages

### Dashboard
- Connection status badges (Foundry + AI)
- Stats: AI model, campaign, session, mode, scene, context window size
- Recent activity log

### AI Settings
- Model dropdown (Claude Sonnet 4, GPT-4o, Gemini 2.5 Pro, Llama 3.3 70B)
- Temperature slider (0-1)
- AI name for Foundry chat
- AI tone selector (5 presets + custom)
- Relay URL and API key
- Save button

### Session Viewer
- AI active/paused indicator
- Event log with timestamps
- Refresh button

### Campaign Builder
- Campaign name + description
- NPC inventory from Foundry (clickable to add)
- File selector with available Obsidian vault files
- Build and Clear buttons

### NPC Manager
- NPC search bar
- NPC list from FoundryVTT
- Click to inspect NPC details
- Refresh button

### GM Overrides
- AI pause/resume controls
- Test chat (enter player name + message → get AI response)
- Manual dice roll (formula, speaker, flavor + quick-templates)
- SRD search (type query → get rules text)

## Troubleshooting

### "AI is not responding to chat messages"
**Symptoms:** Chat messages arrive but AI doesn't respond; RPC timeouts in logs

**Solutions:**
1. Verify LLM service is running on the configured port (`curl http://localhost:8800/v1/models`)
2. Check relay is connected to FoundryVTT (`http://localhost:3010` → see connected clients)
3. Review `ai-engine/ai-gm.log` for `RPC request timed out` errors
4. Increase timeout if experiencing network latency (edit `ai-engine/foundry/client.py` line 237)

### "Connection lost after ~9 minutes"
**Symptoms:** Chat works initially, then stops responding; relay logs show "keepalive ping timeout"

**Root cause:** Relay's WebSocket keepalive mechanism closing inactive connections

**Solutions:**
1. Ensure AI engine is responding to relay keep-alive pings (should be automatic)
2. Increase RPC timeout in `ai-engine/foundry/client.py` line 237 to 60+ seconds
3. Check for network latency between AI engine and relay

### "Campaign files not loading"
**Symptoms:** Warning: "Vault path not found"

**Solutions:**
1. Verify `CAMPAIGN_VAULT_PATH` in `ai-engine/.env` matches actual vault location
2. Default path: `~/Vaults/MyStuff/Dungeons_and_Dragons/` (no "games" subdirectory)
3. Check file permissions: `ls -la ~/Vaults/MyStuff/Dungeons_and_Dragons/`

### "Relay connection refused"
**Symptoms:** `Failed to connect to relay: connection refused`

**Solutions:**
1. Verify relay is running: `curl http://localhost:3010/api/health`
2. Check relay port configuration in `.env` (default: 13010 for WebSocket)
3. Review relay logs in `/tmp/relay.log` or `data/relay/relay.log`

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│  FoundryVTT 14 (players + scenes + NPCs)         │
│  Chat, dice, tokens, combat, scenes              │
└──────────────────────┬───────────────────────────┘
                       │ WebSocket
┌──────────────────────▼───────────────────────────┐
│  Embedded Go Relay (localhost:3010)              │
│  REST API Relay with WebSocket support           │
│  Chrome headless browser for world rendering     │
└──────────────────────┬───────────────────────────┘
                       │ WebSocket + REST
┌──────────────────────▼───────────────────────────┐
│  AI Engine (Python / FastAPI / :18080)           │
│  ├─ LLM Manager (local or remote LLM)            │
│  ├─ Chat Listener (reads Foundry chat events)     │
│  ├─ Action Executor (narrate, speak, roll, etc.) │
│  ├─ State Tracker (game mode, combat, scenes)    │
│  ├─ Campaign Loader (Obsidian vault context)     │
│  └─ Persistence (SQLite session history)         │
└──────────────────────┬───────────────────────────┘
                       │ REST API + WebSocket
┌──────────────────────▼───────────────────────────┐
│  Admin Panel (static HTML / :18080/admin)        │
│  Dashboard · AI Settings · Session Viewer        │
│  Campaign Builder · NPC Manager · GM Overrides   │
└──────────────────────────────────────────────────┘
```

## Project Structure

```
foundryvtt-ai-gm/
├── ai-engine/
│   ├── .env                     # API keys + config
│   ├── .env.example             # Template
│   ├── .gitignore
│   ├── main.py                  # FastAPI server (entry point)
│   ├── config.py                # Settings (pydantic BaseSettings)
│   ├── requirements.txt
│   ├── llm/
│   │   ├── manager.py           # OpenRouter API, chat history, token trimming
│   │   └── system_prompts.py    # GM system prompt, action format, campaign context
│   ├── state/
│   │   ├── models.py            # GameState, GameMode, CombatState, Session
│   │   └── tracker.py           # In-memory + SQLite state management
│   ├── actions/
│   │   ├── executors.py         # Individual action implementations (narrate, roll, etc.)
│   │   ├── schemas.py           # Pydantic action validation schemas
│   │   └── dispatcher.py        # Routes LLM actions to executors
│   ├── foundry/
│   │   ├── client.py            # WebSocket client for Foundry relay
│   │   └── chat_listener.py     # Listens to Foundry chat, routes to AI
│   ├── context/
│   │   └── loader.py            # Loads Obsidian vault campaign notes
│   ├── persistence/
│   │   └── db.py                # SQLite: sessions, events, conversation history
│   ├── campaign/
│   │   ├── generator.py         # LLM-driven campaign generation
│   │   ├── orchestrator.py      # Campaign build pipeline orchestration
│   │   ├── map_generator.py     # SDXL map/portrait generation via ComfyUI
│   │   ├── obsidian_sync.py     # Saves campaigns to Obsidian vault
│   │   └── workflows/           # ComfyUI configuration & setup docs
│   │       ├── README.md        # Workflow directory overview
│   │       ├── SETUP_GUIDE.md   # Complete ComfyUI setup instructions
│   │       ├── QUICK_REFERENCE.md # Quick lookup & troubleshooting
│   │       ├── sdxl_battlemap_workflow.json # Workflow configuration
│   │       └── verify_comfyui_setup.py # Setup verification script
│   ├── rules/
│   │   ├── database.py          # D&D 5e rules reference (skills, DCs, conditions)
│   │   └── engine.py            # Rules evaluation engine
│   ├── admin-panel/
│   │   └── index.html           # Self-contained admin UI, served at /admin
│   └── relay_proc/
│       └── manager.py           # Embedded relay process management
├── run.sh                       # Setup + install
├── start.sh                     # Start the engine
├── PLAN.md
└── README.md
```

## Development

### Admin Panel
The admin panel is a single self-contained HTML file (`ai-engine/admin-panel/index.html`) served by the engine at `/admin` — no build step required.

### AI Engine
```bash
cd ai-engine
source venv/bin/activate
python main.py
```

### Testing a Chat Response (Manual)
```bash
curl -X POST http://localhost:18080/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"message": "I try to pick the lock on the dusty chest.", "speaker": "Selmor"}'
```

### Searching SRD
```bash
curl "http://localhost:18080/api/srd/search?query=spell+slots"
```

---

## Recent Updates & Code Changes

### June 2026 Updates
- **Bounded memory & DB growth** — Implemented LRU scene cache, deque-bounded collections for highlights/quests, rolling window conversation logs, and database retention policy to prevent unbounded storage growth (Issue #36)
- **Dependency injection pattern** — Standardized all endpoints to use injected app.state instead of bare module globals; fixed WebSocket rate limiter timing bugs (Issue #39)
- **Async method fixes** — Added missing awaits for all state_tracker async methods across chat_listener, main.py, and combat loop to ensure thread-safe atomic state mutations (Issue #47)
- **Action validation** — Comprehensive Pydantic schema validation for all LLM-produced actions with bounds on numeric values, sanitization, and extra field rejection (Issue #35)
- **Path safety & concurrency** — Fixed path traversal vulnerabilities in vault loading, added asyncio.Lock protection to shared mutable state (Issues #28, #33)
- **websockets 13.0 → 12.0** — Fixed relay connection errors due to socket incompatibility
- **RPC timeout 30s → 60s** — Improved resilience to relay latency and keepalive timeouts
- **Campaign vault path** — Corrected Obsidian vault path configuration

## License

Private project — FoundryVTT AI Gamemaster
