# ComfyUI Workflows & Configuration

This directory contains the ComfyUI configuration, workflows, and setup documentation for generating D&D battlemap and NPC portrait images.

## 📁 Files in This Directory

### Configuration & Documentation

- **`sdxl_battlemap_workflow.json`** - Complete workflow configuration
  - Checkpoint: `dDBattlemapsSDXL10_upscaleV10.safetensors`
  - Optimal sampler: `dpmpp_3m_sde` with `karras` scheduler
  - Default settings for maps and portraits
  - Node graph structure

- **`SETUP_GUIDE.md`** - Comprehensive installation and setup guide
  - Step-by-step ComfyUI installation
  - Model downloading instructions
  - Configuration verification
  - Troubleshooting guide
  - Performance tuning tips

- **`QUICK_REFERENCE.md`** - Quick lookup card
  - Essential commands and settings
  - Generation time estimates
  - Common issues and quick fixes
  - Default parameter table

- **`README.md`** - This file

### Utilities

- **`verify_comfyui_setup.py`** - Setup verification script
  - Tests ComfyUI connectivity
  - Verifies checkpoint installation
  - Checks sampler/scheduler availability
  - Validates required nodes
  - Generates diagnostic report

## 🚀 Quick Start

### 1. Initial Setup

If setting up ComfyUI for the first time:

```bash
# Read the full setup guide
cat SETUP_GUIDE.md

# Follow instructions to:
# 1. Install ComfyUI
# 2. Download dDBattlemapsSDXL10_upscaleV10.safetensors
# 3. Verify installation
```

### 2. Verify Your Installation

```bash
cd /path/to/ai-engine/campaign/workflows

# Run verification script
python verify_comfyui_setup.py

# For verbose output
python verify_comfyui_setup.py --verbose

# For a different ComfyUI URL
python verify_comfyui_setup.py --url http://localhost:8188
```

### 3. Use in Your Code

The AI GM system automatically uses this configuration:

```python
from campaign.map_generator import MapGenerator

# Initialize (uses defaults from this directory)
generator = MapGenerator(comfyui_url="http://localhost:18188")

# Generate a map
result = await generator.generate_map(
    prompt="top-down tavern interior with bar and fireplace",
    output_dir=Path("./maps")
)

# Generate a portrait
result = await generator.generate_portrait(
    prompt="elven ranger with green eyes",
    output_dir=Path("./portraits")
)
```

## ⚙️ Default Configuration

### Map Generation
- **Width:** 1024 px
- **Height:** 768 px
- **Steps:** 28 (high quality, ~100s per image)
- **CFG:** 7.5 (optimal for SDXL)
- **Sampler:** `dpmpp_3m_sde`
- **Scheduler:** `karras`

### Portrait Generation
- **Width:** 512 px
- **Height:** 768 px
- **Steps:** 28 (detailed facial features, ~80s per image)
- **CFG:** 7.5
- **Sampler:** `dpmpp_3m_sde`
- **Scheduler:** `karras`

## 🔍 Verification

To check your setup:

```bash
# Basic check
python verify_comfyui_setup.py

# Example output:
# ✅ ComfyUI running (v0.24.1)
# ✅ Found dDBattlemapsSDXL10_upscaleV10.safetensors
# ✅ All optimal samplers and schedulers available
# ✅ All required nodes available
# ✅ Workflow config found (v1.0)
```

If any checks fail, see `SETUP_GUIDE.md` Troubleshooting section.

## 📊 Performance

Generated on M1/M2 Mac with 8GB unified memory:

| Task | Sampler | Steps | Time |
|------|---------|-------|------|
| Map Gen | dpmpp_3m_sde | 28 | ~100s |
| Portrait Gen | dpmpp_3m_sde | 28 | ~80s |
| Map (fast) | dpmpp_2m_sde | 20 | ~60s |
| Portrait (fast) | heun | 20 | ~50s |

Times vary based on:
- Hardware (GPU, CPU, RAM available)
- Prompt complexity
- Image resolution
- System load

## 🔧 Customization

### Changing Default Steps

Edit `map_generator.py`:

```python
# For maps (line ~350)
steps: int = 32,  # Change from 28 to 32

# For portraits (line ~420)
steps: int = 32,  # Change from 28 to 32
```

### Changing Checkpoint

Edit `map_generator.py`:

```python
# In __init__ (line ~50)
SDXL_CHECKPOINT = "your_model_name.safetensors"
```

### Changing Sampler/Scheduler

Edit `map_generator.py` in `_build_sdxl_workflow()`:

```python
sampler_name = "dpmpp_2m_sde"  # or "heun", "euler", etc.
scheduler = "sgm_uniform"  # or "simple", "exponential", etc.
```

## 📋 Pre-Installation Checklist

Before using map generation, ensure you have:

- [ ] ComfyUI installed and running
- [ ] `dDBattlemapsSDXL10_upscaleV10.safetensors` in `ComfyUI/models/checkpoints/`
- [ ] Verification script passes: `python verify_comfyui_setup.py`
- [ ] ComfyUI accessible at http://localhost:18188 (or configured URL)

## 🆘 Troubleshooting

### Problem: "Cannot connect to ComfyUI"

```bash
# Is it running?
curl http://localhost:18188/system_stats

# If not, start it:
cd ~/Documents/ComfyUI
python main.py --port 18188
```

### Problem: "Checkpoint not found"

```bash
# Verify file exists
ls ~/Documents/ComfyUI/models/checkpoints/ | grep dDBattle

# If not present:
# 1. Download from Civitai
# 2. Place in ComfyUI/models/checkpoints/
# 3. Restart ComfyUI
```

### Problem: "Sampler not available"

```bash
# Check available samplers
curl http://localhost:18188/object_info/KSampler | \
  jq '.KSampler.input.required.sampler_name[0]'

# If dpmpp_3m_sde missing:
# - Update ComfyUI: git pull && pip install -r requirements.txt
# - Or use dpmpp_2m_sde as fallback
```

## 📖 References

- [SETUP_GUIDE.md](SETUP_GUIDE.md) - Detailed setup instructions
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Quick lookup
- [sdxl_battlemap_workflow.json](sdxl_battlemap_workflow.json) - Workflow definition

## 🔄 Keeping Configuration in Sync

This configuration is auto-used by the AI GM system:

1. **No manual workflow creation** - The system builds workflows automatically
2. **Configuration is code** - Edit settings in `map_generator.py` directly
3. **JSON is reference** - `sdxl_battlemap_workflow.json` documents the structure
4. **Verification is available** - Run `verify_comfyui_setup.py` anytime

When you update checkpoint or sampler preferences, they apply immediately to new generations.

## ✅ You're Ready When...

```bash
# Run verification
python verify_comfyui_setup.py

# If you see all green checkmarks:
# ✅ ComfyUI connection
# ✅ Required checkpoint installed
# ✅ Optimal samplers/schedulers
# ✅ All required nodes available
# ✅ Workflow config found

# Then start generating maps!
```

## 📝 Version Info

- **Configuration Version:** 1.0
- **Last Updated:** 2025-06-15
- **ComfyUI Version:** 0.24.1+
- **Checkpoint:** dDBattlemapsSDXL10_upscaleV10
- **Optimal Sampler:** dpmpp_3m_sde + karras

---

For issues or questions, see SETUP_GUIDE.md or run `verify_comfyui_setup.py --verbose`
