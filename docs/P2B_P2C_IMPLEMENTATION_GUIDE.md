# P2b & P2c Implementation Guide

## P2b: Vault RAG (Retrieval-Augmented Generation)

**Goal:** Enable the LLM to reference campaign lore (settlements, NPCs, handouts, journals) with semantic search rather than keyword matching.

### Architecture

```
CampaignLoader (existing) loads campaign lore files
         ↓
SemanticIndexer (NEW)
  - Chunks campaign documents (settlements, NPCs, etc)
  - Generates embeddings (OpenAI, Ollama, or local)
  - Stores vectors in HNSW index (hnswlib)
         ↓
ContextEnricher (MODIFY context/loader.py)
  - On context build, query SemanticIndexer
  - Retrieve top-K relevant passages by semantic similarity
  - Inject into system prompt before LLM call
         ↓
LLM produces better decisions with live campaign data
```

### Files to Create

1. **vault/indexer.py** (`SemanticIndexer` class)
   - Chunk campaign lore by document type
   - Generate/cache embeddings
   - HNSW index storage

2. **vault/embeddings.py** (`EmbeddingProvider` interface)
   - Support OpenAI, Ollama, sentence-transformers
   - Cache embeddings to disk

3. **tests/test_semantic_indexing.py**
   - Index campaign data
   - Retrieve relevant passages
   - Verify recall on known queries

### Integration Points

- **context/loader.py**: `get_campaign_context()` calls `semantic_indexer.retrieve(query)`
- **main.py**: Initialize `SemanticIndexer` in lifespan (after campaign load)
- **config.py**: Add `VAULT_EMBEDDINGS_MODEL`, `VAULT_INDEX_PATH`

### Estimated Effort: 5-7 days

- Day 1-2: Embeddings provider + HNSW wrapper
- Day 3: Document chunking strategy (settlements, NPCs, handouts)
- Day 4: Caching + index persistence
- Day 5-6: Context integration + prompt injection
- Day 7: Tests + tuning retrieval quality

---

## P2c: Procedural Layout Fallback

**Goal:** Auto-generate interior maps when manual layouts don't exist (for taverns, dungeons, etc).

### Architecture

```
ActionDispatcher receives "create_interior_map" action
         ↓
ProceduralLayoutGenerator (NEW)
  - Building type → BSP/cellular automata
  - Generate dungeon graph (rooms + corridors)
  - Export as Foundry scene JSON
         ↓
ControlNet (optional, high-effort)
  - Describe layout in prose
  - Pass to ControlNet with layout canvas
  - Generate atmospheric dungeon art
         ↓
Foundry imports scene + walls + audio ambience
```

### Files to Create

1. **procedural/layout_gen.py** (`ProceduralLayoutGenerator` class)
   - BSP tree dungeon generation
   - Cellular automata for natural caves
   - Export to Foundry wall/tile format

2. **procedural/controlnet.py** (optional)
   - Describe scene layout
   - Query ControlNet / Stable Diffusion
   - Cache generated images

3. **tests/test_procedural_layouts.py**
   - Generate tavern, dungeon, cave layouts
   - Verify Foundry JSON validity
   - Test wall placement logic

### Integration Points

- **actions/dispatcher.py**: Handle `"procedural_layout"` action type
- **main.py**: Initialize layout generator (no LLM required)
- **api/routes/procedural.py**: Expose `/api/procedural/generate` endpoint

### Estimated Effort: 3-5 days (without ControlNet: 1-2 days)

**Without ControlNet (Recommended for MVP):**
- Day 1: BSP dungeon gen + Foundry export
- Day 2: Cellular automata + tavern layout
- Day 3: Tests + scene import

**With ControlNet (Enhancement):**
- +2-3 days for image generation + caching

---

## Implementation Order

1. **P2b first** — Vault RAG unlocks better decision-making across all agent actions
2. **P2c after** — Procedural layout is lower priority, works as a fallback

### Quick-Start Checklist

- [ ] P2b: Create `vault/indexer.py` with embedding + HNSW
- [ ] P2b: Integrate into `context/loader.py` for context building
- [ ] P2b: Test retrieval quality with real campaign data
- [ ] P2c: Implement BSP dungeon generation
- [ ] P2c: Export to Foundry scene format
- [ ] P2c: Integration tests with real scenes

---

## Reference: Complete P1 + P2a Status

| Component | Status | Tests | Commits |
|-----------|--------|-------|---------|
| P0: House Rules | ✅ | 5 | ✅ |
| P1a: Living Settlement | ✅ | 12 | ✅ |
| P1b: In-Foundry Control | ✅ | 7 | ✅ |
| P2a: Approval Gate | ✅ | 24 | ✅ |
| P2b: Vault RAG | 🔄 WIP | — | — |
| P2c: Procedural Layout | 🔄 WIP | — | — |

