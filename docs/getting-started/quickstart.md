# Quickstart — Your First Session in 5 Minutes

You've installed AI-GM. Now let's run your first game.

## Prerequisite

You have completed [Installation](./installation.md) and:
- ✅ AI-GM is running (`python3 main.py`)
- ✅ FoundryVTT is open
- ✅ Relay URL is configured

## Step 1: Load a Campaign

1. In FoundryVTT, click **Create World** or select an existing D&D 5e world
2. Click **Start Session**
3. Wait 5-10 seconds for the AI-GM to initialize

You'll see:
```
[INFO] Campaign loaded: My Campaign
[INFO] World initialized
[INFO] Ready for player input
```

## Step 2: Meet Your First NPCs

The AI-GM auto-generates a settlement. You'll see a narration like:

> *You find yourselves in Redmarch, a bustling trade town on the crossroads. The smell of fresh bread drifts from the Wandering Star tavern. Behind the bar, you notice Mara, a halfling with sharp eyes and a sharper wit.*

The world is already alive — NPCs have schedules, daily routines, and their own goals.

## Step 3: Play Normally

Just **chat as you would with any GM**. Click the chat bar and type:

> I approach the bar and order an ale. I ask Mara about local rumors.

The AI-GM will:
1. Narrate Mara's response
2. Describe the tavern atmosphere
3. Offer hooks for adventure
4. Remember what happened for later

## Step 4: Let the World Live

After each turn, the world advances:
- NPCs move through their daily schedules
- Time progresses (morning → afternoon → evening)
- Consequences of your actions ripple through the settlement

You can query the world anytime:
- **Session Control Panel** → **Settlements** → Click a settlement to see who's where and when

## Common Commands

### Player Chat
Just type naturally. The GM understands context and roleplay.

```
I cast Fireball on the goblin.
I want to negotiate with the merchant.
I sit by the fire and tell a story about my past.
```

### GM Commands (Chat)

Type `/gm` followed by a command:

```
/gm pause           # Pause the session
/gm resume          # Resume the session
/gm end session     # End session (saves recap to Foundry journal)
/gm settlements     # List settlements and NPCs
```

### Admin Panel (Sidebar)
- **Session Status** — Is the GM running?
- **Pause/Resume** — Control the session
- **Idle Beat** — Nudge the story if it stalls
- **Settlements** — Browse NPCs and their locations

## What Happens Automatically

You don't need to do anything for these — they just work:

| What | Who Does It | Example |
|------|------------|---------|
| **Narration** | AI-GM | Describes scenes, NPC dialogue, consequences |
| **Combat** | AI-GM | Manages NPC turns, positioning, tactics |
| **NPC Actions** | AI-GM | NPCs move on schedules, pursue goals |
| **Time** | AI-GM | World clock advances, seasons change |
| **Lore** | AI-GM | Remembers everything that happened |
| **Consequences** | AI-GM | Your choices change the world |

## What Needs Approval (Consequential Actions)

Some actions require GM approval before taking effect:

- **Granting/removing items** (treasure, magic items)
- **Changing player stats** (ability scores, skill proficiencies)
- **Level-ups** (character advancement)
- **Status effects** (conditions, curses)

**How it works**:
1. AI proposes the action
2. You have **20 seconds** to approve or reject via API, or
3. Action auto-approves and proceeds

Most games, you won't notice this — the 20-second timeout is generous, and the AI usually makes good calls.

## Troubleshooting

### "The GM isn't responding"
- Check that `python3 main.py` is still running in your terminal
- Check network connectivity between FoundryVTT and the relay
- Look at logs: `tail -f ai-gm.log`

### "Actions are stalling"
- If an action is queued for approval, the AI pauses
- Approve or reject via the API, or wait 20 seconds for auto-approval
- Check Session Control Panel for pending approvals

### "My world doesn't feel connected"
- The semantic lore system needs at least 2-3 sessions to index enough context
- After your first session, lore injection kicks in strongly
- Try querying settlements to see what the AI has learned about your world

## Tips for Great Sessions

1. **Lean into roleplay** — The AI thrives on character dialogue and descriptions
2. **Make choices** — Consequential decisions drive better stories
3. **Let it surprise you** — The world evolves in directions you won't predict
4. **Use settlements** — Querying NPCs and locations hooks great storylines
5. **Run attended or unattended** — The approval gates work either way

## Next Steps

- **[Full User Guide](../user-guide/overview.md)** — Learn all features in detail
- **[Combat Guide](../features/combat.md)** — Master the tactical system
- **[Living World Guide](../features/living-world.md)** — Understand NPC schedules and settlements
- **[API Reference](../api/rest-endpoints.md)** — Integrate with external tools

## Ready to Play?

You have everything you need. Start your campaign, type in the chat, and watch the world come alive.

**Let the story begin.** 🎲
