# Combat System

AI-GM can run a Foundry combat loop and propose actions for enemy turns. The implementation is an action-driven assistant, not a separate tactical game engine.

## Implemented

Combat state is read from Foundry and maintained by the combat/state modules. The combat routes and chat commands support starting combat, resolving turns, initiative, movement, attacks, spells, item use, conditions, rests, and hit-point changes where the connected Foundry system exposes the required data.

Enemy decisions are generated from the current scene and combat context. Proposed actions pass through action schemas and referee adjudication before dispatch. Successful and failed consequential actions are recorded in the audit trail.

Player actions are entered through Foundry chat. Describe the intended action, provide or complete requested rolls, and verify the resulting Foundry state. The AI may ask for a player roll rather than rolling on the player's behalf.

## Limits

The code does not expose the documented Easy/Moderate/Hard/Deadly settings, automatic party-size scaling, guaranteed morale/surrender behavior, or a general encounter builder. It does not guarantee optimized tactics, complex environmental reasoning, or a particular result. Encounters remain subject to the connected Foundry world and the configured rules data.

## Safety

Strict action schemas reject unknown fields. Damage is clamped, referee checks can reject proposals, and arbitrary `execute_js` is disabled unless explicitly enabled. Pause the AI or edit the resulting document in Foundry if a ruling needs correction.

## Roadmap

Configurable difficulty, deeper tactical planning, automatic solo-party tuning, and a dedicated encounter builder are roadmap items, not current controls.

---

Next: [Action Audit Trail](action-audit-trail.md)
