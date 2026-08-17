# Handoff: Illustrated Campaign Prologues

**Status:** Planned, not started. Approved 2026-07-18.
**Goal:** Replace the cold open ("you stand at the dungeon door") with a build-time-generated, illustrated prologue — the AI-GM walks players through the world's history and lore before the first scene. Presentation form varies per campaign.

## Problem

Session start today is a single LLM turn at `ai-engine/foundry/chat_listener.py` (`_run_proactive_action`, `reason == "session_start"`, ~line 1273): setup_scene → place_token → optional NPC speak. No world history, no "why are we here." The campaign generator produces factions/locations/journal_entries but has no world-history concept, and nothing presents lore at the table.

## Design principle

The prologue is a **first-class campaign artifact**: written and illustrated at **build time**, presented at session start by a **deterministic engine-driven sequence**. The LLM writes it once; it never improvises it live. Image generation is far too slow for session time.

## The "vessel" concept

The generator LLM chooses a presentation form matching the campaign's tone. Vessel determines art style (ComfyUI prompt prefix), frame narrative, and narration register:

| Vessel | Art style preset | Frame narrative example |
|---|---|---|
| `tome` | illuminated manuscript, gold leaf, aged vellum | "A monk opens the Chronicle of the Age..." |
| `scroll` | faded ink on cracked parchment, sepia | "The herald unrolls a scroll sealed a century ago..." |
| `gallery` | oil paintings, ornate gilt frames, chiaroscuro | "You pass through the great hall; the portraits watch..." |
| `tapestry` | woven textile art, medieval Bayeux style | "Firelight moves across the threadbare tapestry..." |
| `stained_glass` | stained glass window, lead lines, luminous color | "Dawn ignites the cathedral windows one by one..." |
| `mural` | weathered fresco, cracked plaster, faded pigment | "Your torch reveals paintings older than the kingdom..." |
| `cartographer` | antique map, ink annotations, compass roses | "The old explorer spreads his charts across the table..." |

Gothic → stained glass; seafaring → cartographer; war epic → tapestry. The LLM may invent a variant within the schema.

## Presentation: native Foundry, no custom UI

- **JournalEntry** with alternating `image` / `text` pages (Foundry v11+ `JournalEntryPage`) = a real page-turning book, zero frontend code. Stays in players' sidebars as a permanent lore reference.
- **ImagePopout.shareImage** broadcasts each panel fullscreen during the guided walkthrough.
- Per-vessel CSS theming in `foundry-module/` is a deliberate later polish (Phase B2), not v1.

---

## Phase A — Generate (build time)

### A1. Schema — `ai-engine/campaign/generator.py`

Add top-level `prologue` to the campaign JSON contract (alongside `scenes`, `npcs`, `journal_entries`):

```json
"prologue": {
  "vessel": "tapestry",
  "title": "The Weave of the Sundered Oath",
  "frame_narrative": "one paragraph: who/what presents this to the players and where",
  "panels": [
    { "title": "The Age of Concord",
      "body": "2-4 sentences of GM boxed text, past tense, mythic register",
      "image_prompt": "scene description WITHOUT style words — the vessel preset supplies style",
      "era": "ancient" }
  ]
}
```

- 5–7 panels, arc-shaped: *world before → the wound/catastrophe → powers that rose → present tension → "where you stand"*. Last panel must land on the party's starting location (bridges into Act 1).
- Wire into: master prompt JSON example (~line 110 area), `_normalize_campaign_sections` (~1106), `validate_campaign` (~1249: require vessel + ≥4 panels **only if the key exists** — legacy campaigns without `prologue` must pass), `campaign_count_checklist` (~870).
- ~120 lines, mostly prompt text.

### A2. Vessel art presets — `ai-engine/campaign/map_generator.py`

Existing `WORKFLOW_PREFIXES`-style dict (see the `"portrait"` prefix ~line 49) gets one entry per vessel (table above). Generation call: vessel prefix + panel `image_prompt`. Same SDXL/ComfyUI path as portraits (`http://127.0.0.1:18188`). Landscape ~1344×768 so panels fill a journal image page. ~30 lines.

### A3. Asset pipeline — `ai-engine/campaign/orchestrator.py` `generate_assets` (~line 519)

After portraits: one image per panel → `campaign_assets/<name>/prologue/`, upload via `campaign/assets.py` `upload_image` (the unified upload helper — do not add a fourth copy), stash served paths onto panel dicts. Failures non-fatal: a panel without an image renders text-only. ~60 lines.

## Phase B — Deploy (build time)

### B1. Journal creation — `orchestrator.py` `deploy_to_foundry` (~line 1086)

- One JournalEntry `"Prologue — <title>"`, alternating image page (illustration, panel title as caption) + text page (boxed text, era header).
- Flags: `flags.ai-gm = { prologue: true, vessel: "<vessel>", shown: false }`.
- Record journal UUID in `deployment_state.json`.
- **Teardown:** `teardown_campaign` (~2649) deletes flagged docs via `ai-engine/foundry/scripts.py` (~line 705, flag-sweep). Verify the flag shape matches what the sweep matches on — if it keys off a different flag path, align.
- ~70 lines.

### B2 (deferred). Per-vessel CSS themes in `foundry-module/` keyed off the flag. Cosmetic; separate PR.

## Phase C — Present (session start)

### C1. New module `ai-engine/campaign/prologue.py` — deterministic presenter (~120 lines)

```python
async def present_prologue(foundry, narrate_fn, journal_uuid) -> bool:
    # for each panel:
    #   1. broadcast image: ImagePopout(src, {title, shareable}).render(true).shareImage()  [execute_js]
    #   2. narrate panel body via the existing narrate executor (GM voice, vessel-framed)
    #   3. dwell: 4s + len(body)/15 sec reading time, capped 25s
    # close popout; chat card: "The chronicle rests in your journal — revisit it anytime."
    # set flags.ai-gm.shown = true
```

- Deterministic on purpose: a 5-minute cinematic must not depend on the LLM emitting a correct action sequence. The words were written at build time.
- **Skip/abort:** any player chat message during the sequence drops dwell to 1s.

### C2. Session-start hook — `chat_listener.py` `_run_proactive_action` (~1273) + `ai-engine/api/routes/campaign.py`

- On `session_start`: query for journal flagged `{prologue: true, shown: false}`. If found → `present_prologue` first, then inject into the existing opening prompt: *"The players have just witnessed the prologue (summary below). Open the first scene flowing from its final panel — do NOT re-summarize the history."*
- Replay control = the `shown` flag. The restart route (`campaign.py` ~line 886, "Campaign restarted") resets it so a restarted campaign replays the prologue.
- Note the existing retry: `_run_proactive_action` retries session_start once on error (~1405). `present_prologue` must be idempotent-safe under that retry (check `shown` at entry).
- ~40 lines.

### C3. Opt-out

`CampaignBuildRequest` (`campaign.py` ~159): `generate_prologue: bool = True`. Checkbox in `ai-engine/admin-panel/src/pages/CampaignBuilder.jsx`. ~15 lines.

## Phase D — Verify

`ai-engine/tests/test_prologue.py` (~150 lines):
- Schema: vessel required when key present, ≥4 panels, legacy campaigns (no key) pass validation untouched.
- Presenter (AsyncMock foundry): page order, dwell computation, skip-on-chat, `shown` set, no-op when already shown.
- Deploy: journal page structure (image/text alternation), flag shape matches teardown sweep.

Manual E2E: build small campaign → journal renders in Foundry → prologue plays on Start Session → does NOT replay on second start → DOES replay after campaign restart.

## Order, size, constraints

| Step | Files | ~Lines |
|---|---|---|
| A1 | generator.py | 120 |
| A2 | map_generator.py | 30 |
| A3 | orchestrator.py | 60 |
| B1 | orchestrator.py | 70 |
| C1 | campaign/prologue.py (new) | 120 |
| C2 | chat_listener.py, api/routes/campaign.py | 40 |
| C3 | api/routes/campaign.py, admin-panel CampaignBuilder.jsx | 15 |
| D | tests/test_prologue.py | 150 |

- A→B→C strictly sequential; D interleaves.
- **Everything is additive behind the `prologue` key** — campaigns without it behave exactly as today.
- Relay JS convention: execute-js is wrapped as an async function body — use `return await ...`, never an async IIFE; unwrap results via `.get("result")` (see the docstring in `combat/compendium_generator.py::_query_compendium`).
- Run: `cd ai-engine && python3 -m pytest tests/test_prologue.py -q` (plain `python` is not on PATH here).

## Explicitly out of scope (v1)

- Custom fullscreen storybook UI (native journal + ImagePopout ≈ 90% of the moment for 5% of the code; B2 is the upgrade path)
- Ambient prologue music (one `playlist` field later if wanted)
- Per-player variant prologues
