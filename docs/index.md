# AI-GM — Autonomous Game Master for FoundryVTT

An AI-powered Game Master that generates entire D&D 5e campaigns and runs them autonomously in FoundryVTT. No published adventures. No DM present. The AI invents the story, manages NPCs and combat, and adapts to player choices in real time.

**Key features:**
- **Campaign generation** — LLM creates full campaigns (scenes, NPCs, quests, maps, portraits)
- **Autonomous play** — Runs unattended; GM can drop in/out anytime
- **Living world** — NPCs have daily routines; settlements evolve
- **Real combat** — Tactical AI, cover/flanking, initiative, loot
- **Lore memory** — Learns your campaign via semantic vault; injected into every decision
- **Safety gates** — GM approves consequential actions (treasure, stat changes, level-ups) or auto-approves after 20s
- **Full D&D 5e rules** — Skills, DCs, conditions, spells, proficiency built-in
- **Immersion** — TTS narration, ambient sound, particles, vision/lighting

---

## Quick Links

- **[Getting Started](getting-started/installation.md)** — Install and run your first session in 5 minutes
- **[User Guide](user-guide/overview.md)** — Full how-to for playing sessions
- **[Features](features/overview.md)** — Deep-dive into each system
- **[API Reference](api/rest-endpoints.md)** — REST endpoints for integrations
- **[Troubleshooting](troubleshooting/faq.md)** — Common issues and fixes

---

## How It Works

The AI engine listens to your players in FoundryVTT and responds with narration, NPC dialogue, dice rolls, token movement, and scene changes—all driven by an LLM and a rules engine, wired into the Foundry data layer.

**No manual scene building.** Campaign generation scans your Foundry world, generates new scenes/NPCs/quests with ComfyUI-generated maps and portraits, and deploys them live.

**Unattended play.** The AI can run sessions solo while you're away. Player messages arrive via the relay WebSocket, the AI processes them, and posts responses to chat. A **world clock** advances time and triggers NPC actions. **Approval gates** queue high-impact decisions (treasure grants, level-ups) for GM review, or auto-approve after 20 seconds.

**Semantic memory.** The AI extracts entities and facts from each session, stores them in an Obsidian vault, and injects relevant lore into the LLM context before each turn—so it remembers and adapts to your world, not just this session.

---

## What's Inside

- **AI Engine** (Python/FastAPI) — LLM orchestration, action dispatch, rules, combat loop
- **Relay** (Go) — WebSocket/REST bridge to Foundry with headless-Chrome session management
- **Admin Panel** (React) — Campaign builder, session control, combat status, NPC manager
- **Foundry Integration** — Module for session start/relay pairing, rest-api bridge
- **Procedural Generators** — NPCs, quests, treasure, multi-level dungeons (via Scene Levels)
- **Module Integrations** — 25 Foundry addons auto-detected and wired (midi-qol, DAE, item-piles, etc.)

---

## Prerequisites

- **FoundryVTT v14** + D&D 5e system
- **Python 3.11–3.14**, **Node.js 24+**, **Go 1.26+**
- **Google Chrome** (for the relay's headless Foundry session)
- **LLM** — local (Qwen, LLaMA, Mistral) or remote (OpenRouter, Anthropic)
- **ComfyUI** (optional, for AI-generated maps and portraits)

---

## Next Steps

1. **[Install](getting-started/installation.md)** — 10 minutes
2. **[Run your first game](getting-started/quickstart.md)** — 5 minutes
3. **Explore** — settle into the living world

Questions? See the [FAQ](troubleshooting/faq.md).

---

*Built to be the moat of a personal/enthusiast AI-GM, not a commercial product.* 🎲
