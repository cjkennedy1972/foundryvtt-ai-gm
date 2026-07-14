# AI-GM — Positioning & Roadmap

Strategic direction for the FoundryVTT AI-GM, written after a capability review
against **Familiar** (`familiar-vtt`) and **Loremaster** (`loremaster-foundry`) —
two commercial AI co-pilot modules for Foundry.
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
3. **Work the candidate-feature backlog (below), top tier first** — concrete builds
   that amplify autonomy, prioritized P0 → P2. Everything there reinforces the
   generative/autonomous GM rather than chasing co-pilot parity.

## Candidate features (prioritized)

Ranked by leverage-to-effort and fit with the autonomous generative model. Sourced
from reviewing **Familiar** and **Loremaster** (both co-pilot modules) — the point is
to take the *ideas* that reinforce autonomy, not the co-pilot framing. Source tagged
in brackets. Verified against the codebase: none of the P0/P1 items exist today.

### P0 — highest leverage, do next
- **Canon system (draft vs. canonized lore)** [Loremaster] — AI output stays a draft
  until canonized; canonized moments persist to the Obsidian vault as durable campaign
  truth. We have no draft/canon distinction today. Directly strengthens long-campaign
  consistency, which is the moat. Medium build. **Highest-leverage single item.**
- **GM ruling / directive channel** [Loremaster] — let the human GM pin an
  authoritative fact into context (extend the existing `/gm` command channel,
  `foundry/chat_listener.py`) that the AI must honor. Cheap; high steering value for an
  unattended AI.
- **House Rules journal** [Loremaster] — a designated vault/journal doc always injected
  into the system prompt. Trivial; high consistency/immersion payoff.

### P1 — on-moat, medium build
- **Multi-player input batching** [Loremaster] — debounce simultaneous player messages
  into one cohesive GM turn (timer or `!send`) instead of one turn per message. Fewer
  disjointed responses, fewer LLM calls. We batch *actions* today, not *inputs*.
- **In-Foundry control surface** [Familiar] — drive the session from inside Foundry, not
  only the external admin panel (narration already posts to Foundry chat).
- **Conversation → journal export / session recap** [Loremaster] — persist a session
  summary back into Foundry as a journal. We already summarize context; this is the
  write-back.
- **Living settlement generation** [Fantasy Town Generator] — generate towns as
  structured, populated entities: buildings (name / type / services / inventory +
  occupants), NPCs with an occupation, a **daily routine/schedule**, and a typed
  relationship graph, plus religions alongside the factions we already generate. Gives
  the autonomous GM a queryable living social world ("who's in the tavern at dusk?")
  instead of improvising NPCs fresh — a direct world-building/immersion win that widens
  the generative moat. Builds on infra we already have: Obsidian vault, item-piles (shop
  inventories), Simple Calendar (time-of-day), journal linking. Today NPCs are narrative
  notes (role/personality/free-text relationships) with no schedule or building model.
  Take FTG's *world model*, **not** a dependency on the FTG cloud service (premium tiers,
  iframe embed, cloud-stored settlements — contradicts local-first).

### P2 — higher value, larger build; plan before starting
- **Change-approval gate** [Loremaster] — optional "propose → GM approves" for
  consequential mutations (level-ups, stat/item grants) instead of auto-applying every
  action. Trust + safety for an autonomous GM. We auto-apply everything today.
- **Vault RAG / semantic retrieval** [Loremaster] — semantic index over our own
  generated vault so the right lore/NPC/quest surfaces on demand, instead of recency
  trimming. Consistency win that compounds over long campaigns. No embeddings/retrieval
  today. Take the *technique*, not Loremaster's run-someone-else's-PDF product.
- **Procedural layout fallback for interior maps** [DunGen] — an embedded open
  procedural generator (BSP / cellular-automata) that produces guaranteed-connected
  dungeon/cave geometry, used as the ControlNet control input or as a fallback when the
  LLM's `scene_setup` fails validation. We already auto-generate walls/doors and
  ControlNet-conform the art (`campaign/map_generator.py`), so this is a reliability
  tweak — not a new capability. Build only if the LLM is observed emitting bad geometry.
  Use an open/local generator, **not** the proprietary DunGen.app API (off-moat,
  network-dependent, contradicts local-first).

### Conditional — only for a physical/voice table
- **Live transcription (STT / push-to-talk)** [Familiar + Loremaster] — lets the AI GM
  hear the room. Skip entirely for solo/text/online play.
- **Emotional TTS tags** [Loremaster] — `[whispers]` rendered as audio, stripped from
  displayed text. Depends on an ElevenLabs-class engine; our local-first Kokoro stack
  may not support it.

## Explicitly skip (off-moat)

- MCP server for external AI clients
- Broad 23-provider support / provider picker
- Cloud image providers (we're ComfyUI/local-first by design)
- Shared content library / marketplace / tier-gating / Patreon hosting (Loremaster) —
  commercial distribution mechanics, irrelevant to a local build
- **PDF-import-as-primary-play** (Familiar + Loremaster) — running someone else's
  published adventure as the main mode is the co-pilot model; it dilutes the generative
  moat. Take the RAG *technique* (P2), not the run-a-PDF *product*.

These are the co-pilot / "bring your own AI" commodity plays. They don't make an
autonomous generative GM better; they dilute focus.

## Capability gaps vs Familiar (reference)

Native-module install · MCP server · live transcription · in-Foundry chat UI ·
23-provider breadth · persistent per-NPC voices · deeper baked-in 5e rules
(concentration, legendary actions, death saves, opportunity attacks) · measured
templates/weather/drawings as first-class tools. Of these, only **transcription**
and **in-Foundry control** are on-moat (see the candidate-feature backlog).

## If this becomes a product

The calculus flips. Three "skip" items become mandatory: native-module packaging (or
a dead-simple installer), the MCP server, and provider breadth. And Familiar has a
37-release head start on distribution polish — a fight winnable only on the
generative/autonomous differentiator, not on parity.
