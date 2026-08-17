# AI-GM Features Overview

AI-GM is built on several core systems that work together to create immersive, dynamic campaigns. This overview explains what's possible and where to dig deeper.

## Core Systems

### Campaign Generation

AI-GM can create an entire campaign from scratch—or you can build one piece by piece.

**What it creates:**
- Settlements (towns, cities, villages) with distinct personalities
- NPCs with names, backstories, and relationships
- Quests that branch based on player choices
- Lore that ties everything together
- Maps and locations within settlements

**You control:**
- Campaign theme (high fantasy, noir, modern, horror, etc.)
- Tone (heroic, gritty, whimsical, dark)
- Scope (a small town or an entire kingdom)

More: **[Campaign Generation](campaign-generation.md)**

### Living World

Settlements aren't static. The world lives between sessions.

**What happens:**
- NPCs follow daily schedules and routines
- Relationships evolve based on past interactions
- Time advances (days, seasons, years)
- Consequences accumulate (a favored NPC becomes powerful, a town you burned rebuilds)
- The world changes without your involvement

**You control:**
- How much time passes between sessions
- What story consequences matter most
- Major events and direction

More: **[Living World](living-world.md)**

### Combat System

Combat is tactical and intelligent. Enemies think, strategize, and adapt.

**What you get:**
- Intelligent enemy AI that uses tactics
- Real-time decision-making (not pre-scripted)
- Dynamic difficulty that adjusts to your party
- Environmental hazards and interesting battlefields
- Meaningful consequences for victory and defeat

**You control:**
- Combat difficulty and tone
- Whether death is permanent
- Pacing and narrative weight

More: **[Combat System](combat.md)**

### Semantic Lore System

AI-GM remembers everything about your campaign using advanced AI technology.

**What it tracks:**
- Character backstories and relationships
- Settlement history and secrets
- Quest chains and consequences
- NPC motivations and conflicts
- World mythology and legends
- Current state of everything

**You control:**
- What lore matters to the story
- Revelations and secrets
- How lore connects to new events

More: **[Lore System](lore-system.md)**

### Safety & Approval Workflow

You maintain creative control through built-in safety gates.

**What it prevents:**
- Unexpected character deaths (unless you want them)
- Story moments that contradict your vision
- Unwanted narrative outcomes
- Tone whiplash or genre shifts

**How it works:**
- AI-GM pauses before major story events
- You approve, modify, or reject what happens
- Ensures surprises match your preferences

More: **[Approval Workflow](approval-workflow.md)**

## Feature Matrix

Here's what AI-GM can do across different scenarios:

| Feature | Campaign Gen | Living World | Combat | Lore | Safety |
|---------|:-----------:|:---:|:---:|:---:|:---:|
| **Create settlements** | ✓ | ✓ | - | ✓ | - |
| **Generate NPCs** | ✓ | ✓ | ✓ | ✓ | - |
| **Run combat** | - | - | ✓ | - | ✓ |
| **Track lore** | ✓ | ✓ | - | ✓ | - |
| **Time advance** | - | ✓ | - | - | - |
| **Approve events** | - | ✓ | ✓ | ✓ | ✓ |

## How They Work Together

Imagine a typical campaign:

1. **Generate** — You create a campaign with AI-GM creating settlements, NPCs, and starting quests
2. **Play** — You explore, fight, and make choices; AI-GM runs the world and uses lore to stay coherent
3. **Live** — Time passes between sessions; NPCs live their lives, relationships change, consequences accumulate
4. **Approve** — Major story moments pause for your approval, keeping narrative control
5. **Remember** — The lore system remembers all context, so new sessions feel continuous

This cycle repeats, creating a campaign that feels alive and responsive.

## Customization & Control

AI-GM isn't one-size-fits-all. You customize:

- **Campaign tone** — Serious, comedic, dark, heroic, etc.
- **Difficulty** — Lethal, challenging, moderate, easy
- **Scope** — Intimate (one village) or epic (multi-realm)
- **Content** — What themes and content you want
- **NPC behavior** — How much agency NPCs have
- **Story weight** — How much player choices matter

Most of these can change during the campaign. Don't like how combat is scaling? Adjust difficulty. Want more emphasis on roleplay? Tell the AI-GM.

## Integration

AI-GM works inside FoundryVTT and can integrate with:

- **D&D 5e rules** (or other systems)
- **Custom character classes** and rules
- **Imported maps and assets**
- **External tools** via REST API (see [API Overview](../api/overview.md))

If you're building a custom tool or dashboard, the API gives you access to:
- Campaign data
- NPC information
- Lore queries
- Session history
- Combat details

More: **[API Overview](../api/overview.md)**

## What Makes AI-GM Different

Most D&D tools are:
- **Static** — GM writes everything ahead of time
- **Scripted** — Events happen in a fixed order
- **Separate** — NPCs and combat are disconnected

AI-GM is:
- **Dynamic** — The world responds to your choices
- **Intelligent** — NPCs think and act autonomously
- **Coherent** — Everything connects through shared lore

This creates campaigns that feel alive, surprising, and genuinely cooperative between human creativity and AI intelligence.

## Next Steps

- **Want to play?** Start with **[User Guide](../user-guide/overview.md)**
- **Want to create?** Read **[Campaign Generation](campaign-generation.md)**
- **Want details?** Explore individual features listed above
- **Want to integrate?** See **[API Overview](../api/overview.md)**

---

**Questions?** Check the **[FAQ](../troubleshooting/faq.md)** or dive into a specific feature.
