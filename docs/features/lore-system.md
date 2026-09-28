# Lore System

AI-GM stores campaign material in a vault and can retrieve relevant text for prompts. This is semantic retrieval, not full entity understanding.

## Implemented

`ai-engine/vault/vault_semantic_rag.py` indexes vault documents and searches them using embeddings and optional keyword/entity signals. `EntityExtractor` identifies capitalized word sequences and a fixed list of D&D terms. The indexer and semantic-RAG tests cover indexing and retrieval.

Campaign loading and generation write campaign data and linked files to the campaign vault. The chat listener can include campaign/NPC context in an AI turn, and session conversations/events are persisted in SQLite.

Retrieval results are context, not authoritative adjudication; the referee and action schemas check proposed mechanical actions.

## Enriching lore from additional sources

Lore can be added to an existing campaign after it is built. [Enrichment](campaign-generation.md#enriching-an-existing-campaign) reads a new source, appends new material to the campaign's `Worldbuilding.md` and `History.md`, merges new NPCs, locations, factions, and artifacts into `campaign.json`, and keeps the source's notes under `Lore/Sources/`. Those files are picked up by the same retrieval as the rest of the vault: `CampaignLoader.reload()` re-reads the campaign folder and rebuilds the keyword and semantic indexes, and the enrichment endpoint calls it when the enriched campaign is the loaded one. Without that call, files added to the vault after startup are not seen until the campaign is loaded again.

The world context placed in the prompt is the campaign's root `Worldbuilding` note when there is one, not any other note with "World" in its name.

Enrichment also checks new material against what the campaign already says. Contradictions it finds are sent to the canon proposal queue rather than applied. That check is LLM-judged, applies only to enrichment, and is a review aid, not a guarantee.

## Not implemented

There is no general coreference resolver, knowledge graph, guaranteed identity merge, general contradiction resolver, or proof that two descriptions refer to the same person. For example, the extractor does not establish that “the merchant who was robbed” and “Lord Thayer” are the same NPC. NPC memory and goals are separate runtime structures.

Claims that the system automatically maintains perfect relationships, thematic coherence, or canon consistency are not guaranteed by the code.

## Roadmap

Coreference resolution, explicit entity identity, contradiction detection beyond enrichment sources, contradiction resolution, and richer canon tooling remain future work. Use explicit names and inspect or correct canon through the available GM commands and Foundry data.

---

Next: [Action Audit Trail](action-audit-trail.md)
