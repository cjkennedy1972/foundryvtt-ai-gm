# Features Overview

This page describes the behavior currently present in the repository. Product ideas are explicitly marked as roadmap items.

## Implemented systems

### Campaign generation

The campaign API and admin panel build campaign data, store it in the vault, and deploy it to a manually created and paired Foundry world. See [Campaign Generation](campaign-generation.md).

### Sessions

The API can create and inspect sessions. Foundry chat commands can start, pause, resume, replay, inspect events, and end a session. Ending a session writes a recap to a Foundry journal and campaign vault when those integrations are available.

### Combat and actions

Chat and API flows can propose and dispatch supported Foundry actions. Schemas, referee checks, damage limits, and the pause control constrain execution. See [Combat](combat.md) and [Action Audit Trail](action-audit-trail.md).

### Living-world clock

The clock appends time events, activates matching NPC goals, and records scheduled settlement locations. The end-session path advances the configured duration (eight hours by default). See [Living World](living-world.md).

### Vault retrieval

Campaign files can be indexed and retrieved using semantic search. The extractor is heuristic and does not resolve identity or coreference. See [Lore System](lore-system.md).

## Not promises

The repository does not currently provide unattended world simulation, guaranteed canon consistency, a human approval queue, automatic Foundry-world provisioning, configurable combat difficulty, or every control described in older documentation.

## Roadmap

Offline NPC activity, richer world evolution, entity resolution, contradiction handling, solo encounter tuning, and broader authoring controls require future implementation and documentation updates.

---

Start with the [User Guide](../user-guide/overview.md).
