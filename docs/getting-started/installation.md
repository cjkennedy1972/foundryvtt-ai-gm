# Installation Guide

Get AI-GM running on your machine in 10 minutes.

## Prerequisites

- **FoundryVTT** v14 or later ([download](https://foundryvtt.com))
- **Python 3.11+** ([python.org](https://www.python.org))
- **8GB RAM** minimum; 16GB recommended for larger campaigns
- **2GB disk space** for installation and cache

### Supported Platforms

| Platform | Status | Notes |
|----------|--------|-------|
| **macOS** | ✅ Tested | Recommended; best performance |
| **Linux** | ✅ Tested | Ubuntu 22.04+ verified |
| **Windows** | ✅ Tested | WSL2 or native Python 3.11+ |

## Step 1: Clone the Repository

```bash
git clone https://github.com/cjkennedy1972/foundryvtt-ai-gm.git
cd foundryvtt-ai-gm
```

## Step 2: Install Python Dependencies

```bash
cd ai-engine
pip install -r requirements.txt
```

**Expected time**: 2-3 minutes depending on internet speed.

### Troubleshooting Installation

**Issue**: `pip: command not found`
- Ensure Python 3.11+ is installed: `python3 --version`
- On macOS, use `python3 -m pip` instead of `pip`

**Issue**: Permission denied when installing
- Use a virtual environment: `python3 -m venv venv && source venv/bin/activate`

## Step 3: Configure Your LLM

The AI-GM supports three LLM backends. Choose one:

### Option A: Local LLM (Recommended for Privacy)

**Uses**: Ollama with a local quantized model (no API keys needed)

1. Install [Ollama](https://ollama.ai)
2. Pull a model:
   ```bash
   ollama pull neural-chat  # 4B model, ~2GB
   # or
   ollama pull mistral     # Faster, higher quality (~5GB)
   ```
3. Create `.env`:
   ```bash
   cp .env.example .env
   ```
4. Edit `.env`:
   ```ini
   LLM_BASE_URL=http://localhost:11434/v1
   MODEL=neural-chat
   ```
5. Start Ollama in another terminal:
   ```bash
   ollama serve
   ```

### Option B: OpenAI (Recommended for Quality)

1. Get an API key from [platform.openai.com](https://platform.openai.com/api/keys)
2. Create `.env`:
   ```bash
   cp .env.example .env
   ```
3. Edit `.env`:
   ```ini
   LLM_API_KEY=sk-...
   LLM_BASE_URL=https://api.openai.com/v1
   MODEL=gpt-4-turbo
   ```

### Option C: OpenAI-Compatible Server

**Uses**: LocalAI, vLLM, or other OpenAI-compatible endpoints

Edit `.env`:
```ini
LLM_BASE_URL=http://localhost:8000/v1  # Your server URL
MODEL=your-model-name
LLM_API_KEY=  # Leave empty if not required
```

## Step 4: Verify Configuration

```bash
python3 -m pytest tests/ -q  # Run test suite
```

Expected output:
```
....
72 passed in 0.45s
```

## Step 5: Start AI-GM

```bash
python3 main.py
```

Expected output:
```
[INFO] AI-GM initialized
[INFO] FoundryVTT relay listening on http://127.0.0.1:13010
[INFO] Admin API listening on http://127.0.0.1:18080
[INFO] Ready for connections
```

## Step 6: Connect FoundryVTT

1. Open FoundryVTT in your browser
2. Go to **Settings** → **Configure Settings** → **Core**
3. Set **Foundry VTT URL** to `http://localhost:13010` (or your relay URL)
4. Create or load a campaign
5. Click **Start Session**

The AI-GM will automatically generate a world if one doesn't exist.

## Step 7: Create Your First Campaign

Once connected:

1. Open the **Session Control Panel** (sidebar widget)
2. Click **Start Session**
3. The GM will:
   - Generate a settlement
   - Create 3-5 NPCs with personalities and schedules
   - Describe the opening scene
   - Await player input

You're ready to play!

---

## Environment Configuration Reference

All settings are configured via `.env` (created from `.env.example`):

### LLM & Core

| Setting | Default | Description |
|---------|---------|-------------|
| `LLM_API_KEY` | *(empty)* | API key for remote LLM (OpenAI only) |
| `LLM_BASE_URL` | `http://localhost:8800/v1` | LLM endpoint URL |
| `MODEL` | *(required)* | Model name (must be set) |

### Admin API

| Setting | Default | Description |
|---------|---------|-------------|
| `ADMIN_PORT` | `18080` | Admin API port |
| `ADMIN_HOST` | `127.0.0.1` | Admin API bind address |
| `RELAY_URL` | `http://localhost:13010` | Relay service URL |
| `ADMIN_TOKEN` | *(unset)* | Bearer token required on `/api/*` and the admin WebSocket. Not applied to `/admin` (static panel) or `/audio` (narration files) |
| `VAULT_EMBEDDINGS_ENABLED` | `true` | Semantic vault search. Requires `pip install -r requirements-embeddings.txt`; falls back to keyword search with a startup warning if absent |

### World & Session

| Setting | Default | Description |
|---------|---------|-------------|
| `GM_IDLE_TIMEOUT` | `30` | Seconds of silence before GM's first idle nudge |
| `INPUT_BATCH_DEBOUNCE_SECONDS` | `2.5` | Seconds to wait before batching player messages |

### Lore & Semantic RAG

| Setting | Default | Description |
|---------|---------|-------------|
| `VAULT_QUERY_CACHE_ENABLED` | `true` | Enable query result caching for faster repeats |
| `VAULT_QUERY_CACHE_SIZE` | `100` | Max cached queries (LRU eviction) |
| `VAULT_QUERY_CACHE_TTL_SECONDS` | `300` | Cache expiry time (5 minutes) |

### Voice & TTS

| Setting | Default | Description |
|---------|---------|-------------|
| `TTS_VOICE_MALE` | *(empty)* | TTS voice for male NPCs and narrator |
| `TTS_VOICE_FEMALE` | *(empty)* | TTS voice for female NPCs |
| `TTS_VOICE_MAP` | *(empty)* | Custom voice mapping (format: `archetype:voice,...`) |

See `.env.example` for all options and complete descriptions.

---

## Uninstall

To remove AI-GM:

```bash
cd foundryvtt-ai-gm
rm -rf ai-engine  # Remove the Python environment
# FoundryVTT worlds are unaffected and can be reused
```

---

## Next Steps

- **[Quickstart Guide](./quickstart.md)** — Run your first session
- **[User Guide](../user-guide/overview.md)** — Learn all features
- **[Troubleshooting](../troubleshooting/faq.md)** — Common issues

## Support

- GitHub Issues: [github.com/cjkennedy1972/foundryvtt-ai-gm/issues](https://github.com/cjkennedy1972/foundryvtt-ai-gm/issues)
- Discussions: [github.com/cjkennedy1972/foundryvtt-ai-gm/discussions](https://github.com/cjkennedy1972/foundryvtt-ai-gm/discussions)
