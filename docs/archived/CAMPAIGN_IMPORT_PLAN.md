# Campaign Import + Lore-Consistent Adventure Generation

## Context

The AI GM generates campaigns from scratch and deploys them to Foundry. The user wants to also **import ready-made campaigns** (verified example: DriveThruRPG-style product folder in iCloud — adventure PDF(s) + Maps/ JPGs in 300DPI/72DPI grid/gridless variants + Tokens/ PNGs + Handouts/ PDFs), have the AI GM build the world/lore/history from them, run the campaign, and later generate **new adventures consistent with the world's lore** — a guarantee that must hold for imported *and* generated worlds. Hard constraint: **do not break existing campaign creation.**

Key architecture facts (verified):
- Creation: `POST /api/campaign/build` → `CampaignOrchestrator.build_campaign` ([orchestrator.py:2273](ai-engine/campaign/orchestrator.py:2273)) — Phase 2 is one call `generate_campaign_data(...)` producing a `campaign_data` JSON dict; Phases 3–5b (vault save, assets, upload, deploy, encounters, enrich) consume only that dict.
- `generate_assets` (orchestrator.py:521) only generates maps for scenes with `map_needed` truthy and portraits unless `portrait_needed is False`; `upload_maps_to_foundry`/`upload_portraits_to_foundry` upload whatever `map_file`/`portrait_file` points at in the asset dir. **Pre-placing files + flags = source-asset support with zero pipeline changes.**
- Runtime lore = BM25 over every `.md` in `<vault>/Campaigns/<name>/` (`context/loader.py search_vault`), injected per-turn as anchored context. Filename-agnostic. `get_world_context_sync` (loader.py:334) additionally activates if a file key contains "World".
- Consistency gap: `generate_arc_extension_prompt` ([generator.py:937](ai-engine/campaign/generator.py:937)) injects only *name lists* of existing content — no lore bodies. Fix benefits both generated and imported worlds.
- Existing bug found: `extend_campaign_arc` vault fallback reads `campaign_data.json` but the writer saves `campaign.json` (fix while touching).

The build endpoint is a single long synchronous POST with `result.steps`; the import endpoint copies that pattern (no SSE exists).

## Plan

### Step 0 — Branch
`git checkout -b feat/campaign-import master` (user requested branch first).

### 1. New file `ai-engine/campaign/importer.py` (the only substantial new code)
- `scan_product_folder(source_path) -> dict` — classify subdirs by name (`map`/`token`/`handout`), top-level PDFs = adventure; prefer `Printer_Friendly` PDF variant. **Fail fast on 0-byte iCloud placeholders** with a `brctl download '<path>'` hint.
- `extract_pdf_text(pdf)` via **pypdf** (new dep); skip <50-char pages; `chunk_pages(pages, ~12k tokens)` on page boundaries.
- LLM map→reduce (reuses orchestrator plumbing: `_chat_endpoint`, `_suppress_thinking`, `_post_and_parse_campaign_json` 3-retry):
  - **Pass 1** per chunk (5–8 calls): extract markdown GM notes under fixed headings (World/History, Factions, NPCs, Locations, Scenes, Encounters, Plot Beats, Handouts). Extract-only, no invention.
  - **Pass 2** (1 call): notes → campaign JSON using existing `CAMPAIGN_GENERATOR_PROMPT` schema (same convention as arc extension); then `validate_campaign` (auto scene_setup). Skip count-refill — counts come from the source.
  - **Pass 3** (1 call): notes → `Worldbuilding.md` + `History.md`; plus deterministic `Lore/Part NN.md` (raw pass-1 notes) and `Handouts/*.md`.
- Asset matching (pure, unit-testable): `_normalize()` strips `300DPI_/72DPI_/Grid_/Gridless_` prefixes and `- <Product Name>` suffixes.
  - `match_maps_to_scenes`: fuzzy match ≥0.6 (difflib + token overlap); variant pick: **72DPI** (300DPI would blow the relay upload), **Gridless**, labels only for region-type scenes. **If a group has only a 300DPI variant, convert it: Pillow downscale to 72/300 (24%) with LANCZOS, re-encode JPEG quality 85** (`_downscale_to_72dpi(src, dest)` in importer.py — Pillow is already a dep; conversion writes to `maps_dir`, source untouched). Same conversion applies to any matched map >40MB regardless of naming. On match: copy (or convert) into `CampaignStore.maps_dir` as `map_<safe>.jpg`, set `map_file`, `map_needed=False`, scene_setup grid from Pillow image dims (÷64), **empty walls/lights/sounds** (hallucinated walls won't align with pro maps; `enrich_scenes` no-ops on empty). Unmatched scenes keep `map_needed=True` → AI fills gaps.
  - `match_tokens_to_npcs`: ≥0.75 (stricter — wrong face is worse than none), prefer `CLOSEUP` variant; sets `portrait_file`, `portrait_needed=False`. Monster tokens noted in summary only (compendium art already covers monsters).
  - `prepare_handouts`: append journal entries with `pdf_file`/`pdf_src`.

### 2. `ai-engine/campaign/orchestrator.py` — additive only
- `build_campaign(..., campaign_data: Optional[dict] = None)`: Phase 2 becomes `if campaign_data is None: campaign_data = await self.generate_campaign_data(...)`. **This kwarg is the entire touch to the creation path.**
- New `import_campaign(source_path, campaign_name, ...)` (~60 lines): scan → extract → match assets → write lore `.md` files directly into `CampaignStore.folder` (BM25 indexes them automatically; `Worldbuilding.md` also activates `get_world_context_sync`) → upload handout PDFs via existing `foundry/client.py upload_file` → delegate to `build_campaign(campaign_data=...)` → merge import summary into result.
- `deploy_to_foundry` journal loop: one conditional — if `entry.get("pdf_src")`, create a Foundry `type: "pdf"` journal page (generated campaigns never set it).
- `extend_campaign_arc`: **lore injection** — after loading existing_data, build a BM25 query from theme/description/recent arc names, `CampaignLoader.load()` + `search_vault(query, max_results=12)` (~2k tokens), pass as new `lore_context` kwarg; wrap in try/except → `""` on failure. Also **fix the vault fallback** to use `CampaignStore(name, vault_path).campaign_file` (fixes filename + sanitization).

### 3. `ai-engine/campaign/generator.py`
- `generate_arc_extension_prompt(..., lore_context: str = "")` — when non-empty, insert before `## Your Task` (line 995): `## Established World Lore (STAY CONSISTENT — do not contradict these facts)` + chunks. Default = byte-identical output.

### 4. `ai-engine/api/routes/campaign.py`
- Extract the world-attach/clone block of `build_campaign_endpoint` (~lines 366–487) into `_attach_or_create_world(...)` helper (mechanical; keeps the two endpoints from drifting) — the only refactor of existing code.
- `POST /api/campaign/import` — `CampaignImportRequest{source_path, campaign_name, create_world=True, foundry_world_name, foundry_system_id="dnd5e", level_range}`; validate path exists; reuse `CampaignBuildResponse` + `import_summary` field; same `link_world_to_campaign` tail; synchronous like build.

### 5. Admin panel (`ai-engine/admin-panel/src/`)
- `store.js`: `importCampaign()` mirroring `buildCampaign()` (reuse `buildInProgress`/`buildResult`/`buildError`), POST `/campaign/import`.
- `CampaignBuilder.jsx`: "Import Published Adventure" card — folder-path input + Import & Deploy button; reuse existing spinner/steps rendering.

### 6. `ai-engine/requirements.txt`
- Add `pypdf>=4.0,<6` (pure Python, no transitive deps).

## Verification

- **New `ai-engine/tests/test_campaign_importer.py`** (plain pytest, style of test_vault_search.py): normalization against real Dragonlance filenames; map variant selection (72DPI+gridless), 300DPI→72DPI conversion when no 72DPI variant exists (Pillow test image → assert output dims = 24% of source, written to maps_dir), `map_needed=False`, grid-from-image via tiny Pillow-generated JPG, walls emptied, unmatched → `map_needed=True`; token CLOSEUP preference + threshold; folder scan + iCloud fail-fast; chunking losslessness; pass-2 reduce with stubbed llm_client → validate_campaign auto-fills scene_setup. No real 200-page PDF needed (pypdf writes a tiny test PDF).
- **Creation-path-unbroken**: `build_campaign(campaign_data=...)` with `generate_campaign_data` monkeypatched to raise → proves short-circuit; without kwarg → still called. `generate_arc_extension_prompt` default output byte-identical (lore heading absent). Run the existing suite (`npm run build` for admin panel; `pytest` in ai-engine — test_orchestrator_assets, test_campaign_count_compliance, test_campaign_parse_normalization, test_prologue must pass unchanged).
- **Manual E2E**: import the real Dragonlance folder (`brctl download` first) via the admin panel into a dev world; then one normal generated build; then Extend Arc on both and confirm the lore block appears in logs and content references imported lore.

## Risks / mitigations
- Creation-path touches limited to: inert default-None kwarg, `pdf_src` conditional (key never present in generated data), mechanical world-block extraction — each covered by tests above.
- 300DPI upload failures: always pick 72DPI; skip any matched file >40MB with a summary warning.
- Wrong asset matches: thresholds + `import_summary` listing matched/unmatched so the user can fix via existing `/campaign/regenerate-assets`.
- Multi-part maps (Left/Right Side): best single file wins; `# ponytail: no stitching`.
- Imported material stays local (vault + user's own Foundry); nothing redistributed.
