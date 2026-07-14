# AI-GM — Positioning & Roadmap

Strategic direction for the FoundryVTT AI-GM, written after a capability review
against **Familiar** (`familiar-vtt`, a commercial AI co-pilot module for Foundry).
Assumption: this is a personal/enthusiast autonomous-GM build (single local
deployment, local model, self-hosted Foundry), **not** a commercial product. If
that changes, see "If this becomes a product" below — the priorities flip.

## What we are

An **autonomous, generative AI Game Master**. We invent the campaign (LLM-generated
scenes/NPCs/quests, ComfyUI maps/portraits, Obsidian vault lore) and then *run* it
largely unattended (proactive chat listener, session-start openings, idle turns,
combat loop, scene automation, immersion managers).

## What Familiar is (and why it's a different product)

A **co-pilot** installed as a native Foundry module. 194 tools / 24 domains, an
in-Foundry chat window, and an MCP server for external AI clients. It automates and
assists a human GM running a **published adventure you import** — explicitly: *"it
does not invent the story."* Great at automation and distribution; deliberately not
generative or autonomous.

## The moat

Generation + autonomy. Familiar has chosen not to build this; it's the harder,
defensible product. **Do not chase feature parity on Familiar's co-pilot automation
surface** (deep rules enforcement, 23 providers, cloud image APIs) — that space is
commoditizing. Invest in campaign-generation quality and autonomous-GM behavior.

## Architecture decision: the external stack is load-bearing — keep it, make it boring

The engine + relay + headless-Chrome stack is *why* we can do what Familiar can't: a
local LLM orchestrator, Obsidian vault, ComfyUI pipeline, and long-context
reinforcement cannot live inside Foundry's browser runtime. **Do not rewrite as a
native module.** Familiar wins on install simplicity; we win by making our richer
stack install-once and boringly reliable — which is the direction of recent work
(campaign-gated lifecycle, template-world cloning, relay warm-pool/auto-start).

## Priorities

1. **Reliability of the autonomous loop** — an autonomous GM that silently stops
   receiving events is worse than a co-pilot that waits. (Done: idle-reconnect
   supervisor, `foundry/client.py`. Keep this class of bug at the top.)
2. **Generation & autonomy quality** — the moat. Campaign coherence, proactive
   pacing, in-character consistency.
3. **Cherry-pick only gaps that amplify autonomy:**
   - **Live transcription (STT)** — *only if* playing a physical/voice table; lets
     the AI GM hear the room. Skip for solo/text/online play.
   - **In-Foundry control surface** — close the loop so GM/players drive from inside
     Foundry, not the external admin panel (narration already posts to Foundry chat).

## Explicitly skip (off-moat)

- MCP server for external AI clients
- Broad 23-provider support / provider picker
- Cloud image providers (we're ComfyUI/local-first by design)

These are Familiar's "bring your own AI" commodity plays. They don't make an
autonomous generative GM better; they dilute focus.

## Capability gaps vs Familiar (reference)

Native-module install · MCP server · live transcription · in-Foundry chat UI ·
23-provider breadth · persistent per-NPC voices · deeper baked-in 5e rules
(concentration, legendary actions, death saves, opportunity attacks) · measured
templates/weather/drawings as first-class tools. Of these, only **transcription**
and **in-Foundry control** are on-moat (see Priorities).

## If this becomes a product

The calculus flips. Three "skip" items become mandatory: native-module packaging (or
a dead-simple installer), the MCP server, and provider breadth. And Familiar has a
37-release head start on distribution polish — a fight winnable only on the
generative/autonomous differentiator, not on parity.
