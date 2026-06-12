# AI D&D Gamemaster — Implementation Plan (Revised)

> **Project**: The Aethelwyrd Chronicles AI Gamemaster
> **Goal**: An AI that runs D&D 5e campaigns inside FoundryVTT. Players interact normally via Foundry chat. The web UI is only for GM management.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Players playing in FoundryVTT (normal gameplay)             │
│  - Chat in Foundry chat                      │
│  - See scenes, tokens, combat                │
│  - Roll dice, move tokens, use abilities     │
│  - Interact with AI-controlled NPCs          │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   │ WebSocket: AI listens for chat messages,
                   │ roll events, scene/encounter changes
                   │
┌──────────────────▼───────────────────────────────────────────┐
│  AI GM Engine (Python / FastAPI)                             │
│  - Chat listener: subscribes to Foundry chat via WebSocket   │
│  - LLM Manager: processes messages, makes GM decisions       │
│  - Action executor: performs actions in Foundry              │
│    (chat as NPC, roll dice, move tokens, update HP, music)   │
│  - Game state tracker: maintains campaign state              │
│  - Context loader: loads campaign from Obsidian vault        │
│  - Session logger: records everything to SQLite              │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   │ REST API + WebSocket
                   │
┌──────────────────▼───────────────────────────────────────────┐
│  Your Go Relay (localhost:3010) → Foundry VTT v14 + D&D 5e   │
│  Full API: actors, combat, dice, chat, scenes, audio         │
└──────────────────────────────────────────────────────────────┘

              │
              │ REST API + WebSocket
              │
┌─────────────▼───────────────────────────────────────────────┐
│  Web Admin Panel (React SPA)                                 │
│  - AI GM settings (model, temperature, system prompt)        │
│  - Campaign state / current scene                            │
│  - Campaign builder (create new campaign, import notes)      │
│  - Session log viewer                                        │
│  - NPC inventory / status viewer                             │
│  - Manual override controls                                  │
└──────────────────────────────────────────────────────────────┘
```

## How It Works During a Game

1. **Player** types in FoundryVTT chat: "I want to attack the goblin with my sword"
2. **AI Engine** (listening on WebSocket) receives the message
3. **AI** reads the context: party state, scene, combat status, NPC list
4. **AI** decides: roll attack, describe result, update NPC HP, continue narration
5. **AI** sends actions to Foundry: roll the dice, update token HP, send narration as GM/NPC
6. **Players** see the results in Foundry — exactly like a human GM running the table

**The web UI is invisible to players during the game.** GMs use it to:
- Configure AI settings before/during session
- Monitor campaign state
- Build new campaigns
- Review session logs
- Override AI decisions if needed

---

## Component Breakdown

### 1. AI Engine (`ai-engine/`)

```
ai-engine/
├── main.py                  # FastAPI server (admin API + WebSocket listeners)
├── config.py                # Settings, model selection, relay connection
├── llm/
│   ├── manager.py           # LLM calls, context management, streaming
│   ├── system_prompts.py    # GM persona, rules, tool descriptions
│   └── context.py           # Context window management, summarization
├── state/
│   ├── tracker.py           # Game state tracking (scene, combat, NPCs)
│   ├── snapshotter.py       # Save/load game state to SQLite
│   └── models.py            # Pydantic models for state
├── actions/
│   ├── dispatcher.py        # LLM action requests → Foundry execution
│   └── executors.py         # Individual action implementations
├── foundry/
│   ├── client.py            # WebSocket client for Go relay
│   ├── chat_listener.py     # Listens for player messages
│   └── actions.py           # Executes GM actions in Foundry
├── context/
│   ├── loader.py            # Loads campaign from Obsidian vault
│   └── prompts.py           # Constructs system prompt from loaded data
├── persistence/
│   ├── db.py                # SQLite connection
│   └── models.py            # SQLAlchemy/SQLModel schemas
├── skills/
│   ├── dnd5e.py             # D&D 5e rules knowledge
│   └── npc_behavior.py      # NPC AI personality definitions
├── requirements.txt
└── .env
```

### 2. Web Admin Panel (`admin-panel/`)

```
admin-panel/
├── index.html               # Single-page React app
├── src/
│   ├── components/
│   │   ├── Settings.tsx       # AI GM configuration
│   │   ├── CampaignBuilder.tsx # New campaign creation wizard
│   │   ├── SessionViewer.tsx  # Session logs, event timeline
│   │   ├── StateMonitor.tsx   # Live game state dashboard
│   │   ├── NPCManager.tsx     # View/edit NPCs from Foundry
│   │   └── OverridePanel.tsx  # Manual GM overrides
│   └── App.tsx
├── package.json
└── vite.config.ts
```

### 3. Campaign Context Loading

Loads from Obsidian vault at `~/Vaults/MyStuff/games/Dungeons_and_Dragons/`:
- **Aethelwyrd Campaign State.md** → current state, faction info
- **Act I - The Shattered Sky.md** → plot outline
- **NPCs - Act I.md** → NPC personalities, relationships
- **Worldbuilding.md** → setting knowledge
- **Character Hooks.md** → PC backstories, motivations
- **Session 1 — The Shattered Dawn.md** → session structure
- **DM_Reference.md** → GM rules, notes
- **DnD SRD_v5.2.1_Full_Text.txt** → rule reference (chunked retrieval)

---

## AI Actions

The LLM requests actions, which the engine executes in Foundry:

| Action | Description |
|--------|-------------|
| `narrate(text)` | Send narration as GM in Foundry chat |
| `speak_as(npc_name, text, whisper_to?)` | Speak as NPC in chat |
| `roll(formula, speaker, flavor)` | Roll dice in Foundry |
| `roll_combat(tokens)` | Roll initiative for encounter |
| `move_token(token_id, x, y)` | Move token on grid |
| `update_hp(uuid, damage/heal)` | Update actor HP |
| `play_sound(sound_name)` | Play sound effect |
| `play_music(playlist_name)` | Start background music |
| `switch_scene(scene_name)` | Change current scene |
| `start_encounter(token_ids)` | Begin combat |
| `end_encounter()` | End combat |
| `whisper(player, text)` | Whisper to specific player |
| `prompt_player(player, question, options?)` | Ask player for input |

---

## Development Phases

| Phase | What |
|-------|------|
| **0. Foundation** | Project skeleton, relay client, SQLite, campaign loader, basic LLM test |
| **1. Core Engine** | LLM context management, game state tracker, action dispatcher, chat loop |
| **2. Admin Panel** | React app, AI settings, campaign builder, session viewer, state monitor |
| **3. Integration** | End-to-end: Foundry chat → AI → Foundry chat/roll/HP update |
| **4. Polish** | Sound/music, combat flow, NPC behavior, error recovery |
