# ComfyUI Setup Guide for Campaign Map Generation

This guide documents the optimal ComfyUI configuration for generating D&D battlemap and portrait images using the AI GM system.

## Quick Reference

- **ComfyUI Version:** 0.24.1+
- **Primary Checkpoint:** `dDBattlemapsSDXL10_upscaleV10.safetensors`
- **Optimal Sampler:** `dpmpp_3m_sde` (with karras scheduler)
- **Map Generation:** 28 steps, 7.5 CFG, 1024×768 resolution
- **Portrait Generation:** 28 steps, 7.5 CFG, 512×768 resolution
- **URL:** `http://localhost:18188` (default)

## Installation Steps

### 1. Install ComfyUI

```bash
# Clone ComfyUI
git clone https://github.com/comfyanonymous/ComfyUI
cd ComfyUI
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Download Required Models

#### Primary Checkpoint (REQUIRED)
Download `dDBattlemapsSDXL10_upscaleV10.safetensors` from:
- [Civitai](https://civitai.com) - Search for "dDBattlemaps SDXL"
- [HuggingFace](https://huggingface.co) - Search for the model name

Place in: `ComfyUI/models/checkpoints/`

#### Example Alternative Checkpoint (Optional)
- `v1-5-pruned-emaonly-fp16.safetensors` - For Stable Diffusion 1.5 (not recommended for maps)

### 3. Verify Model Installation

```bash
# Start ComfyUI
python main.py --port 18188

# Check available checkpoints
curl http://localhost:18188/object_info/CheckpointLoaderSimple | \
  jq '.CheckpointLoaderSimple.input.required.ckpt_name[0]'

# Should output array including:
# "dDBattlemapsSDXL10_upscaleV10.safetensors"
```

### 4. Verify Sampler Availability

```bash
# Check available samplers
curl http://localhost:18188/object_info/KSampler | \
  jq '.KSampler.input.required.sampler_name[0]'

# Verify these are available:
# - dpmpp_3m_sde (preferred for maps)
# - dpmpp_2m_sde (fallback, faster)
# - karras scheduler (in scheduler_name[0])
```

## Workflow Configuration

The optimal workflow for map generation is documented in `sdxl_battlemap_workflow.json`.

### Key Parameters

| Parameter | Maps | Portraits | Notes |
|-----------|------|-----------|-------|
| Width | 1024 | 512 | Portrait narrower for face focus |
| Height | 768 | 768 | Consistent height for detail |
| Steps | 28 | 28 | Higher steps = more detail |
| CFG | 7.5 | 7.5 | SDXL sweet spot (avoid >8.5) |
| Sampler | dpmpp_3m_sde | dpmpp_3m_sde | Best quality for SDXL |
| Scheduler | karras | karras | SDXL-optimized scheduler |

### Negative Prompts

**For Maps:**
```
blurry, low quality, modern, photorealistic, anime, cartoon, 3d render, text, watermark, logo, oversaturated, washed out, flat lighting, uniformly gray, featureless, empty, simplistic shapes
```

**For Portraits:**
```
blurry, low quality, modern, photorealistic, anime, cartoon, deformed, ugly, bad anatomy
```

## Style Prefixes (Auto-Applied)

The map generator automatically prepends these to all prompts:

### Fantasy Map
```
high-quality fantasy top-down map, aged parchment texture with burn marks, medieval cartography style, detailed terrain features, ornate compass rose, visible grid lines, rich earth tones and forest greens,
```

### Dungeon
```
professional top-down dungeon map, weathered stone corridors with dynamic lighting, flickering torchlight creating dramatic shadows, trap markers and hazards visible, scattered bones and treasure, atmospheric mist on floor, gritty parchment aesthetic with worn edges,
```

### Overworld
```
stunning isometric fantasy world map, layered terrain with mountains casting shadows, dense forests with texture, winding rivers reflecting light, scattered villages and settlements, trade route markers, elegant borders, vibrant yet cohesive color palette,
```

### Portrait
```
professional fantasy character portrait, digital painting quality, dramatic cinematic lighting, intricate facial features and expressions, rich clothing details, epic fantasy illustration style with atmospheric background,
```

## Performance Notes

### Generation Times (macOS M-series GPU)
- Maps (1024×768, 28 steps): ~90-120 seconds
- Portraits (512×768, 28 steps): ~60-80 seconds
- With dpmpp_2m_sde (faster sampler): ~60-80 seconds for maps

### Quality vs Speed Trade-offs

**High Quality (default):**
- Sampler: `dpmpp_3m_sde`
- Steps: 28
- Time: ~90-120s per map
- Best for final assets

**Balanced:**
- Sampler: `dpmpp_2m_sde`
- Steps: 25
- Time: ~70-90s per map
- Good quality, faster iteration

**Fast Preview:**
- Sampler: `heun` or `dpmpp_2m_sde`
- Steps: 20
- Time: ~50-60s per map
- Acceptable quality, good for testing

## Environment Variables

Add to your shell profile or `.env`:

```bash
# ComfyUI location
export COMFYUI_HOME=/path/to/ComfyUI

# Map output directory
export COMFYUI_OUTPUT_DIR=/path/to/campaign_assets/maps

# Default port
export COMFYUI_PORT=18188
```

## Troubleshooting

### "Checkpoint not found" Error

**Problem:** `dDBattlemapsSDXL10_upscaleV10.safetensors` not in model list

**Solution:**
1. Verify file exists: `ls ComfyUI/models/checkpoints/ | grep dDBattle`
2. Check ComfyUI can read it: `curl http://localhost:18188/object_info/CheckpointLoaderSimple`
3. Restart ComfyUI if you added models after startup

### "Sampler not available" Error

**Problem:** `dpmpp_3m_sde` not found

**Solution:**
1. This is a standard SDXL sampler in ComfyUI 0.24+
2. Verify version: `curl http://localhost:18188/system_stats | jq '.system.comfyui_version'`
3. If missing, update ComfyUI: `git pull` and reinstall dependencies

### Scheduler "karras" Not Found

**Problem:** Workflow uses karras but it's not available

**Solution:**
1. Karras is standard in ComfyUI 0.24+
2. Fallback to `sgm_uniform` if needed (similar quality)
3. Update ComfyUI if both are unavailable

## Integration with AI GM

The map generator automatically:
1. Uses this workflow configuration
2. Adds style-specific prefixes to your prompts
3. Selects sampler based on step count (3m for ≥24, 2m for <24)
4. Applies optimal negative prompt filtering
5. Handles model loading and error handling

No manual workflow creation needed—just ensure the checkpoint is installed.

## Updating the Workflow

If you want to modify the workflow configuration:

1. Edit `sdxl_battlemap_workflow.json`
2. Update the defaults section
3. Changes apply to all future map generations
4. Restart the AI GM server for changes to take effect

## References

- [ComfyUI GitHub](https://github.com/comfyanonymous/ComfyUI)
- [ComfyUI Documentation](https://github.com/comfyanonymous/ComfyUI/wiki)
- [SDXL Optimization Guide](https://civitai.com/articles/1093)
- [dDBattlemaps Model Page](https://civitai.com/models/103861) (example link)

## Version History

- **v1.0** (2025-06-15): Initial SDXL optimization
  - dpmpp_3m_sde sampler with karras scheduler
  - 28 steps for maps and portraits
  - Optimized for dDBattlemapsSDXL10_upscaleV10 checkpoint
