# REST API Reference

The AI-GM admin API provides endpoints for campaign management, session control, NPC queries, and more. All endpoints run on `http://localhost:18080/api/` (or your configured `ADMIN_HOST`).

## Authentication

If `ADMIN_TOKEN` is configured in `.env`, include it in the `Authorization` header:

```
Authorization: Bearer <YOUR_ADMIN_TOKEN>
```

Without a token configured, the API is open to localhost.

---

## Campaign Management

### Build a Campaign
**POST** `/api/campaign/build`

Scan a Foundry world, generate campaign structure, create assets, and deploy.

**Request:**
```json
{
  "campaign_name": "The Shattered Coast",
  "world_name": "foundry_world_id"
}
```

### List Campaigns
**GET** `/api/campaign/list`

Get all saved campaigns.

### Get Campaign
**GET** `/api/campaign/get/{campaign_name}`

Get campaign metadata and state.

### Start Campaign
**POST** `/api/campaign/start`

Launch a campaign session.

**Request:**
```json
{
  "campaign_name": "The Shattered Coast",
  "continue_from_last": false
}
```

### Extend Campaign
**POST** `/api/campaign/extend`

Generate additional scenes/quests and deploy.

### Scan World
**POST** `/api/campaign/scan`

Analyze Foundry world content.

### Regenerate Assets
**POST** `/api/campaign/regenerate-assets`

Re-run ComfyUI for maps and portraits.

### Import Campaign
**POST** `/api/campaign/import`

Import external campaign.

### Delete Campaign
**POST** `/api/campaign/delete`

Remove from database.

---

## Session Control

### Session Status
**GET** `/api/session/status`

Active session state: running, current time, turn count.

**Response:**
```json
{
  "session_id": "abc123",
  "campaign": "The Shattered Coast",
  "is_running": true,
  "current_time": "dusk",
  "turn_count": 42
}
```

### Start Session
**POST** `/api/session/new`

Create fresh session for active campaign.

### Pause AI
**POST** `/api/session/pause`

Halt message processing.

### Resume AI
**POST** `/api/session/resume`

Resume processing.

### Idle Beat
**POST** `/api/session/idle-beat`

Trigger narration during a lull.

### End Session
**POST** `/api/session/end`

Cleanly end session.

**Request:**
```json
{
  "export_recap": true
}
```

### Export Recap
**POST** `/api/session/export-recap`

Create Foundry journal with session summary.

---

## Settlements & World

### List Settlements
**GET** `/api/session/settlements`

All settlements with NPC/building count.

### Query Settlement
**GET** `/api/session/settlements/{settlement_id}?time_of_day=dusk`

NPC locations by time of day.

**Query params:**
- `time_of_day`: "dawn", "morning", "noon", "afternoon", "dusk", "night"

**Response:**
```json
{
  "settlement_id": "redmarch",
  "time_of_day": "dusk",
  "locations": {
    "tavern": ["mara", "kess"],
    "smithy": ["garrick"]
  }
}
```

### Game State
**GET** `/api/state`

Current mode, scene, turn count.

---

## Combat

### Combat Status
**GET** `/api/combat/status`

Turn order, current actor, HP, status effects.

### Combat Snapshot
**GET** `/api/combat/snapshot`

Lightweight combat state.

### Tactical Analysis
**POST** `/api/combat/tactical/analyze`

Cover, flanking, reach, suggested actions.

**Request:**
```json
{
  "attacker_token_id": "actor_uuid_1",
  "defender_token_id": "actor_uuid_2"
}
```

### Flanking Check
**POST** `/api/combat/tactical/flanking`

Is target surrounded by 2+ enemies?

### Difficulty Suggestion
**POST** `/api/combat/difficulty/suggest`

Suggest XP budget and CR for party.

---

## NPCs & Relationships

### List NPCs
**GET** `/api/npcs`

All NPCs with name, class, personality.

### Register NPC
**POST** `/api/npc/register`

Manually add NPC.

**Request:**
```json
{
  "name": "Mara",
  "actor_uuid": "foundry_id",
  "class": "rogue",
  "personality": "sharp-witted"
}
```

### NPC Context
**GET** `/api/npc/context`

Full context: personality, goals, relationships, recent actions.

### Relationships
**GET** `/api/npc/relationships`

Relationship graph (allies, enemies, rivals).

### Set Relationship
**POST** `/api/npc/relationship`

Update NPC-to-NPC relationship.

---

## Canon & Approval

### Pending Proposals
**GET** `/api/canon/pending`

GM directives and facts awaiting review.

**Response:**
```json
[
  {
    "id": 1,
    "type": "npc_event",
    "proposal": "Mara discovered Garrick's secret",
    "created_at": "2026-08-17T...",
    "expires_at": "2026-08-17T..."
  }
]
```

### Approve Proposal
**POST** `/api/canon/{proposal_id}/approve`

Canonize a fact.

### Reject Proposal
**POST** `/api/canon/{proposal_id}/reject`

Discard a proposal.

---

## Scene & World

### Current Scene
**GET** `/api/scene/current`

Scene metadata: name, tokens, background, mood.

### List Scenes
**GET** `/api/scenes/list`

All scenes in world.

### Switch Scene
**POST** `/api/scene/switch`

Teleport party to new scene.

**Request:**
```json
{
  "scene_id": "the_sunken_crypt"
}
```

---

## Immersion & Atmosphere

### Play Narration
**POST** `/api/immersion/narrate`

Speak text via TTS.

**Request:**
```json
{
  "text": "You hear distant thunder...",
  "speaker": "GM",
  "voice": "mysterious"
}
```

### Ambient Sound
**POST** `/api/immersion/atmosphere`

Play ambient loop (forest, dungeon, tavern).

### Token Effects
**POST** `/api/immersion/token-effect`

Apply visual effect to token.

### Particles
**POST** `/api/immersion/particle`

Spawn particle effect on scene.

### Vision & Lighting
**POST** `/api/immersion/vision`

Update vision range or lighting.

### Weather
**POST** `/api/immersion/weather`

Change scene weather (rain, storm, fog).

---

## Rules & Reference

### SRD Search
**GET** `/api/srd/search?q=fireball`

Search D&D 5e SRD.

### Spell Lookup
**GET** `/api/rules/spell?spell_name=Fireball`

Full spell details.

### Condition Details
**GET** `/api/rules/condition?condition=prone`

Condition effects.

### DC Reference
**GET** `/api/rules/dc`

Suggested DCs for skill checks.

---

## Settings & Status

### GM Settings
**GET** `/api/settings`

Current settings (approval mode, pacing, limits).

**POST** `/api/settings`

Update settings.

### System Status
**GET** `/api/status`

Health check: engine, relay, Foundry, models, uptime.

### Health
**GET** `/api/health`

Is engine running?

### Ready
**GET** `/api/ready`

Is engine ready to start session?

---

## Chat & Commands

### Test Chat
**POST** `/api/chat/test`

Send message to LLM, get response (no session required).

**Request:**
```json
{
  "message": "What is the capital of France?"
}
```

### GM Command
**POST** `/api/chat/gm`

Send `/gm` command (pause, resume, settlement query, etc.).

**Request:**
```json
{
  "command": "settlement list"
}
```

---

## Procedural Generation

### Multi-Level Dungeon
**POST** `/api/procedural/dungeon/multi-level`

Generate multi-floor Foundry Scene Levels dungeon.

**Request:**
```json
{
  "name": "The Sunken Crypt",
  "levels": 3,
  "size": "large"
}
```

---

## WebSocket (Real-time)

The relay provides a WebSocket at `ws://localhost:13010/ws/api` for:
- Player chat messages
- Combat turn changes
- NPC actions
- Approval proposals
- World state changes

See the Foundry Integration docs for relay setup.

---

## Error Handling

Errors return JSON with status and detail:

```json
{
  "status": "error",
  "detail": "Campaign not found"
}
```

Common errors:
- **400**: Bad request
- **401**: No token (if configured)
- **404**: Resource not found
- **500**: Server error

---

**[API Overview](overview.md)** | **[User Guide](../user-guide/overview.md)** | **[FAQ](../troubleshooting/faq.md)**
