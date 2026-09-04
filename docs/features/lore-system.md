# Lore System

AI-GM stores campaign material in a vault and can retrieve relevant text for prompts. This is semantic retrieval, not full entity understanding.

## Implemented

`ai-engine/vault/vault_semantic_rag.py` indexes vault documents and searches them using embeddings and optional keyword/entity signals. `EntityExtractor` identifies capitalized word sequences and a fixed list of D&D terms. The indexer and semantic-RAG tests cover indexing and retrieval.

Campaign loading and generation write campaign data and linked files to the campaign vault. The chat listener can include campaign/NPC context in an AI turn, and session conversations/events are persisted in SQLite.

Retrieval results are context, not authoritative adjudication; the referee and action schemas check proposed mechanical actions.

## Not implemented

There is no general coreference resolver, knowledge graph, guaranteed identity merge, contradiction resolver, or proof that two descriptions refer to the same person. For example, the extractor does not establish that “the merchant who was robbed” and “Lord Thayer” are the same NPC. NPC memory and goals are separate runtime structures.

Claims that the system automatically maintains perfect relationships, thematic coherence, or canon consistency are not guaranteed by the code.

## Roadmap

Coreference resolution, explicit entity identity, contradiction detection/resolution, and richer canon tooling remain future work. Use explicit names and inspect or correct canon through the available GM commands and Foundry data.

---

Next: [Action Audit Trail](action-audit-trail.md)
