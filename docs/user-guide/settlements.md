# Settlements and NPCs

Campaign data may contain settlements and NPCs. The current runtime uses settlement schedules for location queries and NPC goals for reactive behavior.

## Inspect a settlement

With an active campaign loaded, a GM can use `/gm settlement list` in Foundry chat. The session-control API also exposes settlement listing and scheduled-location queries. A missing or unloaded settlement returns no locations rather than inventing a result.

The clock recognizes six periods: dawn, morning, noon, afternoon, dusk, and night. A scheduled NPC location is recorded as an `NPC_MOVED` event when the clock advances.

## Interact with NPCs

Send a natural-language player message in an active session. The AI receives available campaign/NPC context and may respond or dispatch a supported action. NPC memory, personality, and goals exist as runtime structures, but they do not guarantee that every NPC has a complete schedule, relationship graph, secret, or autonomous off-screen activity.

## Consequences and correction

Resolved actions and time/location changes are persisted as events. If a result contradicts the campaign, pause the AI and correct the relevant Foundry or campaign data directly. Do not assume that a prose response has become canon unless it is represented in persisted campaign/event data.

## Limits

The code does not currently simulate settlement growth, gossip propagation, reputation benefits, shops, temples, quest economies, or NPC activity while offline. Those are roadmap concepts.

---

Next: [Managing Sessions](sessions.md)
