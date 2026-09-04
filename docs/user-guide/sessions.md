# Managing Game Sessions

Sessions are persisted in the AI-GM SQLite database and are available only when the engine is connected to a paired Foundry world.

## Start a session

1. Create and pair the Foundry world first; AI-GM does not provision worlds.
2. Open the AI-GM admin panel and open **Campaign Start**.
3. Select a campaign. If it has not been deployed, use **Deploy**.
4. Use **Start** (or **Resume** for an existing session).

The equivalent Foundry chat command for a GM is `/gm start session [campaign name]`. If no name is supplied, the configured default campaign is used. A session must be active before normal player chat is processed.

## During a session

Players describe actions in Foundry chat. The AI can respond with narration, request player rolls, and propose supported Foundry actions. The connected world remains the source of truth for actors, tokens, items, and combat.

## Pause and resume

A GM can send `/gm pause ai` and `/gm resume ai`, or use the corresponding connected control. Pausing stops AI processing for normal player messages; it does not create a separate time simulation.

## End a session

Send `/gm end session` from a GM-authorized Foundry chat identity. The end-session flow generates a recap, writes it to a Foundry journal and campaign vault when possible, closes the active session even if recap export fails, and advances the world clock by the configured duration (eight hours by default). It does not offer a day/week/month/year selector.

The admin panel also provides an **End** action for the active campaign.

## Review events

Use `/gm session replay [limit]` to inspect recent events, or `/gm session events action_resolved` to inspect resolved actions. The API provides `GET /api/session/events` for the active session. Consequential action lines are also written to `ai-engine/ai-gm.log`.

## Troubleshooting

- **No response:** confirm a session is active and AI is not paused.
- **World not found:** create/pair the Foundry world, then deploy or start the campaign again.
- **Incorrect result:** pause AI, correct the actor/document in Foundry, and resume.
- **No recap:** the session still closes; inspect the engine log and verify vault/Foundry connectivity.

---

Next: [Combat](combat.md) · [Settlements and NPCs](settlements.md)
