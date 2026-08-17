# REST Endpoints Reference

Complete reference for all AI-GM API endpoints. All endpoints require authentication.

## Campaigns

### List Campaigns

```
GET /api/campaigns
```

Returns all campaigns in this installation.

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "campaign_123",
      "name": "The Riverside Chronicles",
      "tone": "gritty",
      "theme": "high fantasy",
      "created_at": "2026-06-15T10:30:00Z",
      "sessions_count": 5,
      "npc_count": 23
    }
  ]
}
```

### Get Campaign Details

```
GET /api/campaigns/{campaign_id}
```

Returns detailed information about a specific campaign.

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "campaign_123",
    "name": "The Riverside Chronicles",
    "description": "A gritty adventure in a coastal city",
    "tone": "gritty",
    "theme": "high fantasy",
    "created_at": "2026-06-15T10:30:00Z",
    "updated_at": "2026-08-16T14:22:00Z",
    "settings": {
      "difficulty": "hard",
      "death_mode": "lethal",
      "approval_mode": "balanced"
    },
    "statistics": {
      "sessions_played": 5,
      "total_playtime_hours": 18.5,
      "npcs_created": 23,
      "quests_completed": 7,
      "settlements": 3
    }
  }
}
```

### Create Campaign

```
POST /api/campaigns
```

Create a new campaign.

**Request:**
```json
{
  "name": "My Campaign",
  "description": "A fantasy adventure",
  "tone": "heroic",
  "theme": "high fantasy",
  "settings": {
    "difficulty": "moderate",
    "death_mode": "heroic"
  }
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "campaign_789",
    "name": "My Campaign",
    "created_at": "2026-08-16T14:22:00Z"
  }
}
```

## Sessions

### List Sessions

```
GET /api/campaigns/{campaign_id}/sessions
```

Returns all sessions in a campaign.

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "session_456",
      "campaign_id": "campaign_123",
      "number": 5,
      "status": "completed",
      "started_at": "2026-08-15T19:00:00Z",
      "ended_at": "2026-08-15T22:30:00Z",
      "duration_minutes": 210,
      "summary": "Defeated the bandit leader..."
    }
  ]
}
```

### Get Session Details

```
GET /api/sessions/{session_id}
```

Returns detailed information about a session.

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "session_456",
    "campaign_id": "campaign_123",
    "status": "completed",
    "date_played": "2026-08-15",
    "in_game_date": "Summer 15",
    "duration_minutes": 210,
    "npcs_encountered": ["npc_1", "npc_5", "npc_12"],
    "quests_progressed": ["quest_3", "quest_7"],
    "combat_encounters": 2,
    "major_events": [
      {
        "type": "quest_completed",
        "name": "Bandits on the Road",
        "timestamp": "2026-08-15T20:15:00Z"
      }
    ],
    "summary": "The party defeated the bandit leader and rescued the merchant."
  }
}
```

### Start Session

```
POST /api/sessions/start
```

Start a new session in a campaign.

**Request:**
```json
{
  "campaign_id": "campaign_123",
  "in_game_date": "Summer 16"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "session_id": "session_789",
    "campaign_id": "campaign_123",
    "status": "active",
    "started_at": "2026-08-16T19:00:00Z"
  }
}
```

### End Session

```
POST /api/sessions/{session_id}/end
```

End the current session.

**Request:**
```json
{
  "time_passes": "1 week",
  "notes": "Great session!"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "session_id": "session_789",
    "status": "completed",
    "ended_at": "2026-08-16T22:00:00Z"
  }
}
```

### Get Session State

```
GET /api/sessions/{session_id}/state
```

Get current state during an active session.

**Response:**
```json
{
  "success": true,
  "data": {
    "status": "active",
    "current_location": "The Red Anchor Tavern",
    "npcs_present": ["npc_1", "npc_5"],
    "time_of_day": "evening",
    "weather": "clear",
    "active_combat": false,
    "current_scene": "The party talks with Marta Crane..."
  }
}
```

## NPCs

### List NPCs

```
GET /api/campaigns/{campaign_id}/npcs
```

Returns all NPCs in a campaign.

**Query parameters:**
- `settlement_id` (optional) — Filter by settlement
- `status` (optional) — "alive", "dead", "missing"

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "npc_1",
      "name": "Marta Crane",
      "title": "Tavern Keeper",
      "settlement": "Port Redhold",
      "status": "alive",
      "personality": "adventurous",
      "relationship": "friendly"
    }
  ]
}
```

### Get NPC Details

```
GET /api/npcs/{npc_id}
```

Returns detailed information about an NPC.

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "npc_1",
    "name": "Marta Crane",
    "title": "Tavern Keeper",
    "age": 45,
    "appearance": "Weathered, one eye scarred",
    "personality": "adventurous, protective",
    "settlement": "Port Redhold",
    "background": "Former pirate, settled down 15 years ago",
    "status": "alive",
    "relationships": {
      "spouse": "npc_3",
      "niece": "npc_7"
    },
    "secrets": 1,
    "notes": "Knows where pirate treasure is buried",
    "last_seen": "2026-08-16T19:30:00Z",
    "current_location": "The Red Anchor Tavern",
    "daily_schedule": {
      "morning": "Home, preparing",
      "afternoon": "Red Anchor Tavern",
      "evening": "Red Anchor Tavern",
      "night": "Home"
    }
  }
}
```

### Get NPC Relationships

```
GET /api/npcs/{npc_id}/relationships
```

Returns an NPC's relationships with others.

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "target_npc_id": "npc_3",
      "name": "Blacksmith (spouse)",
      "relationship_type": "marriage",
      "trust_level": "high",
      "notes": "Married 12 years"
    },
    {
      "target_npc_id": "npc_7",
      "name": "Young Woman (niece)",
      "relationship_type": "family",
      "trust_level": "high",
      "notes": "Protective of her"
    }
  ]
}
```

## Quests

### List Quests

```
GET /api/campaigns/{campaign_id}/quests
```

Returns all quests in a campaign.

**Query parameters:**
- `status` (optional) — "active", "completed", "failed", "available"

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "quest_1",
      "name": "Bandits on the Road",
      "giver": "npc_5",
      "status": "completed",
      "reward": 500,
      "completed_at": "2026-08-15T20:15:00Z"
    }
  ]
}
```

### Get Quest Details

```
GET /api/quests/{quest_id}
```

Returns detailed information about a quest.

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "quest_1",
    "name": "Bandits on the Road",
    "description": "Bandits have been robbing merchants...",
    "giver_id": "npc_5",
    "giver_name": "Merchant Guard Captain",
    "status": "completed",
    "objectives": [
      "Investigate bandit activity",
      "Defeat bandit leader",
      "Return with proof"
    ],
    "progress": "completed",
    "reward": 500,
    "started_at": "2026-08-14T19:00:00Z",
    "completed_at": "2026-08-15T20:15:00Z"
  }
}
```

## Settlements

### List Settlements

```
GET /api/campaigns/{campaign_id}/settlements
```

Returns all settlements in a campaign.

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "settlement_1",
      "name": "Port Redhold",
      "type": "coastal city",
      "population": 8000,
      "leader": "Merchant Council",
      "last_visited": "2026-08-16T19:00:00Z"
    }
  ]
}
```

### Get Settlement Details

```
GET /api/settlements/{settlement_id}
```

Returns detailed information about a settlement.

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "settlement_1",
    "name": "Port Redhold",
    "type": "coastal city",
    "population": 8000,
    "leader": "Merchant Council",
    "atmosphere": "bustling trade hub",
    "districts": ["Docks", "Merchant Quarter", "Noble Estates", "Slums"],
    "key_locations": [
      {
        "name": "The Red Anchor Tavern",
        "type": "tavern",
        "keeper": "Marta Crane"
      }
    ],
    "npcs_count": 23,
    "quests_available": 4,
    "history": "Founded as a pirate haven...",
    "current_issues": ["Merchant family rivalry"]
  }
}
```

## Lore

### Query Lore

```
GET /api/campaigns/{campaign_id}/lore
```

Search campaign lore.

**Query parameters:**
- `query` (required) — Search term
- `type` (optional) — "character", "settlement", "legend", "all"

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "lore_1",
      "type": "character",
      "title": "Marta Crane",
      "content": "Former pirate, now tavern keeper...",
      "mentions": ["Port Redhold", "treasure"],
      "first_seen": "2026-06-15",
      "last_updated": "2026-08-16"
    }
  ]
}
```

### Get Lore Entry

```
GET /api/lore/{lore_id}
```

Returns detailed lore entry.

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "lore_1",
    "type": "character",
    "title": "Marta Crane",
    "content": "Former pirate turned tavern keeper...",
    "related_entries": ["lore_5", "lore_12"],
    "mentions": ["Port Redhold", "pirate treasure", "blacksmith"],
    "first_mentioned": "2026-06-15T10:30:00Z",
    "last_updated": "2026-08-16T14:22:00Z",
    "update_history": [
      {
        "date": "2026-08-16T14:22:00Z",
        "change": "Added secret about treasure location"
      }
    ]
  }
}
```

## Combat

### Get Combat State

```
GET /api/sessions/{session_id}/combat
```

Returns current combat state (if active).

**Response:**
```json
{
  "success": true,
  "data": {
    "active": true,
    "round": 3,
    "turn": 2,
    "combatants": [
      {
        "id": "player_1",
        "name": "Ranger",
        "hp": 28,
        "max_hp": 35,
        "status": "wounded"
      },
      {
        "id": "enemy_1",
        "name": "Bandit",
        "hp": 12,
        "max_hp": 15,
        "status": "wounded"
      }
    ]
  }
}
```

## Actions

### Submit Player Action

```
POST /api/sessions/{session_id}/action
```

Submit a player action during a session.

**Request:**
```json
{
  "character": "Fighter",
  "action": "Attack the nearest bandit with sword",
  "roll_result": 18
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "action_id": "action_123",
    "result": "Hit! You deal 8 damage.",
    "new_state": { ... }
  }
}
```

## Error Responses

### 401 Unauthorized

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid or missing authentication token"
  }
}
```

### 404 Not Found

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "NOT_FOUND",
    "message": "Campaign not found"
  }
}
```

### 429 Rate Limited

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "RATE_LIMITED",
    "message": "Too many requests. Limit: 100 per minute"
  }
}
```

---

**More help?** See **[API Overview](overview.md)** or check the **[FAQ](../troubleshooting/faq.md)**.
