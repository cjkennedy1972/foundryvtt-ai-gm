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
3. **Keep new work on-moat** — the original P0–P2 backlog is done (see below). Judge
   anything new by whether it makes the generative/autonomous GM better, not by
   whether Familiar has it.

## Backlog status (reviewed 2026-09-20)

The backlog below came from reviewing **Familiar** and **Loremaster** — the point was
to take the *ideas* that reinforce autonomy, not the co-pilot framing. Source tagged in
brackets. Every P0, P1 and P2 item has since shipped; each line names the code that
implements it.

### P0 — shipped
- **Canon system (draft vs. canonized lore)** [Loremaster] — `context/canon.py`, the
  `/gm canonize`, `/gm canon review` and `/gm canon approve|reject` commands in
  `foundry/chat_listener.py`, and `canon_context` in the system prompt.
- **GM ruling / directive channel** [Loremaster] — `/gm rule <text>`,
  `foundry/chat_listener.py:1237`.
- **House Rules journal** [Loremaster] — `house_rules_context`,
  `llm/system_prompts.py:522`.

### P1 — shipped
- **Multi-player input batching** [Loremaster] — `input_batch_debounce_seconds`
  (`config.py:145`) debounces simultaneous messages into one turn out of combat when
  more than one player is active (`foundry/chat_listener.py:602`).
- **In-Foundry control surface** [Familiar] — `foundry-module/aigm-control-panel`.
- **Conversation → journal export / session recap** [Loremaster] —
  `POST /api/session/export-recap`.
- **Living settlement generation** [Fantasy Town Generator] —
  `world/settlement_generator.py`; `WorldClockAgent.advance()` moves NPCs to their
  scheduled locations and emits `NPC_MOVED` (`worldclock/agent.py:82`). Factions are
  event-sourced through `FACTION_*` event types. The building/occupancy model and the
  typed relationship graph stayed out of scope, as planned.

### P2 — shipped, one with a changed design
- **Change-approval gate** [Loremaster] — built as a **post-hoc audit trail**
  (`actions/audit.py`), not a pre-execution gate. The AI-GM runs unattended by design,
  so a gate that blocks on a human defeats the product; constrain the action set up
  front, record everything after. See `docs/features/action-audit-trail.md`.
- **Vault RAG / semantic retrieval** [Loremaster] — `vault/vault_semantic_rag.py`,
  `vault/embeddings.py`, `vault/indexer.py`.
- **Procedural layout fallback for interior maps** [DunGen] —
  `campaign/layout_generator.py` (BSP and cellular-automata, guaranteed connected).

### Still open — conditional on a physical/voice table
- **Live transcription (STT / push-to-talk)** [Familiar + Loremaster] — lets the AI GM
  hear the room. Skip entirely for solo/text/online play.
- **Emotional TTS tags** [Loremaster] — `[whispers]` rendered as audio, stripped from
  displayed text. Our local-first Kokoro stack (`tts/service.py`) has no expressive
  control; this needs an ElevenLabs-class engine.

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

Native-module install · MCP server · live transcription · 23-provider breadth ·
persistent per-NPC voices · measured templates/weather/drawings as first-class tools.
Of these, only **transcription** is on-moat, and only for a physical table.

Two entries have closed since this list was written: the in-Foundry control surface
(`foundry-module/aigm-control-panel`) and the 5e rules depth — concentration, legendary
and lair actions, death saves and opportunity attacks all live in `rules/` and
`combat/` now.

## If this becomes a product

The calculus flips. Three "skip" items become mandatory: native-module packaging (or
a dead-simple installer), the MCP server, and provider breadth. And Familiar has a
37-release head start on distribution polish — a fight winnable only on the
generative/autonomous differentiator, not on parity.
