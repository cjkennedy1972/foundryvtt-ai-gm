# Campaign Generation

Campaign creation is exposed through the admin panel and campaign API. It creates campaign data in the vault and can deploy content to an already-created Foundry world.

## Implemented inputs

The build request in `ai-engine/api/routes/campaign.py` accepts a name, description, theme, seed ideas, scale, level range, optional vault files, an optional Foundry world name, and a prologue flag. The admin panel exposes campaign creation and the Campaign Start page.

Generated data may include scenes, encounters, NPCs, quests, settlements, and a prologue, depending on the build and available generators. Counts and content are runtime output; documentation examples are not guaranteed output.

An existing campaign can be imported from a local published-campaign folder through the import endpoint. The importer analyzes supported source material; it does not promise compatibility with every D&D module or setting. To add further sources to a campaign afterwards, see [Enriching an existing campaign](#enriching-an-existing-campaign).

## Deployment and world pairing

AI-GM does not create Foundry worlds. Create and pair a Foundry world first, then select it when deploying or starting a campaign. A campaign without a resolvable world receives a setup error.

The Campaign Start page provides lifecycle actions including Deploy, Start/Resume, End, Extend Campaign, Analyze/Optimize, Restart, and Remove when prerequisites are met. Restart erases session history and redeploys campaign content. Remove deletes campaign-created Foundry content while preserving vault files.

## Editing and extension

Campaign records and deployed Foundry documents can be edited through supported surfaces. “Extend Campaign” generates a further arc from the current level. There is no general natural-language editor that guarantees arbitrary changes to every NPC, quest, relationship, or lore entry.

## Enriching an existing campaign

When more source material turns up after a campaign is built (another sourcebook, your own wiki notes, more Foundry journals), enrich the campaign instead of re-importing it. Enrichment reads the new source and adds to the world and lore of the campaign that is already there; it does not regenerate scenes, encounters, loot, or maps.

**How to run it.** On the campaign's **Campaign Start** page, open **Enrich World & Lore**, enter the path to a PDF or to a folder of PDF, `.md`, and `.txt` files, and run it. The same is available as `POST /api/campaign/enrich` (see the [REST reference](../api/rest-endpoints.md#enrich-campaign)), which also accepts a Foundry journal compendium pack or world journal folder. Hidden files, empty files, and other file types in a folder are skipped.

**What it changes.** For each source:

- The extracted notes are saved under `Lore/Sources/<source>/` in the campaign's vault folder.
- New world and history material is appended to `Worldbuilding.md` and `History.md` under a `## From <source>` heading. Existing text is not rewritten.
- NPCs, locations, factions, and artifacts in the source are matched to existing ones by name (and, for NPCs and locations, by an LLM check for variants such as "Kansaldi" and "Kansaldi Fire-Eyes"). Empty fields are filled, lists are merged, and unmatched ones are added. Each records the sources it came from in `sources`.
- Only the changed NPC and location notes are re-written in the vault. Scenes, quests, loot, and encounters are not touched.

**Existing content wins.** Enrichment never overwrites a value the campaign already has:

- A contradiction in the world lore, or an `alignment` or `faction` clash on an NPC, is not applied. It is added to the canon review queue (`GET /api/canon/pending`), where you approve or reject it.
- A different description of something already in the campaign is kept as a `source_notes` entry on it, not as a replacement and not as a conflict.
- `role` and `type` are left alone, since they hold the campaign's own categories (for example `boss` or `ruin`).

A source that has already been added is skipped unless you force it. Forcing a source replaces the `## From <source>` sections it added before instead of stacking new ones, and a conflict that is already waiting in the canon queue is not queued again. Each source is saved as soon as it finishes; if one fails, the run stops there, the sources before it stay saved, and the response is `partial` with the error. If the enriched campaign is the one currently loaded, the engine reloads its lore (also after a partial run), so new material is searchable without a restart.

A source's id is its file name including the extension (`atlas.md` and `atlas.pdf` are separate sources), or the Foundry pack or folder name.

**Limits.**

- Conflict detection is LLM-judged and imperfect. The model can miss a contradiction, and it sometimes adds a claim it also flagged; the engine drops most of those lines, but not reliably. Read the canon queue and the appended sections after a run.
- It updates the vault and the live GM context only. Foundry actors and journals that are already deployed are not changed.
- `source_notes` are stored in `campaign.json` and are not yet shown in the rendered NPC and location notes.
- A large source is read in groups, so a big PDF can take several minutes.

## Roadmap and limits

Rich relationship webs, adaptive difficulty, and fully guided piece-by-piece generation are aspirations unless a corresponding control or endpoint exists in the current build. Generated content is runtime output, not a shipped published module.

---

Next: [Living World](living-world.md) · [User Guide](../user-guide/overview.md)
