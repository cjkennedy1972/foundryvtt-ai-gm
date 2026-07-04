# Map Generation Coordinate Alignment Fix

## Problem
Walls and generated map images were misaligned in FoundryVTT:
- **Walls**: Placed at correct grid coordinates (0,0 origin)
- **Background Image**: Appeared centered on a larger grid, offset from walls

## Root Cause
The `generate_layout_mask()` function in `map_generator.py` was **shrinking the layout mask** based on wall coordinates using `min()`:

```python
# BAD: Computes mask dimensions from wall bounds
max_x = max(all_coords[0::2])
max_y = max(all_coords[1::2])
computed_w = int((max_x + 1) * grid_size)
computed_h = int((max_y + 1) * grid_size)
width = min(width, computed_w)  # ← SHRINKS THE MASK!
height = min(height, computed_h)
```

**Example:** 
- Scene setup: 16×12 grid (1024×768 px)
- Walls: Only cover 14×10 grid
- Computed: 960×704 px
- **Result**: ComfyUI upscales 960×704 → 1024×768, **centering walls**

## Solution
**Always create the layout mask at full requested dimensions** (grid_width × grid_size_px, grid_height × grid_size_px).

The black padding around walls is **intentional and necessary** — it ensures walls stay at (0,0) origin when ComfyUI processes the image.

```python
# GOOD: Always use full dimensions
mask = PILImage.new("L", (width, height), 0)  # width/height are full grid dims
```

## Changes Made
1. **map_generator.py:148-167** — Removed dimension shrinking logic
2. **tests/test_layout_mask.py** — Updated tests to expect full dimensions

## Verification
✅ Layout masks now always match scene canvas dimensions  
✅ Walls align at correct grid origin (0,0)  
✅ Black padding ensures ControlNet respects wall positioning  
✅ FoundryVTT walls and background image now align perfectly
