# AI-GM User Guide

AI-GM is an AI assistant for a self-hosted FoundryVTT world. The human operator remains the Foundry GM and player; the AI acts only through the connected runtime and supported actions.

## Start here

1. Follow the world-pairing setup in [Quick Start](../getting-started/quickstart.md).
2. Build or import a campaign in the admin panel.
3. Open **Campaign Start**, deploy the campaign, and start a session.
4. Use Foundry chat to play. See [Managing Sessions](sessions.md).

AI-GM does not automatically create or provision a Foundry world.

## Current capabilities

- Campaign build/import, vault storage, and deployment to a paired world.
- Session start, pause, resume, event replay, recap export, and end-session clock advancement.
- AI-assisted narration and supported Foundry actions, with schema/referee checks and audit events.
- Scheduled settlement location tracking and reactive NPC goals.
- Semantic retrieval over indexed campaign files.

These systems have limits. The living world is not an offline population simulator, semantic retrieval does not resolve entity identity, and combat does not expose the older documented difficulty presets or automatic solo balancing.

## Guides

- [Managing Sessions](sessions.md)
- [Combat](combat.md)
- [Settlements and NPCs](settlements.md)
- [Provisioning the AI-GM Seat](ai-gm-setup.md)
- [Campaign Generation](../features/campaign-generation.md)
- [Action Audit Trail](../features/action-audit-trail.md)

---

For API details, see the API documentation where available.
