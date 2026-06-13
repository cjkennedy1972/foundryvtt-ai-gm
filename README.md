# Sage - AI D&D Gamemaster

An AI-powered D&D 5e Gamemaster integrated with FoundryVTT. Players interact directly within FoundryVTT's chat and scenes — the AI GM listens to player messages, makes narrative and mechanical decisions via LLM, and acts in Foundry (narration, NPC dialogue, dice rolls, combat management, scene changes).

The admin panel (`http://localhost:18080`) is a web dashboard for the human GM to monitor and control the AI — view session events, adjust AI settings, test responses, roll dice manually, search the SRD, and build/manage campaigns from Obsidian vault notes.

## Architecture

```
┌──────────────────────────────────────────────────┐
│  FoundryVTT 14 (players + scenes + NPCs)         │
│  Chat, dice, tokens, combat, scenes              │
└──────────────────────┬───────────────────────────┘
                       │ WebSocket (relay)
┌──────────────────────▼───────────────────────────┐
│  Embedded Go Relay (localhost:13010)             │
│  Spawned + managed by the AI Engine              │
│  Web UI for Foundry module pairing               │
└──────────────────────┬───────────────────────────┘
                       │ WebSocket + REST
┌──────────────────────▼───────────────────────────┐
│  AI Engine (Python / FastAPI / :18080)           │
│  ├─ LLM Manager (OpenRouter / Claude Sonnet 4)   │
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

## Quick Start

### Prerequisites
- **FoundryVTT v14** with D&D 5e installed, plus the [foundryvtt-rest-api module](https://github.com/ThreeHats/foundryvtt-rest-api)
- **Python 3.11+**
- **Node.js 18+**
- **Go 1.26+** (builds the embedded relay; `brew install go`)
- **Google Chrome** (only needed for the relay's headless Foundry sessions)
- **OpenRouter API key** (get one at https://openrouter.ai/keys)
- **Obsidian vault** at `~/Vaults/MyStuff/games` (for campaign notes)

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
1. Edit `ai-engine/.env` and set your `LLM_API_KEY` / `LLM_BASE_URL`
2. That's it for the relay — `RELAY_API_KEY` is provisioned automatically on
   first launch and stored in `data/relay/aigm-credentials.json`

### Start
```bash
./start.sh
```

- **Admin Panel**: http://localhost:18080
- **API**: http://localhost:18080/api
- **Relay Dashboard**: http://localhost:13010

### Connect to FoundryVTT

**Option A — Headless Chrome (recommended, no module config needed)**

Add these to `ai-engine/.env`:
```
FOUNDRY_URL=http://localhost:30000
FOUNDRY_USERNAME=Gamemaster
FOUNDRY_PASSWORD=your-foundry-gm-password
FOUNDRY_WORLD=your-world-name   # optional
```
On startup the AI Engine launches a headless Chrome session, Chrome logs into
FoundryVTT and the relay module auto-connects. No manual pairing step.

**Option B — Manual module pairing**

1. Open http://localhost:13010 and log in with the credentials from
   `data/relay/aigm-credentials.json`
2. In FoundryVTT, set the rest-api module's relay URL to `ws://localhost:13010`
3. Approve the pairing request in the relay dashboard

### Migrating from a standalone relay
If you previously ran `foundryvtt-rest-api-relay` separately and want to keep
your paired worlds, copy its `data/` contents (`relay.db`, `.secrets.env`) into
`data/relay/` before the first launch. To keep using an external relay instead,
set `RELAY_MANAGED=false` in `ai-engine/.env`.

## Features

### AI Gamemaster
- **Chat-driven**: Listens to player messages in FoundryVTT chat
- **LLM-powered**: Uses Claude Sonnet 4 via OpenRouter for GM decisions
- **Action execution**: Narrates via chat, speaks as NPCs, rolls dice, manages combat, moves tokens, plays sound effects, switches scenes
- **Campaign context**: Automatically injects Obsidian vault notes (worldbuilding, NPCs, session plans, character hooks) into the LLM prompt
- **Game state**: Tracks session number, mode (exploration/combat), current scene, HP, and encounter state
- **Conversation history**: Maintains context window with smart token trimming

### Admin Panel
- **Dashboard**: Real-time status (Connected/Disconnected, AI Active/Paused), stats (model, campaign, session, scene, mode), recent activity log
- **AI Settings**: Select model (Claude Sonnet 4, GPT-4o, Gemini, Llama), adjust temperature, set AI name and tone, configure relay connection
- **Session Viewer**: View game events and AI actions as they happen
- **Campaign Builder**: Select Obsidian vault files to build a campaign context from scratch
- **NPC Manager**: View NPCs loaded from FoundryVTT, click to inspect details
- **GM Overrides**: Pause/resume AI, test chat responses manually, roll dice, search SRD rules

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
│   │   └── dispatcher.py        # Routes LLM actions to executors
│   ├── foundry/
│   │   ├── client.py            # WebSocket client for Foundry relay
│   │   └── chat_listener.py     # Listens to Foundry chat, routes to AI
│   ├── context/
│   │   └── loader.py            # Loads Obsidian vault campaign notes
│   ├── persistence/
│   │   └── db.py                # SQLite: sessions, events, conversation history
│   └── admin-panel/
│       └── index.html           # Self-contained admin UI, served at /admin
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

## License

Private project — Sage AI Gamemaster
