# Aethelwyrd AI Gamemaster

An AI-powered D&D 5e Gamemaster integrated with FoundryVTT. Players interact directly within FoundryVTT's chat and scenes — the AI GM listens to player messages, makes narrative and mechanical decisions via LLM, and acts in Foundry (narration, NPC dialogue, dice rolls, combat management, scene changes).

The admin panel (`http://localhost:8000`) is a web dashboard for the human GM to monitor and control the AI — view session events, adjust AI settings, test responses, roll dice manually, search the SRD, and build/manage campaigns from Obsidian vault notes.

## Architecture

```
┌──────────────────────────────────────────────────┐
│  FoundryVTT 14 (players + scenes + NPCs)         │
│  Chat, dice, tokens, combat, scenes              │
└──────────────────────┬───────────────────────────┘
                       │ WebSocket (relay)
┌──────────────────────▼───────────────────────────┐
│  Go Relay (localhost:3010) → Foundry VTT Bridge  │
└──────────────────────┬───────────────────────────┘
                       │ WebSocket + REST
┌──────────────────────▼───────────────────────────┐
│  AI Engine (Python / FastAPI / :8000)            │
│  ├─ LLM Manager (OpenRouter / Claude Sonnet 4)   │
│  ├─ Chat Listener (reads Foundry chat events)     │
│  ├─ Action Executor (narrate, speak, roll, etc.) │
│  ├─ State Tracker (game mode, combat, scenes)    │
│  ├─ Campaign Loader (Obsidian vault context)     │
│  └─ Persistence (SQLite session history)         │
└──────────────────────┬───────────────────────────┘
                       │ REST API + WebSocket
┌──────────────────────▼───────────────────────────┐
│  Admin Panel (React SPA / :3000 or :8000/admin)  │
│  Dashboard · AI Settings · Session Viewer        │
│  Campaign Builder · NPC Manager · GM Overrides   │
└──────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites
- **FoundryVTT v14** with D&D 5e installed
- **Go Relay** running at `localhost:3010` (your existing `go-relay` project)
- **Python 3.11+**
- **Node.js 18+**
- **OpenRouter API key** (get one at https://openrouter.ai/keys)
- **Obsidian vault** at `~/Vaults/MyStuff/games` (for campaign notes)

### Install
```bash
cd /Users/ckennedy/Projects/foundryvtt-ai-gm
chmod +x run.sh start.sh
./run.sh
```

### Configure
1. Edit `ai-engine/.env` and set your `OPENROUTER_API_KEY`
2. Verify `RELAY_URL` points to your Go relay (`http://localhost:3010`)
3. Verify `RELAY_WS_URL` points to the WebSocket endpoint (`ws://localhost:3010/ws/api`)

### Start
```bash
./start.sh
```

- **Admin Panel**: http://localhost:8000
- **API**: http://localhost:8000/api

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
│   └── admin-panel/             # React SPA (Vite)
│       ├── index.html
│       ├── package.json
│       ├── vite.config.js
│       └── src/
│           ├── main.jsx
│           ├── App.jsx
│           ├── index.css
│           ├── store.js         # Zustand state management
│           └── pages/
│               ├── Dashboard.jsx
│               ├── Settings.jsx
│               ├── SessionViewer.jsx
│               ├── CampaignBuilder.jsx
│               ├── NPCManager.jsx
│               └── Overrides.jsx
├── run.sh                       # Setup + install
├── start.sh                     # Start the engine
├── PLAN.md
└── README.md
```

## Development

### Admin Panel (HMR)
```bash
cd ai-engine/admin-panel
npm run dev
# Runs on localhost:3000, proxies /api to localhost:8000
```

### AI Engine
```bash
cd ai-engine
source venv/bin/activate
python main.py
```

### Testing a Chat Response (Manual)
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"message": "I try to pick the lock on the dusty chest.", "speaker": "Selmor"}'
```

### Searching SRD
```bash
curl "http://localhost:8000/api/srd/search?query=spell+slots"
```

## License

Private project — The Aethelwyrd Chronicles
