# Managing Game Sessions

A session is a single game night—from when you press "start" to when you press "end." During a session, the AI-GM runs the world, controls NPCs and enemies, and advances the story.

## Session Lifecycle

### Starting a Session

1. Open AI-GM in FoundryVTT
2. Click **Start New Session**
3. Choose a campaign (or create one first)
4. Optionally set a date/time for the in-game world
5. Click **Begin**

The world is now active. NPCs are at their scheduled locations, quests are ready, and the story awaits.

### During a Session

**What you can do:**
- Explore settlements and talk to NPCs
- Accept quests and follow story leads
- Trigger combat encounters
- Ask the GM for guidance (the AI responds)
- Make decisions that shape the world

**What AI-GM does:**
- Responds to your actions with believable reactions
- Controls enemy tactics in combat
- Maintains NPC schedules and routines
- Remembers conversations and past events
- Records every action it takes (see "Action Audit Trail" below)

### Pausing a Session

Click **Pause** to take a break without ending the session. The world freezes—NPCs stop moving, time doesn't advance. When you **Resume**, everything picks up where it left off.

Pausing is useful for:
- Taking a real-world break
- Looking up rules
- Discussing what happens next with your party
- Stepping away to grab food

### Ending a Session

Click **End Session** when the game night is over. The AI-GM:

- **Saves all progress** (character positions, NPC states, completed quests)
- **Advances in-game time** so NPCs live their lives between sessions
- **Preserves context** so the next session picks up naturally
- **Exports a summary** (optional) for your records

The world continues living between sessions—NPCs follow their routines, relationships evolve, and locations change based on what happened.

## Action Audit Trail

AI-GM is designed to run unattended, so it does not stop mid-scene to ask
permission — there may be nobody at the keyboard to answer. Instead it keeps a
record of everything it did, and mechanical changes are called out so you can
scan for them.

**Recorded prominently (hit points, conditions, resources):**
- Damage and healing (`update_hp`)
- Conditions applied and token effects
- Attacks, spells, and saving-throw items
- Death saves, exhaustion, inspiration
- Short and long rests
- Encounters starting and ending

**Where to read it:**
- In chat: `/gm session events action_resolved`
- In the log: `grep '\[Audit\]' ai-engine/ai-gm.log`

**If you want to intervene:** click **Pause**, make the change in Foundry
directly, then **Resume**. The trail tells you what to look for.

More: **[Action Audit Trail](../features/action-audit-trail.md)**

## Session History & Export

### View Past Sessions

Click **Sessions** to see a list of all games you've played. From here you can:
- See when each session happened
- Check duration and major events
- Reopen a session for review
- Export session data

### Export Session Summary

At the end of a session, click **Export Summary** to save a narrative recap:

```
Session 3: The Tavern Burned Down
Date Played: August 10, 2026
Duration: 3 hours

Major Events:
- The party discovered the arsonist was the mayor's son
- The tavern keeper fled to the woods
- The party agreed to help find him
- Combat: 2 bandits encountered, both defeated

NPCs Encountered:
- Mayor (suspicious about investigation)
- City Guard Captain (cooperative)
- Hooded Stranger (disappeared before combat)

Lore Updates:
- The mayor has a secret past with the arsonist's mother
- The forest harbors a hidden druid circle
```

This summary helps you:
- Remember what happened last session
- Track ongoing story threads
- Reference NPC relationships
- Plan future sessions

## Tips for Smooth Sessions

**Before starting:**
- Confirm the campaign is set up correctly
- Gather your party and set expectations
- Have snacks and water ready

**During play:**
- Ask the AI-GM questions about the world ("What do I see?", "Can I do X?")
- React authentically to what unfolds
- Approve or modify story events to match your vision
- Take notes on NPCs or locations you want to remember

**Between sessions:**
- Review the exported summary
- Let the world live—don't micromanage NPC actions
- Trust the AI-GM to develop the story naturally

## Troubleshooting

**"The AI-GM isn't responding to my action"**
→ Make sure you're not paused. Click Resume if needed.

**"An NPC did something unexpected"**
→ This is the living world working! NPCs have agency. If it contradicts lore, pause the AI, correct it in Foundry, and resume — the audit trail shows exactly what the AI changed.

**"I want to change something from the last session"**
→ Open the previous session, review what happened, then start a new session with that context fresh.

---

Next: Learn about **[Combat Encounters](combat.md)** or explore **[Settlements & NPCs](settlements.md)**.
