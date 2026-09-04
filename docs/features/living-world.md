# Living World System

The current living-world implementation is a small, event-driven clock. It is not an autonomous simulation of a settlement.

## Implemented

`ai-engine/worldclock/agent.py` provides `WorldClockAgent`:

- `advance(session_id, duration_seconds)` appends a `TIME_ADVANCED` event.
- The clock moves through six approximate periods: dawn, morning, noon, afternoon, dusk, and night. Each period is 3,600 seconds; a full modeled day is 21,600 seconds.
- Pending NPC goals whose trigger matches the time event are marked active.
- Registered settlements are queried for scheduled NPC locations, and `NPC_MOVED` events are appended. If an NPC is mapped to a Foundry actor, the event includes its actor UUID.
- Locations can be queried with `query_location_at_time` or the session-control settlement endpoints.

Settlements are loaded from active campaign data when the chat listener starts (`ai-engine/foundry/chat_listener.py`). The production end-session path calls `advance` once with the configured duration (eight hours by default), so `/gm end session` is a single configured clock transition, not a selectable day, week, month, or year simulation.

NPC goals may also be considered after resolved actions. `NPCAgent` may act when a goal matches, but that is a reactive turn for one selected NPC, not a background population simulation.

## Not implemented

The code does not currently implement settlement growth, seasons, succession, plagues, romance arcs, world-scale events, or NPC actions while the server is offline. Relationships and consequences are not automatically simulated merely because time advances.

## Roadmap

Configurable elapsed-time controls, long-term settlement change, autonomous offline NPC activity, seasonal systems, and multi-settlement world events are product ideas, not available features.

---

Next: [Features Overview](overview.md)
