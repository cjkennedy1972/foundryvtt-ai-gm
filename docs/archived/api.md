# Admin API Reference

Base URL: `http://localhost:18080`

---

## Status & State

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Engine status (connected, ai_running, model, campaign) |
| GET | `/api/relay/status` | Relay process status (running, port, uptime) |
| GET | `/api/health` | Engine health check |
| GET | `/api/state` | Full game state (mode, scene, session, combat) |
| GET | `/api/scene/current` | Current scene details |
| GET | `/api/scenes/list` | List all scenes in campaign |

## Settings & Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings` | Get current AI settings (model, temperature, name, tone) |
| POST | `/api/settings` | Update AI settings |
| POST | `/api/relay/start` | Start embedded relay |
| POST | `/api/relay/stop` | Stop relay |
| POST | `/api/relay/restart` | Restart relay |

## Campaign Management

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

## Session Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/session/active` | Get active session info |
| POST | `/api/session/new` | Create new session |
| POST | `/api/session/end` | End current session |
| GET | `/api/session/events` | Get session event history |

## Game State & Actions

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/state/update` | Manually update game state (scene, mode, etc.) |
| POST | `/api/scene/switch` | Switch to a different scene |
| POST | `/api/roll` | Roll dice (manual override) |

## NPC Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/npcs` | List NPCs from Foundry |
| POST | `/api/npc/register` | Register/update NPC in tracking |
| POST | `/api/npc/personality` | Update NPC personality |
| GET | `/api/npc/context` | Get NPC context for AI |
| GET | `/api/npc_context` | Full NPC context data |
| POST | `/api/npc/relationship` | Update NPC relationship |
| GET | `/api/npc/relationships` | Get all NPC relationships |

## Combat

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/combat/start` | Start an encounter |
| POST | `/api/combat/stop` | End combat |
| GET | `/api/combat/status` | Get current combat state |
| GET | `/api/combat/snapshot` | Get pre-combat state snapshot (for rollback) |
| POST | `/api/combat/difficulty/suggest` | Suggest encounter CR by party level |
| GET | `/api/combat/difficulty/suggestions` | Get difficulty suggestions |
| POST | `/api/combat/tactical/analyze` | Analyze combat terrain and tactics |
| POST | `/api/combat/tactical/flanking` | Check flanking opportunities |

## Procedural Generation

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/procedural/encounter` | Generate balanced encounter and deploy to Foundry |
| GET | `/api/procedural/treasure` | Generate treasure and create Foundry journal entry |
| GET | `/api/procedural/npc` | Generate NPC, create Foundry actor, place token |
| GET | `/api/procedural/party` | Generate full party |
| GET | `/api/procedural/quest` | Generate quest and create Foundry journal entry |
| GET | `/api/procedural/session` | Generate session plan |

## Immersion & Effects

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

## Rules & Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/srd/search` | Search SRD rules text |
| GET | `/api/rules/spell` | Get spell details |
| GET | `/api/rules/spells` | List spells |
| GET | `/api/rules/condition` | Get condition details |
| GET | `/api/rules/dc` | Get difficulty class reference |
| GET | `/api/rules/reference` | Get general rules reference |

## Chat & Testing

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/test` | Test AI response with manual message |

## Context & Reinforcement

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/context/reinforcement` | Get context reinforcement info |
| POST | `/api/context/reinforce` | Trigger context refresh |
| POST | `/api/context/summarize` | Generate context summary |
| POST | `/api/context/world_summary` | Generate world summary |

## ComfyUI Integration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/comfyui/health` | Check ComfyUI service health |
| GET | `/api/comfyui/models` | List available checkpoints |

## WebSocket

| Method | Endpoint | Description |
|--------|----------|-------------|
| WS | `/api/ws` | Real-time game events (chat, combat, scene changes) |

---

## Example Requests

**Test a chat response:**
```bash
curl -X POST http://localhost:18080/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"message": "I try to pick the lock on the dusty chest.", "speaker": "Selmor"}'
```

**Generate an encounter (deploys monsters to Foundry):**
```bash
curl "http://localhost:18080/api/procedural/encounter?party_level=5&party_size=4"
```

**Start combat:**
```bash
curl -X POST http://localhost:18080/api/combat/start \
  -H "Content-Type: application/json" \
  -d '{"scene_name": "Goblin Lair", "enemy_tokens": ["goblin_1", "goblin_2"], "party_level": 5}'
```

**Get pre-combat snapshot (for rollback):**
```bash
curl http://localhost:18080/api/combat/snapshot
```

**Update NPC personality:**
```bash
curl -X POST http://localhost:18080/api/npc/personality \
  -H "Content-Type: application/json" \
  -d '{"name": "Grok", "personality": {"goals": "Find lost tribe", "fears": "Betrayal"}}'
```

**Set weather:**
```bash
curl -X POST http://localhost:18080/api/immersion/weather \
  -H "Content-Type: application/json" \
  -d '{"condition": "heavy rain", "severity": "thunderstorm"}'
```

**Search SRD:**
```bash
curl "http://localhost:18080/api/srd/search?query=spell+slots"
```
