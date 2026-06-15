# ComfyUI Quick Reference Card

## Checkpoint Installation

```bash
# Download dDBattlemapsSDXL10_upscaleV10.safetensors from Civitai
# Place in: ComfyUI/models/checkpoints/

# Verify
curl http://localhost:18188/object_info/CheckpointLoaderSimple | jq '.CheckpointLoaderSimple.input.required.ckpt_name[0]'
```

## Running ComfyUI

```bash
cd ComfyUI
python main.py --port 18188
```

Then access at: http://localhost:18188

## Default Generation Settings

| Type | Width | Height | Steps | CFG | Sampler | Scheduler |
|------|-------|--------|-------|-----|---------|-----------|
| Maps | 1024 | 768 | 28 | 7.5 | dpmpp_3m_sde | karras |
| Portraits | 512 | 768 | 28 | 7.5 | dpmpp_3m_sde | karras |

## Required Models

- ✅ **dDBattlemapsSDXL10_upscaleV10.safetensors** - PRIMARY (in checkpoints/)
- ✅ **SDXL VAE** - Included in checkpoint
- ❌ ~~Z-Image-Turbo~~ - Not used
- ❌ ~~oMLX API~~ - Not used

## Generation Times (M-series Mac)

| Sampler | Steps | Time | Quality |
|---------|-------|------|---------|
| dpmpp_3m_sde | 28 | ~100s | ⭐⭐⭐ Best |
| dpmpp_2m_sde | 25 | ~70s | ⭐⭐ Good |
| heun | 20 | ~50s | ⭐ Fast |

## If Generation Fails

1. **Is ComfyUI running?**
   - Check: `curl http://localhost:18188/system_stats`

2. **Is dDBattlemapsSDXL10_upscaleV10.safetensors installed?**
   - Check: `ls ~/Documents/ComfyUI/models/checkpoints/ | grep dDBattle`

3. **Are samplers/schedulers available?**
   - Check: `curl http://localhost:18188/object_info/KSampler | jq '.KSampler.input.required.sampler_name[0]'`

## Map Prompt Tips

✅ **DO:** Be specific and vivid
- "top-down tavern interior, wooden floorboards, bar counter at back, fireplace glow, lanterns, cozy atmosphere"

❌ **DON'T:** Be too simple
- "tavern map"

✅ **DO:** Include 8+ visual details
- Terrain, materials, lighting, focal points, decorative elements

❌ **DON'T:** Include conflicting styles
- Avoid mixing "cartography" with "photorealistic" or "3D render"

## Portrait Prompt Tips

✅ **DO:** Focus on character details
- "Elven ranger with green eyes, auburn hair, weathered face, leather armor, bow across back"

❌ **DON'T:** Request impossible anatomy
- "4-armed warrior" or other anatomically incorrect features

✅ **DO:** Specify clothing and accessories
- "studded leather jerkin, silver chain, red cloak"

## Files to Keep with the Application

📁 `campaign/workflows/`
- ✅ `sdxl_battlemap_workflow.json` - Workflow configuration
- ✅ `SETUP_GUIDE.md` - Full setup instructions
- ✅ `QUICK_REFERENCE.md` - This file

## Updating Configuration

To change generation parameters:

1. Edit `map_generator.py` lines 158-160 (map defaults) or 361-364 (portrait defaults)
2. Or modify `sdxl_battlemap_workflow.json` defaults section
3. Restart AI GM server

Example: Increase map quality to 32 steps
```python
# In generate_map_comfyui()
steps: int = 32,  # was 28
```

## Environment Variables (Optional)

```bash
# In ~/.zshrc or ~/.bash_profile
export COMFYUI_PORT=18188
export COMFYUI_BASE_URL=http://localhost:18188
```

## Version Info

- **ComfyUI:** 0.24.1+
- **Checkpoint:** dDBattlemapsSDXL10_upscaleV10
- **Sampler:** dpmpp_3m_sde + karras scheduler
- **Config Version:** 1.0 (2025-06-15)

---

**For detailed setup:** See `SETUP_GUIDE.md`

**For workflow details:** See `sdxl_battlemap_workflow.json`
