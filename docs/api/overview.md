# API Overview

AI-GM exposes a REST API that lets you integrate with external tools, dashboards, and systems. Whether you want to build a companion app, create custom analytics, or automate workflows, the API provides programmatic access to your campaign.

## Who Should Use the API?

The API is for:

- **Tool Builders** — Creating companion dashboards or tools
- **Streamers** — Building custom overlays and alerts
- **Developers** — Automating campaigns or running analytics
- **GMs** — Building custom integrations with other tools

You **don't need the API** to play—the FoundryVTT interface handles everything. But if you want to extend AI-GM, the API is there.

## What Can You Do?

### Query Campaign Data

- Get campaign information (name, settings, NPCs)
- Fetch NPC details and relationships
- Query quest status and progress
- Search lore and history
- View session records
- Access combat statistics

### Manage Sessions

- Start, pause, resume, and end sessions
- Get current session state
- View turn-by-turn combat history
- Export session summaries
- Query ongoing events

### Interact with the World

- Talk to NPCs programmatically
- Submit actions during sessions
- Get descriptions of locations
- Query NPC schedules and availability
- Check settlement status

### Build Custom Tools

- Create dashboards showing campaign progress
- Build companion apps for players
- Generate analytics and reports
- Export data for external systems
- Build automatic backups

## API Basics

### Authentication

All API requests require authentication:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:18080/api/campaigns
```

Get your token from **Settings > API > Generate Token**.

### Base URL

```
http://localhost:18080/api/
```

Default port is 18080. If you've configured a different port, use that instead.

### Response Format

All responses are JSON:

```json
{
  "success": true,
  "data": { ... },
  "error": null
}
```

### Error Handling

Failed requests return error information:

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

## Common Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/campaigns` | List all campaigns |
| GET | `/campaigns/{id}` | Get campaign details |
| GET | `/campaigns/{id}/npcs` | List NPCs in a campaign |
| GET | `/campaigns/{id}/npcs/{npc_id}` | Get NPC details |
| GET | `/campaigns/{id}/sessions` | List sessions |
| GET | `/campaigns/{id}/sessions/{session_id}` | Get session details |
| GET | `/campaigns/{id}/lore` | Query lore |
| POST | `/sessions/{session_id}/action` | Submit player action |
| GET | `/sessions/{session_id}/state` | Get current state |

Full documentation is in **[REST Endpoints](rest-endpoints.md)**.

## Example: Get Campaign Info

```bash
curl -H "Authorization: Bearer token_123" \
  http://localhost:18080/api/campaigns/campaign_456
```

Response:

```json
{
  "success": true,
  "data": {
    "id": "campaign_456",
    "name": "The Riverside Chronicles",
    "tone": "gritty",
    "theme": "high fantasy",
    "created_at": "2026-06-15T10:30:00Z",
    "updated_at": "2026-08-16T14:22:00Z",
    "sessions_count": 5,
    "npc_count": 23,
    "settlement_count": 3
  },
  "error": null
}
```

## Rate Limiting

API requests are rate-limited to prevent abuse:

- **100 requests per minute** per token
- **1000 requests per hour** per token

If you exceed limits, requests return `429 Too Many Requests`.

## Webhooks

You can subscribe to campaign events. AI-GM will POST notifications to your endpoint:

**Supported events:**
- `session.started` — Session started
- `session.ended` — Session ended
- `combat.started` — Combat encounter started
- `npc.died` — NPC died
- `quest.completed` — Quest completed
- `settlement.changed` — Settlement changed

### Setting Up Webhooks

In **Settings > API > Webhooks**, add your endpoint:

```
https://your-app.com/ai-gm-webhooks
```

AI-GM will POST events to this URL:

```json
{
  "event": "session.started",
  "campaign_id": "campaign_456",
  "session_id": "session_123",
  "timestamp": "2026-08-16T14:22:00Z",
  "data": { ... }
}
```

## Rate Limiting for Webhooks

Webhooks are delivered best-effort. If your endpoint is slow or unavailable:
- Retries happen 3 times over 1 hour
- After 3 failures, the webhook is disabled
- Check **Settings > API > Webhooks** to re-enable

## Common Use Cases

### Use Case 1: Campaign Dashboard

Build a dashboard showing:
- Current session status
- Active NPCs and their locations
- Recent quests
- Campaign statistics

```javascript
// Fetch campaign data every 10 seconds
setInterval(async () => {
  const response = await fetch('/api/campaigns/my-campaign');
  const data = await response.json();
  updateDashboard(data.data);
}, 10000);
```

### Use Case 2: NPC Directory

Create a searchable directory of all NPCs:

```javascript
const npcs = await fetch('/api/campaigns/my-campaign/npcs').json();
displayNPCs(npcs.data);
```

### Use Case 3: Session Analytics

Track statistics across sessions:

```javascript
const sessions = await fetch('/api/campaigns/my-campaign/sessions').json();
const stats = analyzeSession(sessions.data);
displayAnalytics(stats);
```

### Use Case 4: Custom Bot

A bot that watches for specific events (quest completions, deaths) and posts summaries to Discord or Slack:

```javascript
// Configure webhook to your bot
// Bot receives events and processes them
function handleWebhook(event) {
  if (event.event === 'quest.completed') {
    postToDiscord(`Quest completed: ${event.data.quest_name}`);
  }
}
```

## Security

### Token Safety

- **Never share your token** — It's like a password
- **Rotate tokens regularly** — In Settings > API
- **Use HTTPS** — Only for production systems
- **Restrict endpoints** — IP allowlist if possible

### Data Privacy

- All API responses are JSON (no HTML injection possible)
- Authenticated requests only
- Rate limiting prevents brute force
- No sensitive data in logs

## Errors & Debugging

### Common Errors

| Code | Meaning | Fix |
|------|---------|-----|
| `UNAUTHORIZED` | Bad or missing token | Check your token in settings |
| `NOT_FOUND` | Campaign/NPC not found | Verify the ID is correct |
| `INVALID_REQUEST` | Malformed request | Check request format |
| `RATE_LIMITED` | Too many requests | Wait before retrying |

### Debugging

Enable debug logging in **Settings > API > Debug Logging** to see:
- Request details
- Response data
- Latency information
- Errors and warnings

## SDK Libraries

If you're building tools, use a library for easier integration:

**JavaScript/TypeScript:**
```javascript
import { AIGMClient } from '@ai-gm/client';
const client = new AIGMClient('your_token');
const campaign = await client.campaigns.get('campaign_id');
```

**Python:**
```python
from ai_gm import Client
client = Client('your_token')
campaign = client.campaigns.get('campaign_id')
```

## Limitations

The API **cannot:**
- Directly control character actions (submit actions through intended endpoints)
- Access player passwords or session tokens
- Bypass approval workflows
- Modify campaign lore directly (use intended endpoints)
- Run arbitrary code in FoundryVTT

These restrictions keep your game safe and prevent abuse.

## Next Steps

- **Full reference**: See **[REST Endpoints](rest-endpoints.md)**
- **Want to build?** Check out **[SDK documentation](https://github.com/ai-gm/sdk-docs)**
- **Have questions?** See **[FAQ](../troubleshooting/faq.md)**

---

**Getting started?** Generate your token in **Settings > API**, then explore **[REST Endpoints](rest-endpoints.md)**.
