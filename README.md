# FoundryVTT AI Gamemaster

An AI-powered D&D 5e Gamemaster integrated with FoundryVTT. Players interact directly within FoundryVTT's chat and scenes — the AI GM listens to player messages, makes narrative and mechanical decisions via LLM, and acts in Foundry (narration, NPC dialogue, dice rolls, combat management, scene changes).

The admin panel (`http://localhost:18080`) is a web dashboard for the human GM to monitor and control the AI — view session events, adjust AI settings, test responses, roll dice manually, search the SRD, and build/manage campaigns from Obsidian vault notes.

## Features

### Core AI Gamemaster
- **Chat-driven**: Listens to player messages in FoundryVTT chat; responds with narrative and actions
- **LLM-powered**: Configurable LLM backend (local or remote) for GM decisions and content generation
- **Action execution**: Narrates via chat, speaks as NPCs, rolls dice, manages combat, moves tokens, plays sound effects, switches scenes
- **Campaign context**: Automatically injects Obsidian vault notes (worldbuilding, NPCs, session plans, character hooks) into the LLM prompt
- **Game state tracking**: Monitors session number, mode (exploration/combat), current scene, HP, and encounter state
- **Context management**: Maintains conversation history with smart token trimming and reinforcement summarization
- **Resilient communication**: Handles relay latency and reconnection scenarios gracefully

### Campaign Building & Management
- **Campaign Wizard**: Scan FoundryVTT world, generate campaign from Obsidian vault notes and LLM input
- **Asset Generation**: AI-generated battle maps and NPC portraits via ComfyUI (SDXL)
- **Campaign Deployment**: Deploy scenes, NPCs, journal entries, loot tables, quest logs, playlists to Foundry
- **Campaign Persistence**: Save/load campaigns with deployment state tracking via JSON

### Procedural Generation
- **Dynamic Encounters**: Generate monsters, combat encounters, treasure loot, balanced by party level
- **NPC Generation**: Create full NPC personalities with relationships, backgrounds, and dialogue
- **Quest Generation**: Generate quest hooks, objectives, and rewards from campaign context
- **Session Generation**: AI creates detailed session plans with pacing, encounters, and narrative beats
- **Party Analysis**: Analyze party composition, levels, and capabilities for balanced content

### Scene Automation & Immersion
- **Sophisticated NPC Placement**: Match NPC first-appearance fields to scenes; auto-place multiple NPCs with position spreading
- **Fog of War Configuration**: Extract vision ranges from lighting specs; set darkness regions and player vision limits
- **Hazard Visualization**: Color-coded drawing overlays for traps, spikes, poison, fire, water, obstacles
- **Ambient Sound/Music**: Auto-select mood-appropriate ambient audio and music by scene atmosphere
- **GM Macro Generation**: Auto-create initiative, perception, short/long rest, round timer, encounter start macros
- **Token Effects**: Apply visual effects to tokens (auras, particle effects, status indicators)
- **Vision & Lighting**: Configure vision distance, light sources, and fog of war per scene
- **Item Pools**: Create loot tables and item drop mechanics for merchants and treasure

### NPC Personality & Behavior
- **Personality System**: Full NPC personalities with relationships, goals, fears, and dialogue hooks
- **NPC Context Management**: Load NPC context into AI prompt for consistent behavior
- **NPC Relationships**: Track faction relationships, personality conflicts, ally/enemy status
- **Dynamic NPC Behavior**: Emergent NPC decisions based on campaign state and party relationships

### Combat & Tactical Features
- **Combat Automation**: Initialize encounters, manage initiative, roll attacks and saves
- **Difficulty Suggestions**: Suggest monster CRs and encounter balancing by party level
- **Tactical Analysis**: Analyze combat terrain, suggest positioning, flank opportunities
- **Combat State Tracking**: Track current combatants, turn order, round number, conditions

### Advanced Features
- **Rules Engine**: D&D 5e rules reference and evaluation (skills, DCs, conditions, spells)
- **Context Reinforcement**: Maintain accurate game state with smart summarization to prevent token bloat
- **Session History**: Store all game events, AI decisions, dice rolls, and chat in SQLite
- **Relay Management**: Start/stop/restart embedded Go relay; provision credentials automatically
- **ComfyUI Integration**: Health checks and model listing for image generation

### Admin Panel
- **Dashboard**: Real-time status (Connected/Disconnected, AI Active/Paused), stats (model, campaign, session, scene, mode), recent activity log
- **AI Settings**: Select model (Claude Sonnet 4, GPT-4o, Gemini, Llama), adjust temperature, set AI name and tone, configure relay connection
- **Session Viewer**: View game events and AI actions as they happen
- **NPC Manager**: View NPCs loaded from FoundryVTT, click to inspect details; manage relationships
- **Campaign Builder**: Create new campaigns, import Obsidian vault notes, generate content
- **Immersion Controls**: Configure weather, time of day, token effects, macros, ambient sound
- **GM Overrides**: Pause/resume AI, test chat responses manually, roll dice, search SRD rules
- **Procedural Tools**: Generate encounters, treasure, NPCs, quests on demand

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

### Status & State
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Engine status (connected, ai_running, model, campaign) |
| GET | `/api/relay/status` | Relay process status (running, port, uptime) |
| GET | `/api/health` | Engine health check |
| GET | `/api/state` | Full game state (mode, scene, session, combat) |
| GET | `/api/scene/current` | Current scene details |
| GET | `/api/scenes/list` | List all scenes in campaign |

### Settings & Configuration
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings` | Get current AI settings (model, temperature, name, tone) |
| POST | `/api/settings` | Update AI settings |
| POST | `/api/relay/start` | Start embedded relay |
| POST | `/api/relay/stop` | Stop relay |
| POST | `/api/relay/restart` | Restart relay |

### Campaign Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/campaign/scan` | Scan FoundryVTT world for existing content |
| POST | `/api/campaign/build` | Build new campaign (scan + generate + deploy) |
| POST | `/api/campaign/deploy` | Deploy campaign to Foundry |
| POST | `/api/campaign/regenerate-assets` | Regenerate maps/portraits and re-attach to scenes |
| GET | `/api/campaign/list` | List available campaigns |
| GET | `/api/campaign/get/{campaign_name}` | Get campaign details |
| POST | `/api/campaign/delete` | Delete a campaign |
| POST | `/api/campaign/start` | Start a campaign session |

### Session Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/session/active` | Get active session info |
| POST | `/api/session/new` | Create new session |
| POST | `/api/session/end` | End current session |
| GET | `/api/session/events` | Get session event history |

### Game State & Actions
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/state/update` | Manually update game state (scene, mode, etc) |
| POST | `/api/scene/switch` | Switch to a different scene |
| POST | `/api/roll` | Roll dice (manual override) |

### NPC Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/npcs` | List NPCs from Foundry |
| POST | `/api/npc/register` | Register/update NPC in tracking |
| POST | `/api/npc/personality` | Update NPC personality |
| GET | `/api/npc/context` | Get NPC context for AI |
| GET | `/api/npc_context` | Full NPC context data |
| POST | `/api/npc/relationship` | Update NPC relationship |
| GET | `/api/npc/relationships` | Get all NPC relationships |

### Combat
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/combat/start` | Start an encounter |
| POST | `/api/combat/stop` | End combat |
| GET | `/api/combat/status` | Get current combat state |
| POST | `/api/combat/difficulty/suggest` | Suggest encounter CR by party level |
| GET | `/api/combat/difficulty/suggestions` | Get difficulty suggestions |
| POST | `/api/combat/tactical/analyze` | Analyze combat terrain and tactics |
| POST | `/api/combat/tactical/flanking` | Check flanking opportunities |

### Procedural Generation
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/procedural/encounter` | Generate balanced encounter |
| GET | `/api/procedural/treasure` | Generate treasure/loot |
| GET | `/api/procedural/npc` | Generate NPC with personality |
| GET | `/api/procedural/party` | Generate full party |
| GET | `/api/procedural/quest` | Generate quest hook |
| GET | `/api/procedural/session` | Generate session plan |

### Immersion & Effects
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/immersion/weather` | Set weather/atmosphere |
| POST | `/api/immersion/time` | Advance game time |
| GET | `/api/immersion/atmosphere` | Get current atmosphere |
| POST | `/api/immersion/token-effect` | Apply visual effect to token |
| GET | `/api/immersion/token-effects/{token_id}` | Get token effects |
| POST | `/api/immersion/vision` | Configure vision/lighting |
| GET | `/api/immersion/vision-status` | Get vision status |
| POST | `/api/immersion/macro/register` | Create GM macro |
| POST | `/api/immersion/macro/execute` | Execute macro |
| GET | `/api/immersion/macros` | List macros |
| GET | `/api/immersion/macro-templates` | Get macro templates |
| POST | `/api/immersion/particle` | Create particle effect |
| POST | `/api/immersion/particle-preset` | Save particle preset |
| GET | `/api/immersion/particles` | List particles |
| GET | `/api/immersion/particle-presets` | List presets |
| POST | `/api/immersion/item-pool` | Create loot table |
| GET | `/api/immersion/item-pools` | List loot tables |
| GET | `/api/immersion/inventory/{actor_id}` | Get actor inventory |

### Rules & Reference
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/srd/search` | Search SRD rules text |
| GET | `/api/rules/spell` | Get spell details |
| GET | `/api/rules/spells` | List spells |
| GET | `/api/rules/condition` | Get condition details |
| GET | `/api/rules/dc` | Get difficulty class reference |
| GET | `/api/rules/reference` | Get general rules reference |

### Chat & Testing
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/test` | Test AI response with manual message |

### Context & Reinforcement
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/context/reinforcement` | Get context reinforcement info |
| POST | `/api/context/reinforce` | Trigger context refresh |
| POST | `/api/context/summarize` | Generate context summary |
| POST | `/api/context/world_summary` | Generate world summary |

### ComfyUI Integration
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/comfyui/health` | Check ComfyUI service health |
| GET | `/api/comfyui/models` | List available checkpoints |

### WebSocket
| Method | Endpoint | Description |
|--------|----------|-------------|
| WS | `/api/ws` | Real-time game events (chat, combat, scene changes) |

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

## Obsidian Vault Structure

The campaign builder expects Obsidian vault files in `$CAMPAIGN_VAULT_PATH`:

```
Dungeons_and_Dragons/
├── [Campaign Name]/
│   ├── campaign.json              # Generated campaign data (created by builder)
│   ├── Campaign State.md          # Current campaign state & factions
│   ├── Act I - Chapter 1.md       # Plot outline
│   ├── NPCs - Act I.md            # NPC personalities & relationships
│   ├── Worldbuilding.md           # Setting & lore
│   ├── Character Hooks.md         # PC backstories & motivations
│   ├── Session 1 — Opening.md    # Session structure & pacing
│   ├── DM_Reference.md            # House rules & GM notes
│   └── Maps/
│       ├── tavern.md             # Scene descriptions
│       └── dungeon.md
└── Rules/
    └── DnD_SRD_5e_Full.txt       # Optional: full SRD for rules lookups
```

**Recommended file templates:**
- `Campaign State.md`: Factions, current goals, timeline
- `NPCs - Act I.md`: Personality, quirks, hooks for each NPC
- `Worldbuilding.md`: History, geography, magic, pantheon
- `Character Hooks.md`: Ties to party members and world
- `Session N.md`: Scene descriptions, key NPCs, encounters, pacing

## Troubleshooting

### "AI is not responding to chat messages"
**Symptoms:** Chat messages arrive but AI doesn't respond; RPC timeouts in logs

**Solutions:**
1. Verify LLM service is running on the configured port:
   ```bash
   curl http://localhost:18800/v1/models  # Adjust port per your config
   ```
2. Check relay is connected to FoundryVTT: `http://localhost:3010` → click "Clients"
3. Review logs for errors:
   ```bash
   tail -f ai-engine/ai-gm.log | grep -i "error\|timeout"
   ```
4. Verify FoundryVTT module is installed and connected:
   - FoundryVTT Settings → Module Management → Install "foundryvtt-rest-api"
   - Configure module to connect to `ws://localhost:3010/ws/api`
5. Check firewall/network:
   ```bash
   curl http://localhost:18080/api/status  # Should return {"connected": true}
   ```

### "Connection lost after ~9 minutes"
**Symptoms:** Chat works initially, then stops responding; logs show WebSocket closed

**Root cause:** Relay WebSocket keepalive or inactivity timeout

**Solutions:**
1. Verify relay is still running: `curl http://localhost:3010/api/health`
2. Check AI engine logs for disconnect:
   ```bash
   tail -f ai-engine/ai-gm.log | grep -i "disconnect\|close"
   ```
3. Increase RPC timeout in `ai-engine/foundry/client.py`:
   ```python
   REQUEST_TIMEOUT = 60  # was 30, increase if experiencing latency
   ```
4. Verify relay is configured correctly:
   ```bash
   cat ai-engine/.env | grep RELAY
   ```

### "Campaign files not loading"
**Symptoms:** Warning: "Vault path not found" or "campaign.json not found"

**Solutions:**
1. Verify vault path in `.env`:
   ```bash
   cat ai-engine/.env | grep CAMPAIGN_VAULT_PATH
   ls -la ~/Vaults/MyStuff/Dungeons_and_Dragons/  # Check actual path
   ```
2. **Default path is `~/Vaults/MyStuff/Dungeons_and_Dragons/`** (no "games" subdirectory)
3. Check file permissions:
   ```bash
   chmod 755 ~/Vaults/MyStuff/Dungeons_and_Dragons/
   ```
4. Verify campaign structure:
   ```bash
   ls -la ~/Vaults/MyStuff/Dungeons_and_Dragons/[CampaignName]/
   cat ~/Vaults/MyStuff/Dungeons_and_Dragons/[CampaignName]/campaign.json
   ```

### "Relay connection refused"
**Symptoms:** `Failed to connect to relay: connection refused` or port already in use

**Solutions:**
1. Check relay is running:
   ```bash
   curl http://localhost:3010/api/health
   ```
2. If not running, start it:
   ```bash
   ./start.sh  # Starts relay + AI engine
   ```
3. Check port configuration in `.env`:
   ```bash
   cat ai-engine/.env | grep RELAY
   ```
4. If port is in use, find the process:
   ```bash
   lsof -i :3010  # Check port 3010
   kill -9 <PID>  # Force kill if needed
   ```
5. Review relay logs:
   ```bash
   tail -f data/relay/relay.log
   ```

### "ComfyUI health check fails"
**Symptoms:** `ComfyUI is not reachable` when building campaign

**Solutions:**
1. Start ComfyUI on the configured port:
   ```bash
   python main.py --port 18188  # In ComfyUI directory
   ```
2. Verify it's running:
   ```bash
   curl http://localhost:18188/api/health
   ```
3. Check your `.env` has correct URL:
   ```bash
   cat ai-engine/.env | grep COMFYUI
   ```
4. Verify checkpoint is installed:
   ```bash
   ls ComfyUI/models/checkpoints/ | grep dDBattlemapsSDXL
   ```

### "Maps/portraits fail to upload"
**Symptoms:** Images generated but not attached to scenes/actors

**Solutions:**
1. Check asset files exist:
   ```bash
   ls -la ai-engine/campaign_assets/[campaign_name]_maps/
   ```
2. Verify relay upload endpoint is working:
   ```bash
   curl http://localhost:3010/api/health
   ```
3. Check for permission errors in Foundry:
   - Verify scene/actor ownership in FoundryVTT
   - Check Foundry user has edit permissions
4. Review detailed logs:
   ```bash
   tail -f ai-engine/ai-gm.log | grep -i "upload\|attach"
   ```

### "Admin panel shows 'Not Connected'"
**Symptoms:** Dashboard shows Foundry/Relay disconnected despite services running

**Solutions:**
1. Check WebSocket connection:
   ```bash
   curl http://localhost:18080/api/relay/status
   ```
2. Verify admin panel has correct relay URL:
   - Open `http://localhost:18080/admin`
   - Click Settings → check Relay URL is `http://localhost:3010`
3. Check firewall allows WebSocket on relay port:
   ```bash
   nc -zv localhost 3010
   ```
4. Check browser console for WebSocket errors (F12 → Console tab)

---

## Advanced Features Guide

### Procedural Generation System
The procedural generation system can create balanced, contextual content on demand.

**Generate Encounters:**
```bash
curl "http://localhost:18080/api/procedural/encounter?party_level=5&party_size=4"
```
Returns monsters balanced for the party, with suggested CR and tactics.

**Generate NPCs:**
```bash
curl "http://localhost:18080/api/procedural/npc?campaign=MyStuff&background=tavern_keeper"
```
Returns full NPC with personality, quirks, goals, and dialogue hooks.

**Generate Quests:**
```bash
curl "http://localhost:18080/api/procedural/quest?campaign=MyStuff&theme=rescue"
```
Returns quest hook with objectives, rewards, and hooks to party.

**Generate Treasure:**
```bash
curl "http://localhost:18080/api/procedural/treasure?party_level=5"
```
Returns balanced loot appropriate for party level.

### Scene Automation
When deploying a campaign, scenes are automatically enhanced with:
- **NPC Placement**: NPCs from `first_appearance` field are placed in matching scenes
- **Fog of War**: Vision distance extracted from scene lighting and applied to players
- **Hazard Visualization**: Traps and hazards displayed as color-coded overlay zones
- **Ambient Sound**: Scene `atmosphere` field auto-selects matching ambient audio
- **GM Macros**: Initiative, Perception, Short Rest, Long Rest (+ combat macros for encounters)

Scene data structure in campaign JSON should include:
```json
{
  "name": "Tavern",
  "lighting": {
    "type": "dim",
    "sources": ["torch (20ft), chandelier (40ft)"]
  },
  "atmosphere": "bustling tavern",
  "hazards": [
    {"name": "weak floor", "type": "obstacle", "x": 300, "y": 400}
  ],
  "encounter": {
    "has_encounter": false,
    "enemies": []
  }
}
```

### NPC Personality System
NPCs have full personality trees: goals, fears, flaws, bonds, ideals, and relationships.

**Update NPC Personality:**
```bash
curl -X POST http://localhost:18080/api/npc/personality \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Grok the Barbarian",
    "personality": {
      "goals": "Find lost tribe",
      "fears": "Betrayal by friends",
      "flaws": "Aggressive when drunk",
      "bonds": "Loyal to party",
      "ideals": "Strength and honor"
    }
  }'
```

**Track NPC Relationships:**
```bash
curl -X POST http://localhost:18080/api/npc/relationship \
  -H "Content-Type: application/json" \
  -d '{
    "npc_a": "Grok",
    "npc_b": "Elara",
    "relationship": "rivals",
    "history": "competed for chief role"
  }'
```

### Combat Automation
Combat handles initiative, turn order, actions, and tactical positioning.

**Start Encounter:**
```bash
curl -X POST http://localhost:18080/api/combat/start \
  -H "Content-Type: application/json" \
  -d '{
    "scene_name": "Goblin Lair",
    "enemy_tokens": ["goblin_1", "goblin_2", "goblin_boss"],
    "party_level": 5
  }'
```

**Get Difficulty Suggestions:**
```bash
curl "http://localhost:18080/api/combat/difficulty/suggestions?party_level=5&party_size=4"
```
Returns CR suggestions (Easy/Medium/Hard/Deadly) for balanced encounters.

**Analyze Terrain:**
```bash
curl -X POST http://localhost:18080/api/combat/tactical/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "scene_name": "Goblin Lair",
    "combatants": ["grok", "elara", "goblin_1", "goblin_2"]
  }'
```
Suggests positioning and flanking opportunities.

### Immersion Features

**Set Weather/Atmosphere:**
```bash
curl -X POST http://localhost:18080/api/immersion/weather \
  -H "Content-Type: application/json" \
  -d '{"condition": "heavy rain", "severity": "thunderstorm"}'
```

**Apply Token Effects:**
```bash
curl -X POST http://localhost:18080/api/immersion/token-effect \
  -H "Content-Type: application/json" \
  -d '{
    "token_id": "elara",
    "effect": "fire_aura",
    "color": "#ff6600",
    "radius": 20,
    "scale": 1.5
  }'
```

**Create Particle Effects:**
```bash
curl -X POST http://localhost:18080/api/immersion/particle \
  -H "Content-Type: application/json" \
  -d '{
    "type": "spell_cast",
    "position": {"x": 400, "y": 300},
    "color": "#0066ff"
  }'
```

**Save/Load Particle Presets:**
```bash
curl -X POST http://localhost:18080/api/immersion/particle-preset \
  -H "Content-Type: application/json" \
  -d '{"name": "fireball", "type": "explosion", "color": "#ff3300"}'
```

### Context Reinforcement
The AI maintains conversation history within token limits using smart summarization.

**Get Context Info:**
```bash
curl "http://localhost:18080/api/context/reinforcement"
```

**Trigger Reinforcement:**
```bash
curl -X POST http://localhost:18080/api/context/reinforce
```
Summarizes old context and refreshes the conversation window.

**Get World Summary:**
```bash
curl -X POST http://localhost:18080/api/context/world_summary
```
Generates a concise summary of the campaign world state.

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
│   ├── main.py                  # FastAPI server (entry point, 2300+ lines, 50+ endpoints)
│   ├── config.py                # Settings (pydantic BaseSettings)
│   ├── requirements.txt
│   │
│   ├── llm/
│   │   ├── manager.py           # OpenRouter API, chat history, token trimming
│   │   └── system_prompts.py    # GM system prompt, action format, campaign context
│   │
│   ├── state/
│   │   ├── models.py            # GameState, GameMode, CombatState, Session, Event
│   │   └── tracker.py           # In-memory + SQLite state management
│   │
│   ├── actions/
│   │   ├── executors.py         # Action implementations (narrate, roll, move token, etc.)
│   │   ├── schemas.py           # Pydantic action validation schemas
│   │   └── dispatcher.py        # Routes LLM actions to executors
│   │
│   ├── foundry/
│   │   ├── client.py            # WebSocket client for Foundry relay
│   │   └── chat_listener.py     # Listens to Foundry chat, routes to AI
│   │
│   ├── context/
│   │   ├── loader.py            # Loads Obsidian vault campaign notes
│   │   ├── window_manager.py    # Context window management & token counting
│   │   └── reinforcement_manager.py # Smart summarization & context pruning
│   │
│   ├── persistence/
│   │   └── db.py                # SQLite: sessions, events, conversation history
│   │
│   ├── campaign/
│   │   ├── generator.py         # LLM-driven campaign generation
│   │   ├── orchestrator.py      # Campaign build pipeline (scan → generate → deploy)
│   │   ├── map_generator.py     # SDXL map/portrait generation via ComfyUI
│   │   ├── obsidian_sync.py     # Saves campaigns to Obsidian vault
│   │   └── workflows/           # ComfyUI configuration & setup docs
│   │       ├── README.md, SETUP_GUIDE.md, QUICK_REFERENCE.md
│   │       ├── sdxl_battlemap_workflow.json
│   │       └── verify_comfyui_setup.py
│   │
│   ├── scene/
│   │   └── awareness.py         # Scene automation (NPC placement, hazards, FOW, sound, macros)
│   │
│   ├── combat/
│   │   ├── difficulty.py        # Encounter balancing by CR and party level
│   │   ├── loop.py              # Combat turn automation
│   │   └── mechanics.py         # D&D 5e combat rules (flanking, cover, etc.)
│   │
│   ├── npc/
│   │   ├── personality.py       # NPC personality system (traits, goals, relationships)
│   │   └── registry.py          # NPC tracking and relationship management
│   │
│   ├── procedural/
│   │   ├── encounters.py        # Generate balanced encounters
│   │   ├── generator.py         # Procedural content framework
│   │   ├── npcs.py              # Generate NPCs with personality
│   │   ├── quests.py            # Generate quest hooks
│   │   └── treasures.py         # Generate loot and rewards
│   │
│   ├── immersion/
│   │   ├── ambient.py           # Ambient sound and music integration
│   │   ├── effects.py           # Visual/particle effects on tokens
│   │   ├── items.py             # Item pools and loot tables
│   │   ├── macros.py            # GM macro generation and execution
│   │   ├── particles.py         # Particle effect presets
│   │   └── vision.py            # Vision/lighting and fog of war
│   │
│   ├── rules/
│   │   ├── database.py          # D&D 5e rules reference (skills, DCs, conditions, spells)
│   │   └── engine.py            # Rules evaluation engine
│   │
│   ├── admin-panel/             # React SPA served at /admin (see admin-panel/)
│   │   └── index.html           # Self-contained admin UI
│   │
│   ├── relay_proc/
│   │   └── manager.py           # Embedded relay process management
│   │
│   └── utils/
│       ├── path_safety.py       # Path traversal protection
│       └── token_counter.py     # LLM token counting
│
├── admin-panel/                 # React single-page app (TS/TSX)
│   ├── src/
│   │   ├── components/          # React components (Settings, CampaignBuilder, etc.)
│   │   └── main.jsx
│   ├── package.json
│   └── vite.config.ts
│
├── relay/                       # Git submodule: foundryvtt-rest-api-relay (Go)
│   └── (Compiled by run.sh, launched as embedded process)
│
├── data/                        # Runtime data
│   ├── relay/                   # Relay database & credentials
│   └── foundryvtt-ai-gm.db      # Session history & game state
│
├── run.sh                       # Setup + install (git submodule, venv, deps)
├── start.sh                     # Start relay + AI engine
├── PLAN.md                      # High-level architecture & development phases
└── README.md                    # This file
```

**Key Modules by Feature:**
- **Campaign**: orchestrator.py, generator.py, map_generator.py, obsidian_sync.py
- **State Tracking**: state/tracker.py, persistence/db.py, state/models.py
- **Combat**: combat/difficulty.py, combat/loop.py, combat/mechanics.py, procedural/encounters.py
- **Procedural**: procedural/*.py (encounters, NPCs, quests, treasures, sessions)
- **Immersion**: immersion/*.py (sound, effects, items, macros, particles, vision)
- **NPC**: npc/personality.py, npc/registry.py, procedural/npcs.py
- **Scene Automation**: scene/awareness.py
- **Rules**: rules/database.py, rules/engine.py

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

#### Asset Regeneration Bug Fixes (PR #60)
Fixed 9 critical bugs identified in comprehensive code review:
- **NPC error handler double-fault** — Secondary KeyError masked original exception when NPC dict lacked 'name' field
- **Silent failure on scene timeout** — Network errors from relay returned None, silently skipping error recording and counter increment
- **Level data destruction** — Hardcoded single 'Base Level' wipes all custom Foundry levels (Perfect Vision, Levels module); now preserves existing levels
- **Percent-encoded map paths** — Map uploads not URL-decoded like portraits, causing broken Foundry image URLs
- **Missing API field** — `portraits_attached` never returned to clients in regenerate-assets response
- **Stale UUID validation** — Enhanced error handling when cached UUID from deployment_state updates wrong actor
- **Triple get_actors() call** — Result from Strategy 4 discarded then called again for logging; now cached and reused
- **Sequential uploads** — All uploads sequential (13+ round-trips); refactored to use asyncio.gather with Semaphore(4) for ~4x parallelism
- **Loop imports** — `from urllib.parse import unquote` inside per-NPC loop; moved to module level

#### Earlier Updates
- **Bounded memory & DB growth** — Implemented LRU scene cache, deque-bounded collections for highlights/quests, rolling window conversation logs, and database retention policy to prevent unbounded storage growth (Issue #36)
- **Dependency injection pattern** — Standardized all endpoints to use injected app.state instead of bare module globals; fixed WebSocket rate limiter timing bugs (Issue #39)
- **Async method fixes** — Added missing awaits for all state_tracker async methods across chat_listener, main.py, and combat loop to ensure thread-safe atomic state mutations (Issue #47)
- **Action validation** — Comprehensive Pydantic schema validation for all LLM-produced actions with bounds on numeric values, sanitization, and extra field rejection (Issue #35)
- **Path safety & concurrency** — Fixed path traversal vulnerabilities in vault loading, added asyncio.Lock protection to shared mutable state (Issues #28, #33)
- **websockets 13.0 → 12.0** — Fixed relay connection errors due to socket incompatibility
- **RPC timeout 30s → 60s** — Improved resilience to relay latency and keepalive timeouts
- **Campaign vault path** — Corrected Obsidian vault path configuration

---

## Tips for Best Results

### Campaign Building
1. **Rich vault notes** — The more detail in Obsidian vault files, the more consistent the AI GM. Include:
   - NPC personalities with goals/fears/quirks
   - Faction relationships and politics
   - Plot hooks tied to party members
   - Session pacing notes (combat-heavy, roleplay-heavy, exploration, etc.)

2. **Campaign structure** — Organize by act/chapter:
   - Act I - Chapter 1.md (plot for first session)
   - Act I - Chapter 2.md (plot for second session)
   - Each chapter lists key scenes, NPCs appearing, and encounter suggestions

3. **NPC definitions** — For each important NPC, document:
   - Physical description
   - Personality traits, flaws, goals
   - Relationships to other NPCs and party
   - Dialogue style and catchphrases
   - Secret motivations or hidden agendas

### AI Configuration
1. **Model selection** — Different models have different strengths:
   - **Claude Sonnet 4.6** — Best narrative quality, excellent roleplay
   - **GPT-4o** — Good at tactical decisions and rule application
   - **Gemini 2.5 Pro** — Fast responses, good for real-time chat
   - **Llama 3.3 70B** — Local option if running on dedicated GPU

2. **Temperature tuning:**
   - **0.5** — Conservative, predictable, good for rules-heavy combat
   - **0.7** (default) — Balanced narrative and consistency
   - **0.9** — Creative surprises, more personality variation

3. **System prompt customization** — Edit `ai-engine/llm/system_prompts.py` to:
   - Adjust tone (serious, comedic, dark, etc.)
   - Add setting-specific rules or custom mechanics
   - Emphasize certain NPC personalities
   - Tune difficulty or player assistance level

### Game Session Tips
1. **Session start** — Use admin panel to:
   - Select the campaign and session
   - Verify AI name and tone
   - Review current scene and party status
   - Test a chat message before players arrive

2. **During session** — Monitor via admin panel:
   - Watch event log for AI actions and responses
   - Pause AI if you need to override a decision
   - Use Manual Roll for critical moments
   - Check NPC context if AI seems out of character

3. **Session management:**
   - Use `/reset` in Foundry chat to clear combat or reset scene state
   - Use manual NPC personality updates if AI needs course correction
   - Save session events regularly via the Session Viewer
   - Review session log after play to improve next session

---

## Quick Start Cheat Sheet

**First time setup (5 minutes):**
```bash
git clone --recursive git@github.com:cjkennedy1972/foundryvtt-ai-gm.git
cd foundryvtt-ai-gm
chmod +x run.sh start.sh
./run.sh              # Install dependencies
```

**Pre-session (2 minutes):**
```bash
# Terminal 1: Start engine
./start.sh

# Terminal 2: Open admin panel
open http://localhost:18080/admin
```

**In FoundryVTT:**
1. Install "foundryvtt-rest-api" module
2. Configure to connect to `ws://localhost:3010/ws/api`
3. Approve pairing in relay dashboard (http://localhost:3010)

**Build a campaign:**
1. Create Obsidian vault files in `~/Vaults/MyStuff/Dungeons_and_Dragons/`
2. Admin Panel → Campaign Builder → Scan World → Build Campaign
3. Select vault files to use as context
4. Click "Build" (takes 1-2 minutes)

**Start playing:**
1. Admin Panel → Campaign Builder → Start Session
2. Select campaign and session
3. Players begin chatting in Foundry
4. AI responds and acts automatically

---

## License

Private project — FoundryVTT AI Gamemaster
