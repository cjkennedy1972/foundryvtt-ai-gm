# Combat

Combat is played through Foundry chat while the AI-GM is running an active session.

## Start and play

A GM can use `/gm start combat` when the connected Foundry scene contains the intended combatants. During combat, describe the player's action in chat. The AI reads the current scene/combat state, may ask the player to make a roll, and proposes supported actions for adjudication and dispatch.

Common supported actions include attacks, spells, item use, movement, hit-point changes, conditions, and encounter start/end. The exact result depends on the actor/item data in Foundry and the configured rules system.

## Verify results

Check the Foundry actor, token, and combat documents after a consequential action. Resolved actions are available with `/gm session events action_resolved`; failures are retained as well. The audit trail also appears in `ai-engine/ai-gm.log`.

## Pause or correct

Use `/gm pause ai` or the connected pause control to stop AI processing. Correct a document directly in Foundry, then use `/gm resume ai`. Arbitrary JavaScript is disabled by default.

## Limits

There are no documented difficulty presets, automatic solo balancing, or guaranteed surrender/morale rules in the current UI. The AI suggests actions; it does not replace Foundry's authoritative actor and rules data.

---

Next: [Managing Sessions](sessions.md)
