# Modular Architecture Proposal

Branch: `refactor/modular-architecture` · Baseline: 345ae3f · 154 tests green

## The problem, measured

| File | Lines | Bloat indicators |
|---|---|---|
| `ai-engine/main.py` | 3,435 | 93 route decorators, 32 pydantic models, 67 hand-built `JSONResponse` error blocks, 20 copies of the "is Foundry connected" check, 6 copies of the vault-load block, 54 `except Exception` blocks — API, app state, websocket hub, and background tasks in one file |
| `ai-engine/campaign/orchestrator.py` | 3,003 | 47 `"module-id" in mods` conditionals inline in one ~700-line `deploy_to_foundry`; 3 near-identical upload/attach implementations (build maps, build portraits, regen maps+portraits); the `unquote(upload.get("path")) or fallback` idiom 4×; 9 identical try/create/append-status blocks |
| `ai-engine/actions/executors.py` | 1,655 | ~25 executor functions (fine as a dispatch table) but TTS playback machinery embedded in the same file |
| `ai-engine/foundry/client.py` | 1,502 | 41 thin `_send` wrappers (acceptable); inline JS strings — 49 `execute_js` call sites spread across 4 files with no shared snippet library |
| `admin-panel/src/store.js` | 947 | 29 fetch helpers, all the same 13-line try/safeFetch/return shape |
| `admin-panel/src/pages/CampaignList.jsx` | 794 | 4 copies of the same action-panel state machine (loading/result/error state + handler + JSX) |

The bloat is not incidental: every new feature (a new endpoint, a new addon
integration, a new admin action) currently requires copying one of these
patterns, which is why the big files grow ~linearly with features.

## Verdict

Yes — a modular architecture is worth putting in place, and it can be done
incrementally with the test suite green at every step. The design below removes
roughly 2,000 lines of repetition and, more importantly, turns the two growth
axes (API endpoints, addon integrations) into one-small-file-per-feature.

## Target structure

```
ai-engine/
  api/
    deps.py               # get_app_state, require_foundry, require_campaign, ApiError
    schemas/              # pydantic request/response models, by domain
    routes/               # thin routers: campaign_build, campaign_lifecycle,
                          # session, gm_tools, settings, websocket
  app_state.py            # AppState + startup/shutdown lifecycle
  campaign/
    orchestrator.py       # slim pipeline coordinator (phases only)
    vault.py              # CampaignStore: load/save campaign.json + deployment_state
    assets/pipeline.py    # ONE sequential generate→upload→attach implementation
    deployers/            # per-entity deployers sharing one base
      base.py             # the try/create/record pattern, implemented once
      npcs.py scenes.py journals.py quests.py loot.py encounters.py ...
    modules/              # addon integrations — the 47 "in mods" blocks
      registry.py         # ModuleIntegration protocol + active-module resolution
      item_piles.py midi_qol.py levels.py polyglot.py patrol.py smalltime.py ...
    enrichment.py         # enrich_scenes + scene_setup→canvas conversion
  foundry/
    client.py             # transport only: _send, reconnect, events, upload
    scripts.py            # every execute_js snippet as a named, tested function
  tts/                    # playback machinery out of actions/executors.py
```

## The reusable components

1. **`ApiError` + one exception handler** — endpoints `raise ApiError(code,
   message, status)`; a single `@app.exception_handler` renders `ErrorResponse`.
   Kills the 67 JSONResponse blocks and most of the 54 try/excepts.
2. **`require_foundry` dependency** — replaces 20 inline connection checks.
3. **`CampaignStore`** — `load(name)` (with `_normalize_campaign_sections`),
   `save(name, data)`, `deployment_state(name)`. Replaces 6 duplicated blocks;
   the July asset-persistence bug existed precisely because save/load was
   scattered.
4. **`EntityDeployer` base** — subclass supplies `build(item, ctx) -> dict`;
   base does create/record-uuid/record-failure. `deploy_to_foundry` becomes a
   ~50-line coordinator.
5. **`ModuleIntegration` registry** — each addon declares the flags/items it
   contributes per entity type (`scene_flags`, `npc_flags`, `npc_items`,
   `quest_flags`…). Deployers ask the registry instead of 47 inline `in mods`
   branches. Adding an addon (e.g. the planned combat-tracker integration)
   becomes one new file instead of edits across a 700-line function.
6. **`AssetPipeline.upload_and_attach()`** — the single sequential
   implementation (uploads must stay sequential: relay 408s under concurrency),
   with `resolve_uploaded_path()` holding the unquote/fallback idiom once.
   Replaces 3 divergent copies — the divergence is what caused "regenerate
   works but build doesn't".
7. **`foundry/scripts.py`** — named JS snippet builders
   (`count_scene_placeables`, `find_actors_needing_portraits`,
   `import_compendium_actor`…), unit-testable and greppable.
8. **Frontend `apiPost(path)` factory + `useAction()` hook** — the 29 store
   helpers become one-liners; the 4 CampaignList panel state machines collapse
   into one hook + a shared panel component.

## What we deliberately do NOT build

- No plugin framework, DI container, or event bus — the registry is a dict.
- No repository/service layers over the SQLite `Database` class — it's fine.
- `foundry/client.py`'s 41 wrappers stay: thin, flat, and readable beats a
  generic gateway abstraction.
- `actions/executors.py`'s dispatch-table shape stays; only TTS moves out.

## Migration plan (tests green after every phase)

| Phase | Work | Risk | Payoff |
|---|---|---|---|
| 1 | Split `main.py` into `api/routes/*` + `api/schemas/*` + `deps.py` + `app_state.py`. Pure moves, zero behavior change. | Low | Largest file 3,435 → ~300; endpoints findable |
| 2 | `CampaignStore` + `ApiError`/handler + `require_foundry`. Endpoints shrink to their actual logic. | Low | −~800 lines of boilerplate |
| 3 | `AssetPipeline` — unify the 3 upload/attach paths (build, redeploy, regenerate all call it). | Medium | One code path for the bug class we just fixed |
| 4 | `ModuleIntegration` registry + `EntityDeployer`s; `deploy_to_foundry` becomes a coordinator. | Medium | Addon integrations become one file each |
| 5 | `foundry/scripts.py`; move TTS out of executors. | Low | JS snippets testable |
| 6 | Frontend `apiPost` factory + `useAction` hook. | Low | −~350 lines of JS/JSX |

Phases 1–2 are mechanical and safe to do immediately. Phase 4 is the one that
changes real logic — it should land with a live deploy verification against a
test world, not just unit tests.
