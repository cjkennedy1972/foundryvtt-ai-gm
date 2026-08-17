# AI-GM — Autonomous Game Master for FoundryVTT

An AI-powered Game Master system that generates campaigns, runs combat, manages NPCs, and keeps your story flowing — whether you're playing solo, in a group, or anywhere in between.

## What is AI-GM?

AI-GM is a **fully autonomous Game Master** integrated with FoundryVTT. It:

- 🎲 **Generates complete campaigns** from scratch (scenes, NPCs, quests, settlements)
- 🗣️ **Runs the table** — narrates, describes, makes decisions, advances time
- ⚔️ **Manages combat** — tactical positioning, NPC turns, encounter difficulty scaling
- 🏘️ **Builds living worlds** — procedural settlements with NPCs on daily schedules
- 📖 **Preserves lore** — semantic vault system remembers your campaign and injects context
- 🎭 **Stays in character** — individual NPC personalities and voices
- ⏸️ **Respects the table** — GM approval gates for consequential actions; can run attended or unattended

The system is **local-first** (runs on your machine), **open-source**, and designed specifically for **autonomy** — it gets out of the way and lets your table play.

## Quick Start

### System Requirements

- **FoundryVTT** v14+ (local or remote)
- **Python 3.11+**
- **8GB RAM** minimum (16GB recommended)
- **macOS** (primary target), **Linux**, or **Windows** (WSL2)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/cjkennedy1972/foundryvtt-ai-gm.git
   cd foundryvtt-ai-gm
   ```

2. Install dependencies:
   ```bash
   cd ai-engine
   pip install -r requirements.txt
   ```

3. Configure your LLM:
   ```bash
   cp .env.example .env
   # Edit .env with your LLM settings (local, OpenAI, or Ollama)
   ```

4. Start the AI-GM:
   ```bash
   python main.py
   ```

5. Open FoundryVTT and create a campaign — the AI-GM handles the rest.

**[→ Full Installation Guide](./getting-started/installation.md)**

## Features

### Campaign Generation
- Procedural world generation (settlements, NPCs, quests)
- Scene-level automation (descriptions, ambient effects)
- Item and treasure generation
- Compatible with any D&D 5e rules set

**[→ Campaign Features](./features/campaign-generation.md)**

### Combat System
- Full turn management and initiative
- NPC AI with tactical positioning
- Flanking, cover, and reach calculations
- Difficulty scaling and encounter balancing
- Real-time token positioning via Foundry

**[→ Combat Features](./features/combat.md)**

### Living World
- Settlement generation with buildings and NPCs
- Daily NPC schedules (who's where and when)
- Time-of-day awareness and world progression
- Event-driven world state updates

**[→ Living World Features](./features/living-world.md)**

### Semantic Lore System
- Automatic vault indexing of campaign lore
- Context-aware lore injection in narration
- Consistency preservation across sessions
- Query interface for GM inspection

**[→ Lore System](./features/lore-system.md)**

### Safety & Control
- Approval gates for consequential actions (stat changes, item grants, level-ups)
- Attended mode (GM present) vs. unattended mode (autonomous)
- Full action logging for audit and replay
- Optional integration with existing campaigns

**[→ Safety & Control](./features/approval-workflow.md)**

## Architecture

AI-GM is built on a **multi-tier, event-sourced architecture**:

- **Event Store** — All world state changes are immutable events
- **Agents** — Specialized AI subsystems (NPC behavior, combat, world clock)
- **Semantic Indexing** — Campaign lore indexed for context injection
- **Relay** — WebSocket bridge to FoundryVTT
- **Admin Panel** — In-Foundry control surface for pause, resume, settlement queries

**[→ Full Architecture](./architecture/overview.md)**

## Getting Help

- **[User Guide](./user-guide/overview.md)** — How to use all features
- **[API Reference](./api/rest-endpoints.md)** — REST API endpoints
- **[Troubleshooting](./troubleshooting/faq.md)** — Common issues and solutions
- **[Development](./development/setup.md)** — Contributing and extending

## What's Not Included

AI-GM is deliberately focused on **generative autonomy**. It does NOT:

- Import or run published adventures (you provide your own world)
- Support cloud deployment (local-first by design)
- Integrate with external AI providers beyond LLM APIs
- Provide a web UI for character creation (use Foundry's native system)

## License

Open source — see LICENSE for details.

## Status

✅ **Production Ready** — The system is fully implemented and tested. It's ready for real-world campaigns with real players.

**Latest Version**: 1.0.0 (August 2026)

---

**[Start with Installation →](./getting-started/installation.md)**

**[Explore Features →](./features/overview.md)**

**[Read the Full Guide →](./user-guide/overview.md)**
