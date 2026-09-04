# Campaign Generation

Campaign creation is exposed through the admin panel and campaign API. It creates campaign data in the vault and can deploy content to an already-created Foundry world.

## Implemented inputs

The build request in `ai-engine/api/routes/campaign.py` accepts a name, description, theme, seed ideas, scale, level range, optional vault files, an optional Foundry world name, and a prologue flag. The admin panel exposes campaign creation and the Campaign Start page.

Generated data may include scenes, encounters, NPCs, quests, settlements, and a prologue, depending on the build and available generators. Counts and content are runtime output; documentation examples are not guaranteed output.

An existing campaign can be imported from a local published-campaign folder through the import endpoint. The importer analyzes supported source material; it does not promise compatibility with every D&D module or setting.

## Deployment and world pairing

AI-GM does not create Foundry worlds. Create and pair a Foundry world first, then select it when deploying or starting a campaign. A campaign without a resolvable world receives a setup error.

The Campaign Start page provides lifecycle actions including Deploy, Start/Resume, End, Extend Campaign, Analyze/Optimize, Restart, and Remove when prerequisites are met. Restart erases session history and redeploys campaign content. Remove deletes campaign-created Foundry content while preserving vault files.

## Editing and extension

Campaign records and deployed Foundry documents can be edited through supported surfaces. “Extend Campaign” generates a further arc from the current level. There is no general natural-language editor that guarantees arbitrary changes to every NPC, quest, relationship, or lore entry.

## Roadmap and limits

Rich relationship webs, adaptive difficulty, and fully guided piece-by-piece generation are aspirations unless a corresponding control or endpoint exists in the current build. Generated content is runtime output, not a shipped published module.

---

Next: [Living World](living-world.md) · [User Guide](../user-guide/overview.md)
