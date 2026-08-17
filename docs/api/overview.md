# API Overview

AI-GM exposes a REST API for campaign management, session control, NPC queries, and more. The API runs on `http://localhost:18080/api/` (default) and is accessible via curl, JavaScript, or any HTTP client.

## Who Should Use the API?

The API is for:

- **Tool Builders** — Creating dashboards, stat trackers, or custom tools
- **Streamers** — Querying session state, combat status, settlements
- **Developers** — Automating campaign operations (start, pause, extend campaigns)
- **GMs** — Triggering NPC actions, approving proposals, or exporting sessions

You **don't need the API** to play—FoundryVTT chat handles everything. The API is for external integrations.

## What Can You Do?

### Campaign Management

- Build campaigns (scan world, generate scenes, deploy)
- List saved campaigns
- Get campaign metadata
- Start, pause, extend, or regenerate campaigns

### Session Control

- Start/pause/resume/end sessions
- Get session status (current time, turn count)
- Trigger idle beats or export recaps
- Query settlements and world state

### Combat & Tactics

- Get combat status (turn order, HP, initiative)
- Analyze tactical situations (cover, flanking, reach)
- Check difficulty and suggest actions

### NPC & World

- List NPCs with personality and class
- Get full NPC context and relationships
- Query settlement locations by time of day
- Manage NPC relationships

### Canon Proposals

- Get pending proposals (GM directives, facts the AI wants to canonise)
- Approve or reject proposals with reasons

Canon proposals are the one thing that does wait for a human: they change the
campaign's written lore, they are reviewed between sessions rather than
mid-scene, and nothing auto-approves them.

### Scene & Atmosphere

- Switch scenes, get current scene metadata
- Play narration via TTS
- Add ambient sound, particles, effects
- Update vision, lighting, weather

## API Basics

### Authentication

If `ADMIN_TOKEN` is set in `.env`, include it in the `Authorization` header:

```bash
curl -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  http://localhost:18080/api/session/status
```

**Without a token configured**, the API is open to `localhost` only.

### Base URL

```
http://localhost:18080/api/
```

Adjust the port if you've configured a different `ADMIN_HOST`.

### Response Format

Responses are JSON. Most queries return data directly:

```json
{
  "session_id": "abc123",
  "campaign": "The Shattered Coast",
  "is_running": true,
  "current_time": "dusk",
  "turn_count": 42
}
```

### Error Handling

Errors return JSON with status and detail:

```json
{
  "status": "error",
  "detail": "Campaign not found"
}
```

Common HTTP codes:
- **400** — Bad request
- **401** — No token configured, but not localhost
- **404** — Resource not found
- **500** — Server error

## Common Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/campaign/list` | List all campaigns |
| GET | `/campaign/get/{name}` | Get campaign details |
| POST | `/campaign/start` | Start a campaign |
| GET | `/session/status` | Get active session state |
| GET | `/session/settlements` | List all settlements |
| GET | `/combat/status` | Get combat turn order |
| GET | `/npcs` | List all NPCs |
| GET | `/canon/pending` | Get canon proposals awaiting review |
| POST | `/canon/{id}/approve` | Approve a proposal |

Full endpoint reference: **[REST Endpoints](rest-endpoints.md)**

## Example: Check Session Status

```bash
curl -H "Authorization: Bearer my_admin_token" \
  http://localhost:18080/api/session/status
```

Response:

```json
{
  "session_id": "session_42",
  "campaign": "The Shattered Coast",
  "is_running": true,
  "current_time": "dusk",
  "turn_count": 12
}
```

## Security

### Token Safety

- **Never share your token** — Treat it like a password
- **Keep `.env` secret** — Never commit it to version control
- **Use HTTPS in production** — localhost is unencrypted
- **Restrict access** — Only expose to trusted networks

### Data Access

- All API responses are JSON (no HTML injection)
- Single admin token; no per-user tokens
- No webhooks or external notifications
- Every dispatched action is recorded in the audit trail (see features/action-audit-trail.md)

## Common Use Cases

### Use Case 1: Session Status Widget

Check if a session is running and what time it is:

```javascript
async function getSessionStatus() {
  const response = await fetch('http://localhost:18080/api/session/status', {
    headers: { 'Authorization': 'Bearer my_token' }
  });
  const status = await response.json();
  console.log(`Session is ${status.is_running ? 'running' : 'paused'} at ${status.current_time}`);
}
```

### Use Case 2: Settlement Query

Find where NPCs are at a specific time:

```bash
curl -H "Authorization: Bearer my_token" \
  "http://localhost:18080/api/session/settlements/redmarch?time_of_day=dusk"
```

### Use Case 3: Canon Review Dashboard

Poll pending proposals and check their status:

```bash
# Get pending proposals
curl -H "Authorization: Bearer my_token" \
  http://localhost:18080/api/canon/pending

# Approve one
curl -X POST -H "Authorization: Bearer my_token" \
  http://localhost:18080/api/canon/1/approve
```

### Use Case 4: Combat Analysis

Check flanking or cover for a target:

```bash
curl -X POST -H "Authorization: Bearer my_token" \
  -H "Content-Type: application/json" \
  -d '{"attacker_token_id": "actor_1", "defender_token_id": "actor_2"}' \
  http://localhost:18080/api/combat/tactical/analyze
```

## Limitations

The API **cannot:**
- Modify character sheets or player data (only GM-driven actions)
- Create players or add users
- Access player passwords or private session tokens
- Bypass action schema validation or the referee's rules adjudication
- Run arbitrary JavaScript unless ALLOW_EXECUTE_JS is explicitly enabled

These restrictions keep your game safe.

## Next Steps

- **Full reference**: See **[REST Endpoints](rest-endpoints.md)**
- **Have questions?** See **[FAQ](../troubleshooting/faq.md)**

---

Start with an `ADMIN_TOKEN` in `.env`, then explore **[REST Endpoints](rest-endpoints.md)** for the full API.
