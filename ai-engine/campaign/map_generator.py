"""
Campaign Map Generator — Generate fantasy map images via ComfyUI.

Uses SDXL (Stable Diffusion XL) workflow for high-quality fantasy map and portrait generation.

Output types:
- Top-down dungeon maps (combat-scale)
- Exploration maps (overworld, village, city scale)
- Portrait images (NPC headshots)
"""

import asyncio
import hashlib
import logging
import os
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config import settings
from utils.path_safety import validate_contained_path
from campaign.layout_generator import generate_layout, generate_and_validate, validate_scene_setup

try:
    import PIL.Image as PILImage
    from PIL import ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    PILImage = None

logger = logging.getLogger(__name__)


class MapGenerator:
    """Generate fantasy maps via ComfyUI using SDXL."""

    # ── SDXL checkpoint for map generation ──
    SDXL_CHECKPOINT = "dDBattlemapsSDXL10_upscaleV10.safetensors"

    # ── Style prompt prefixes shared by all generation paths ──
    _STYLE_PREFIXES = {
        # A play surface, not an illustration: flat top-down, even light, open floor for
        # tokens, no frame or paper, nothing drawn on it that Foundry already draws.
        # "filling the entire image" + the void terms below were checked against the real
        # checkpoint: without them a quarter to a third of the frame came out solid black.
        "battlemap": "top-down tabletop battlemap of a single room filling the entire image edge to edge, orthographic view straight down, evenly lit, high-detail floor textures, clear open walkable floor, distinct walls and doorways, props and furniture sized for 5 foot squares, painterly fantasy VTT map, ",
        "fantasy_map": "high-quality fantasy top-down map, aged parchment texture with burn marks, medieval cartography style, detailed terrain features, visible grid lines, rich earth tones and forest greens, ",
        "dungeon": "professional top-down dungeon map, weathered stone corridors with dynamic lighting, flickering torchlight creating dramatic shadows, trap markers and hazards visible, scattered bones and treasure, atmospheric mist on floor, gritty parchment aesthetic with worn edges, ",
        "overworld": "stunning isometric fantasy world map, layered terrain with mountains casting shadows, dense forests with texture, winding rivers reflecting light, scattered villages and settlements, trade route markers, elegant borders, vibrant yet cohesive color palette, ",
        "portrait": "professional fantasy character portrait, digital painting quality, dramatic cinematic lighting, intricate facial features and expressions, rich clothing details, epic fantasy illustration style with atmospheric background, ",
    }

    # What a battlemap must not contain: grid lines (Foundry draws its own), lettering,
    # a border or paper edge, figures (tokens are the figures), and any tilt.
    _BATTLEMAP_NEGATIVE = (
        "grid, grid lines, squares overlay, text, letters, labels, watermark, logo, border, frame, "
        "parchment, paper edge, compass rose, characters, people, creatures, tokens, perspective, "
        "isometric, tilted, vignette, blurry, low quality, photorealistic, anime, 3d render, "
        "black void, empty darkness, black background, unexplored area, multiple rooms, corridors, floor plan"
    )

    # ── Palette: keep maps in D&D's muted, earthy range ──
    # The prompt guidance and style prefixes asked for glowing runes, spectral glow, dramatic shadows and
    # crimson/golden light, and the default negative prompt ruled out "washed out / flat lighting", so maps
    # came out at mean saturation 0.60 (acid green, neon blue). These rewrite the loud words and add the cues.
    _TONE_DOWN = (
        (r"\bglowing\b", "faintly lit"), (r"\bluminescen\w*", "faint light"), (r"\bspectral\b", "pale"),
        (r"\b(?:vibrant|vivid|neon|radiant|luminous|brilliant|saturated|stunning)\b", ""),
        (r"\bdramatic\b", "soft"), (r"\bcrimson\b", "dark red"), (r"\bgolden\b", "warm"),
        (r"\beerie (?:blue )?", ""), (r"\bmagical\b", ""),
    )
    _MUTED_CUES = (", muted natural earth tones, desaturated subdued palette, soft even lighting with gentle shadows, "
                   "matte hand-painted texture, grounded realistic fantasy")
    _VIVID_NEGATIVE = ("oversaturated, neon, vibrant, glowing, luminous, hdr, high contrast, glossy, deep fried, "
                       "psychedelic, saturated colors")
    _MUTED_DROPS = ("washed out", "flat lighting", "uniformly gray")

    def _finish_prompt(self, prompt: str) -> str:
        """The positive prompt with loud wording toned down and muted-palette cues added (when enabled)."""
        if not settings.map_muted_palette:
            return prompt
        for pattern, repl in self._TONE_DOWN:
            prompt = re.sub(pattern, repl, prompt, flags=re.IGNORECASE)
        return re.sub(r"\s{2,}", " ", re.sub(r"\s+,", ",", prompt)).strip() + self._MUTED_CUES

    def _finish_negative(self, negative: str) -> str:
        """The negative prompt without the terms that fight a muted palette, plus anti-vivid ones (when enabled)."""
        if not settings.map_muted_palette:
            return negative
        kept = [t.strip() for t in negative.split(",") if t.strip() and t.strip().lower() not in self._MUTED_DROPS]
        have = {t.lower() for t in kept}
        return ", ".join(kept + [t for t in (x.strip() for x in self._VIVID_NEGATIVE.split(",")) if t.lower() not in have])

    @staticmethod
    def _resolve_cfg(cfg: Optional[float]) -> float:
        return float(settings.map_cfg if cfg is None else cfg)

    @property
    def hires_scale(self) -> int:
        """Scale shared with scene creation for the image dimensions we actually save."""
        return 1

    def _append_hires(self, workflow: Dict, width: int, height: int, seed: int, cfg: float) -> Dict:
        """Skip tiled VAE upscaling due to MPS deadlock on Mac (tiled VAE encode/decode hangs).

        Previously attempted: Lanczos upscale → tiled VAE encode → low-denoise resample → tiled decode.
        This caused 5+ minute hangs on Apple Silicon due to tile overlap synchronization issues.

        NEW (2026-10-02): Skip refinement pass entirely. Base output at 1024×768 is sufficient for TTRPGs.
        Non-tiled VAE would require 3.7+ GB VRAM (unavailable on most systems during other tasks).
        """
        # ponytail: skip hires for reliability; VRAM constraints + MPS deadlock risk too high
        return workflow

    # ── Vessel art style presets for prologue panels ──
    # Each vessel maps to a style prefix that will be prepended to the panel's image_prompt
    _VESSEL_PREFIXES = {
        "tome": "illuminated manuscript page, gold leaf borders, aged vellum texture, medieval scriptorium art, intricate marginalia, rich pigments, gothic calligraphy, ",
        "scroll": "ancient scroll parchment, faded sepia ink, cracked aged texture, weathered edges, historical document aesthetic, calligraphic script, ",
        "gallery": "oil painting in ornate gilt frame, chiaroscuro lighting, museum masterpiece quality, dramatic classical composition, rich impasto textures, ",
        "tapestry": "woven textile art, medieval Bayeux tapestry style, wool and linen threads, embroidered narrative scenes, faded historical colors, decorative borders, ",
        "stained_glass": "stained glass window panel, lead came lines, luminous colored glass, cathedral light streaming through, sacred geometry, gothic tracery, ",
        "mural": "weathered fresco wall painting, cracked plaster texture, faded pigment, ancient mural art, archaeological site aesthetic, narrative frieze composition, ",
        "cartographer": "antique map illustration, ink and watercolor on aged paper, compass roses, ink annotations, coastal hachures, cartouches, sea monsters in margins, ",
    }

    def __init__(
        self,
        comfyui_url: str = "http://127.0.0.1:18188",
        timeout: int = 300,
        checkpoint_name: str = "",
        provider: str = "auto",
        comfyui_input_dirs: Optional[List[Path]] = None,
        # Legacy / unused params kept for call-site compatibility
        omlx_url: str = "",
        omlx_model: str = "",
        omlx_api_key: str = "",
        omlx_base_url: str = "",
        omlx_size: str = "1024x1024",
        omlx_style: str = "fantasy_map",
    ):
        self.comfyui_base_url = comfyui_url.rstrip("/")
        self.timeout = timeout
        self.checkpoint_name = checkpoint_name or self.SDXL_CHECKPOINT
        self.provider = provider
        self._client = httpx.AsyncClient(timeout=timeout)
        self._client_id = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
        # ControlNet model for layout-guided map generation
        self.controlnet_model = "control-union-sdxl-1.0.safetensors"
        # ComfyUI input directories for LoadImage resolution.
        # Configure via settings.comfyui_input_dirs (list of path strings in .env).
        if comfyui_input_dirs is not None:
            self.comfyui_input_dirs = comfyui_input_dirs
        else:
            from config import settings as _settings
            self.comfyui_input_dirs = [Path(p) for p in (_settings.comfyui_input_dirs or [])]
        # Populated lazily by _ensure_comfyui_input_dir() on first layout generation.
        self._detected_comfyui_input_dir: Optional[Path] = None

    @staticmethod
    def _to_pixel_coords(coords: List[float], grid_size_px: int) -> List[int]:
        """Convert grid coordinates to pixel coordinates by the fixed grid size.

        Must NOT auto-fit/normalize to wall bounds — that would rescale and
        re-center walls instead of aligning them 1:1 with the Foundry grid
        (see the CRITICAL note in generate_layout_mask).
        """
        return [int(v * grid_size_px) for v in coords]

    async def _ensure_comfyui_input_dir(self) -> Optional[Path]:
        """Return an input/ directory ComfyUI will scan for LoadImage filenames.

        Priority:
        1. Explicitly configured comfyui_input_dirs (from .env)
        2. Auto-detected from ComfyUI's /system_stats --base-directory
        3. None (caller should warn and skip copy)
        """
        if self.comfyui_input_dirs:
            return self.comfyui_input_dirs[0]
        if self._detected_comfyui_input_dir is not None:
            return self._detected_comfyui_input_dir
        try:
            resp = await self._client.get(f"{self.comfyui_base_url}/system_stats", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                argv = data.get("system", {}).get("argv", [])
                # --base-directory <path> appears in the argv list
                for i, arg in enumerate(argv):
                    if arg in ("--base-directory", "--base_path") and i + 1 < len(argv):
                        base = Path(argv[i + 1])
                        input_dir = base / "input"
                        if input_dir.exists():
                            self._detected_comfyui_input_dir = input_dir
                            logger.info(f"[Layout] Auto-detected ComfyUI input dir: {input_dir}")
                            return input_dir
        except Exception as e:
            logger.debug(f"[Layout] Could not auto-detect ComfyUI input dir: {e}")
        return None

    # ─── Layout mask generation (PIL-based) ──────────────────────────────────

    # Roots that generated assets may be written under. Callers all derive
    # output_dir from sanitize_filename(campaign_name) today, but that is a
    # convention across a dozen call sites rather than an enforced invariant,
    # and this module performs nine filesystem writes trusting the parameter.
    # The same convention-only guarantee is what let a campaign name reach
    # `vault_path / name` unsanitized in context/loader.py. One check at the
    # sink covers every caller, including ones added later.
    _ASSET_ROOTS = ("campaign_assets", "/tmp/ai-gm-maps", "tts_audio")

    @classmethod
    def _checked_output_dir(cls, output_dir: Path) -> Path:
        """Resolve output_dir and refuse anything outside a known asset root."""
        resolved = Path(output_dir).expanduser().resolve()
        for root in cls._ASSET_ROOTS:
            base = Path(root).expanduser().resolve()
            try:
                resolved.relative_to(base)
                return resolved
            except ValueError:
                continue
        raise ValueError(
            f"Refusing to write generated assets outside {cls._ASSET_ROOTS}: {resolved}"
        )

    async def generate_layout_mask(
        self,
        scene_setup: Dict[str, Any],
        width: int = 1024,
        height: int = 768,
        grid_size_px: int = 64,
    ) -> Optional[Path]:
        """Generate a layout mask image from scene_setup wall/door coordinates.

        Creates a black PNG with white lines for walls, and black gaps for doors.
        This layout mask is used as ControlNet conditioning for map generation,
        ensuring the generated map's visual barriers align with the physical
        wall/door objects placed in Foundry.

        Args:
            scene_setup: The scene_setup dict from campaign data, containing
                         'walls' (list of [x0,y0,x1,y1] in grid coords)
                         and 'doors' (list of {c:[x0,y0,x1,y1], door:N, ds:N})
            width, height: Output image dimensions (pixels)
            grid_size_px: Grid square size in pixels (default 64)

        Returns:
            Path to generated mask PNG, or None if no wall data
        """
        if not PIL_AVAILABLE:
            logger.warning("PIL not available — skipping layout mask generation")
            return None

        walls = scene_setup.get("walls", [])
        doors = scene_setup.get("doors", [])
        if not walls and not doors:
            return None

        # CRITICAL: Always create the mask at the full requested dimensions.
        # The mask must match the scene canvas dimensions so walls align with
        # the Foundry grid. Black padding around walls is necessary to ensure
        # the ControlNet doesn't scale/center walls when upscaling to final size.
        # DO NOT shrink based on wall coordinates — that causes walls to be
        # centered in the image when ComfyUI scales up to requested dimensions.

        mask = PILImage.new("L", (width, height), 0)  # black background
        draw = ImageDraw.Draw(mask)

        # Draw all wall segments as white lines
        for seg in walls:
            if len(seg) == 4:
                x0, y0, x1, y1 = self._to_pixel_coords(seg, grid_size_px)
                draw.line([(x0, y0), (x1, y1)], fill=255, width=3)

        # Draw door gaps — overlay black on wall segments where doors exist
        for door in doors:
            c_raw = door.get("c", [])
            if len(c_raw) == 4:
                x0, y0, x1, y1 = self._to_pixel_coords(c_raw, grid_size_px)
                draw.line([(x0, y0), (x1, y1)], fill=0, width=8)

        # Find output directory from scene_setup if available
        output_dir = scene_setup.get("_output_dir", None)
        if output_dir:
            output_dir = Path(output_dir)
        else:
            output_dir = Path("./campaign_assets")

        mask_dir = output_dir / "layouts"
        mask_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        mask_path = mask_dir / f"layout_mask_{timestamp}.png"
        mask.save(str(mask_path))
        logger.info(f"[Layout] Mask generated: {mask_path} ({width}x{height})")
        return mask_path


    # ─── Procedural layout fallback ──────────────────────────────────────────

    async def generate_procedural_layout_mask(
        self,
        scene_setup: Dict[str, Any],
        width: int = 1024,
        height: int = 768,
        grid_size_px: int = 64,
        seed: Optional[int] = None,
        scene_type: str = "dungeon",
    ) -> Optional[Path]:
        """Generate a layout mask from procedurally-generated dungeon geometry.

        Falls back to BSP/cellular-automata generation when the LLM's
        scene_setup fails validation (disconnected walls, out-of-bounds
        coordinates, or empty wall/door data for interior scenes).

        This produces the same wall/door coordinate format consumed by
        generate_layout_mask(), so it works identically with ControlNet.

        Args:
            scene_setup: The original scene_setup dict (used for grid dimensions)
            width, height: Output image dimensions (pixels)
            grid_size_px: Grid square size in pixels
            seed: Random seed for reproducibility (None for random)
            scene_type: Scene type for generator tuning ('dungeon' or 'cave')

        Returns:
            Path to generated mask PNG, or None if PIL unavailable
        """
        if not PIL_AVAILABLE:
            logger.warning("PIL not available — skipping procedural layout mask")
            return None

        gw = scene_setup.get("grid_width", width // grid_size_px)
        gh = scene_setup.get("grid_height", height // grid_size_px)

        # Validate the original scene_setup first
        is_valid, warnings = validate_scene_setup(scene_setup)
        if not is_valid:
            logger.info(
                f"[Procedural] Validating scene_setup for '{scene_setup.get('_scene_type', 'unknown')}' "
                f"— {len(warnings)} issue(s). Generating fallback layout."
            )
            for w in warnings:
                logger.debug(f"[Procedural]   - {w}")

        # Generate procedural layout
        result = generate_layout(
            scene_type=scene_type,
            grid_width=gw,
            grid_height=gh,
            seed=seed,
            method="bsp",
        )

        # Validate the generated layout is actually connected
        fallback_setup = result.to_scene_setup(gw, gh)
        ok, _ = validate_scene_setup(fallback_setup)
        if not ok:
            logger.error("[Procedural] Generated layout failed connectivity check — skipping")
            return None

        # Build the mask image from the procedural walls/doors
        mask = PILImage.new("L", (width, height), 0)
        from PIL import ImageDraw
        draw = ImageDraw.Draw(mask)

        # Draw walls
        for seg in fallback_setup.get("walls", []):
            if len(seg) == 4:
                x0, y0, x1, y1 = self._to_pixel_coords(seg, grid_size_px)
                draw.line([(x0, y0), (x1, y1)], fill=255, width=3)

        # Draw door gaps
        for door in fallback_setup.get("doors", []):
            c_raw = door.get("c", [])
            if len(c_raw) == 4:
                x0, y0, x1, y1 = self._to_pixel_coords(c_raw, grid_size_px)
                draw.line([(x0, y0), (x1, y1)], fill=0, width=8)

        # Save
        output_dir = scene_setup.get("_output_dir", None)
        if output_dir:
            output_dir = Path(output_dir)
        else:
            output_dir = Path("./campaign_assets")

        mask_dir = output_dir / "layouts"
        mask_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        mask_path = mask_dir / f"layout_mask_procedural_{timestamp}.png"
        mask.save(str(mask_path))
        logger.info(
            f"[Procedural] Fallback mask generated: {mask_path} "
            f"({result.rooms.__len__()} rooms, {len(fallback_setup.get('walls', []))} walls)"
        )
        return mask_path

    async def fallback_layout_for_scene(
        self,
        scene: Dict[str, Any],
        width: int = 1024,
        height: int = 768,
        grid_size_px: int = 64,
    ) -> Optional[Path]:
        """Generate a procedural layout mask as a fallback for a scene.

        Used when the LLM's scene_setup fails validation. Replaces the
        scene's walls/doors with procedurally-generated guaranteed-connected
        geometry before passing to ControlNet.

        Mutates the scene dict in-place to swap scene_setup.walls and
        scene_setup.doors with the procedural result.
        """
        setup = scene.get("scene_setup", {})
        scene_type = scene.get("type", "dungeon")

        gw = setup.get("grid_width", width // grid_size_px)
        gh = setup.get("grid_height", height // grid_size_px)

        # Generate procedural replacement
        fallback_setup = generate_and_validate(
            scene_type=scene_type,
            grid_width=gw,
            grid_height=gh,
        )

        # Validate the replacement passes our checks
        ok, warnings = validate_scene_setup(fallback_setup)
        if not ok:
            logger.error(f"[Fallback] Procedural layout for '{scene.get('name')}' failed validation: {warnings}")
            return None

        # Swap in the procedural geometry
        scene["scene_setup"]["walls"] = fallback_setup["walls"]
        scene["scene_setup"]["doors"] = fallback_setup["doors"]
        logger.info(
            f"[Fallback] Replaced scene '{scene.get('name')}' geometry with "
            f"procedural layout ({len(fallback_setup['walls'])} walls, "
            f"{len(fallback_setup['doors'])} doors)"
        )

        # Generate the mask from the new setup
        return await self.generate_layout_mask(
            scene_setup=scene["scene_setup"],
            width=width,
            height=height,
            grid_size_px=grid_size_px,
        )


    # ─── Health / availability ────────────────────────────────────────────────

    async def health_check(self) -> Dict[str, bool]:
        """Check ComfyUI availability."""
        comfyui_ok = await self._comfyui_healthy()
        return {"comfyui": comfyui_ok}

    async def get_models(self) -> List[str]:
        """Checkpoint names ComfyUI currently has loaded, for /api/comfyui/models.

        ComfyUI publishes them as the enum of CheckpointLoaderSimple's
        ckpt_name input, which is the only place the running server lists
        what it can actually load.
        """
        resp = await self._client.get(
            f"{self.comfyui_base_url}/object_info/CheckpointLoaderSimple", timeout=10
        )
        resp.raise_for_status()
        node = (resp.json() or {}).get("CheckpointLoaderSimple", {})
        ckpt = node.get("input", {}).get("required", {}).get("ckpt_name") or []
        names = ckpt[0] if ckpt and isinstance(ckpt[0], list) else []
        return [str(n) for n in names]

    async def _comfyui_healthy(self) -> bool:
        try:
            resp = await self._client.get(
                f"{self.comfyui_base_url}/system_stats", timeout=5
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ─── SDXL workflow ────────────────────────────────────────────────────────

    def _build_sdxl_workflow(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        cfg: float,
        seed: int,
        filename_prefix: str = "map",
        use_controlnet: bool = False,
        controlnet_model: str = None,
        layout_image_path: str = None,
        controlnet_strength: float = 1.0,
        hires: bool = True,
    ) -> Dict:
        """Build an SDXL ComfyUI workflow.

        When use_controlnet is True, includes ControlNet nodes that condition
        generation on a layout mask image (walls/doors from scene_setup).
        """
        # For SDXL, dpmpp_3m_sde with karras scheduler is optimal for quality
        # dpmpp_2m_sde is faster alternative with minimal quality loss
        sampler_name = "dpmpp_3m_sde" if steps >= 24 else "dpmpp_2m_sde"
        scheduler = "karras"  # SDXL-specific scheduler for improved quality
        controlnet_model = controlnet_model or self.controlnet_model

        base_workflow = {
            "3": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self.checkpoint_name},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["3", 1]},
            },
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative_prompt, "clip": ["3", 1]},
            },
            "7": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 2]}},
            "11": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["8", 0],  # SaveImage reads from VAEDecode (node 8), NOT KSampler (node 10)
                    "filename_prefix": filename_prefix,
                },
            },
        }

        if use_controlnet:
            # Layout-guided ControlNet workflow:
            #  3 = CheckpointLoaderSimple (outputs model + CLIP)
            #  4 = CLIPTextEncode (positive prompt conditioning)
            #  5 = CLIPTextEncode (negative prompt conditioning)
            #  6 = ControlNetLoader (outputs model + control_net_weights)
            #  7 = ControlNetApply (applies control_net to positive conditioning)
            #  8 = EmptyLatentImage (latent image)
            # 10 = KSampler (model + controlnet-modified conditioning + control_net weights + latent)
            # 12 = LoadImage (layout mask image)
            # 14 = VAEDecode (decode latent to image)
            # 15 = SaveImage (save final image)
            return (self._append_hires if hires else (lambda wf, *a: wf))({
                "3": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": self.checkpoint_name},
                },
                "4": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": prompt, "clip": ["3", 1]},
                },
                "5": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": negative_prompt, "clip": ["3", 1]},
                },
                "6": {
                    "class_type": "ControlNetLoader",
                    "inputs": {"control_net_name": controlnet_model},
                },
                "7": {
                    "class_type": "ControlNetApply",
                    "inputs": {
                        "conditioning": ["4", 0],
                        "control_net": ["6", 0],
                        "image": ["12", 0],
                        "strength": controlnet_strength,
                    },
                },
                "8": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {"width": width, "height": height, "batch_size": 1},
                },
                "10": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": seed,
                        "steps": steps,
                        "cfg": cfg,
                        "sampler_name": sampler_name,
                        "scheduler": scheduler,
                        "denoise": 1.0,
                        "model": ["3", 0],
                        "positive": ["7", 0],  # ControlNetApply output (embeds control_net data)
                        "negative": ["5", 0],
                        "latent_image": ["8", 0],  # EmptyLatentImage output
                    },
                },
                "12": {
                    "class_type": "LoadImage",
                    "inputs": {
                        "image": os.path.basename(layout_image_path) if layout_image_path else "",
                        "image_type": "IMAGE",
                        "upload": "image",
                    },
                },
                "14": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["10", 0], "vae": ["3", 2]},
                },
                "15": {
                    "class_type": "SaveImage",
                    "inputs": {
                        "images": ["14", 0],
                        "filename_prefix": filename_prefix,
                    },
                },
            }, width, height, seed, cfg)
        else:
            # Standard text-only workflow (unchanged)
            return (self._append_hires if hires else (lambda wf, *a: wf))({
                **base_workflow,
                "6": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": seed,
                        "steps": steps,
                        "cfg": cfg,
                        "sampler_name": sampler_name,
                        "scheduler": scheduler,
                        "denoise": 1.0,
                        "model": ["3", 0],
                        "positive": ["4", 0],
                        "negative": ["5", 0],
                        "latent_image": ["7", 0],
                    },
                },
            }, width, height, seed, cfg)

    # ─── ComfyUI execution helpers ────────────────────────────────────────────

    async def _submit_and_wait(
        self, workflow: Dict, output_dir: Path, filename_hint: str, accept: tuple = (".png", ".jpg", ".jpeg"),
    ) -> Dict[str, Any]:
        """Submit a workflow to ComfyUI and wait for the output image."""
        resp = await self._client.post(
            f"{self.comfyui_base_url}/prompt",
            json={"prompt": workflow, "client_id": self._client_id},
        )
        if resp.status_code != 200:
            return {
                "status": "error",
                "prompt_id": None,
                "error": resp.text,
                "provider": "comfyui",
            }

        prompt_id = resp.json().get("prompt_id")
        if prompt_id is None:
            return {
                "status": "error",
                "prompt_id": None,
                "output_file": None,
                "provider": "comfyui",
                "error": "ComfyUI /prompt returned 200 without a prompt_id",
            }
        output_file = await self._wait_for_completion(prompt_id, output_dir, accept)
        return {
            "status": "success" if output_file else "error",
            "prompt_id": prompt_id,
            "output_file": str(output_file) if output_file else None,
            "provider": "comfyui",
        }

    async def _wait_for_completion(
        self, prompt_id: str, output_dir: Path, accept: tuple = (".png", ".jpg", ".jpeg"),
    ) -> Optional[Path]:
        """Poll ComfyUI history until the prompt completes and download the image."""
        start = time.time()
        while time.time() - start < self.timeout:
            try:
                resp = await self._client.get(
                    f"{self.comfyui_base_url}/history/{prompt_id}"
                )
                if resp.status_code == 200:
                    entry = resp.json().get(prompt_id)
                    if entry:
                        status = entry.get("status", {})
                        if status.get("status_str") == "success":
                            for node_output in entry.get("outputs", {}).values():
                                for img in node_output.get("images", []):
                                    filename = img.get("filename", "")
                                    if filename.endswith(accept):
                                        return await self._download_image(
                                            filename, output_dir
                                        )
                            # Finished, but nothing we accept: waiting longer cannot produce it.
                            logger.warning(f"ComfyUI prompt {prompt_id} finished without a {accept} output")
                            return None
                        if status.get("status_str") == "error":
                            logger.warning(f"ComfyUI error for prompt {prompt_id}")
                            return None
            except Exception as e:
                logger.debug(f"History poll error: {e}")
            await asyncio.sleep(2)

        logger.warning(f"Timeout waiting for ComfyUI prompt {prompt_id}")
        return None

    async def _download_image(
        self, filename: str, output_dir: Path
    ) -> Optional[Path]:
        """Download an image from ComfyUI's output folder.

        Validates that the filename is safe and stays within output_dir
        to prevent path traversal attacks from untrusted ComfyUI filenames.
        """
        try:
            # Validate that filename doesn't escape output_dir
            # Use only basename to prevent directory traversal
            safe_filename = os.path.basename(filename)
            if not safe_filename or safe_filename != filename:
                logger.warning(f"Rejected unsafe filename: {filename}")
                return None

            filepath = validate_contained_path(safe_filename, str(output_dir))

            resp = await self._client.get(
                f"{self.comfyui_base_url}/view",
                params={"filename": filename, "type": "output", "subfolder": ""},
            )
            if resp.status_code == 200:
                filepath.write_bytes(resp.content)
                return filepath
        except (ValueError, OSError) as e:
            logger.warning(f"Failed to download {filename}: {e}")
        except Exception as e:
            logger.warning(f"Failed to download {filename}: {e}")
        return None

    # ─── Public map generation API ────────────────────────────────────────────

    async def generate_map_comfyui(
        self,
        prompt: str,
        output_dir: Path,
        negative_prompt: str = "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render, text, watermark, logo, oversaturated, washed out, flat lighting, uniformly gray, featureless, empty, simplistic shapes",
        width: int = 1024,
        height: int = 768,
        steps: int = 28,
        cfg: Optional[float] = None,
        seed: int = -1,
        style: str = "fantasy_map",
    ) -> Dict[str, Any]:
        """Generate a map image via ComfyUI using SDXL.

        Optimized for dDBattlemapsSDXL checkpoint with dpmpp_3m_sde sampler.
        Higher step count (28) ensures detailed terrain, architecture, and elements.
        """
        output_dir = self._checked_output_dir(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if seed < 0:
            seed = random.getrandbits(31)

        styled_prompt = self._finish_prompt(self._STYLE_PREFIXES.get(style, self._STYLE_PREFIXES["fantasy_map"]) + prompt)
        negative_prompt = self._finish_negative(negative_prompt)
        cfg = self._resolve_cfg(cfg)

        logger.info("Map generation: using SDXL via ComfyUI")
        workflow = self._build_sdxl_workflow(
            prompt=styled_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            seed=seed,
            filename_prefix=f"map_{int(time.time())}_{uuid.uuid4().hex[:6]}",
        )

        return await self._submit_and_wait(workflow, output_dir, "map")

    async def generate_map_controlnet(
        self,
        prompt: str,
        layout_image_path: str | Path,
        output_dir: Path,
        negative_prompt: str = "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render, text, watermark, logo, oversaturated, washed out, flat lighting, uniformly gray, featureless, empty, simplistic shapes",
        width: int = 1024,
        height: int = 768,
        steps: int = 28,
        cfg: Optional[float] = None,
        seed: int = -1,
        style: str = "dungeon",
        controlnet_strength: float = 1.0,
    ) -> Dict[str, Any]:
        """Generate a map image using ControlNet layout guidance.

        The layout mask (wall/door coordinates) is used as ControlNet conditioning,
        ensuring the generated map's visual barriers align with the physical
        wall/door objects placed in Foundry.

        The layout mask is a black PNG with white lines for walls.
        Doors appear as gaps in the white wall lines.
        This tells ComfyUI where to draw walls in the final image.

        Args:
            prompt: Natural language description of the map's aesthetic/terrain
            layout_image_path: Path to the layout mask PNG (white walls on black)
            output_dir: Where to save the output map image
            width, height: Output image dimensions (pixels)
            steps, cfg, seed: Sampling parameters
            style: Map style prefix (dungeon, fantasy_map, overworld)
            controlnet_strength: How strongly the layout mask guides generation (0.0–1.0)

        Returns:
            {status: 'success'|'error', output_file: Path, provider: 'comfyui'}
        """
        health = await self.health_check()
        if not health.get("comfyui"):
            logger.warning("Map generation skipped — ComfyUI is unreachable")
            return {
                "status": "error",
                "error": "ComfyUI backend is not available",
                "provider": "none",
            }

        output_dir = self._checked_output_dir(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        layout_image_path = str(Path(layout_image_path).resolve())
        if seed < 0:
            seed = random.getrandbits(31)

        styled_prompt = self._finish_prompt(self._STYLE_PREFIXES.get(style, self._STYLE_PREFIXES["dungeon"]) + prompt)
        negative_prompt = self._finish_negative(negative_prompt)
        cfg = self._resolve_cfg(cfg)

        # Build workflow WITH ControlNet
        # The workflow includes a LoadImage node to inject the layout mask
        # at runtime (not at workflow-definition time) because LoadImage
        # requires a filename that ComfyUI resolves from its input directory.
        workflow = self._build_sdxl_workflow(
            prompt=styled_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            seed=seed,
            use_controlnet=True,
            controlnet_model=self.controlnet_model,
            layout_image_path=layout_image_path,
            controlnet_strength=controlnet_strength,
            filename_prefix=f"map_cn_{int(time.time())}_{uuid.uuid4().hex[:6]}",
        )

        # Inject the actual layout image path into the LoadImage node.
        # ComfyUI's LoadImage node expects the image path in the 'image' field,
        # resolved from its 'input' directory.
        # Strategy: copy the layout image to output_dir/layouts/ (record-keeping)
        # AND to each configured ComfyUI input directory.
        safe_layout_name = os.path.basename(str(layout_image_path))
        import shutil

        # Always copy to output_dir/layouts/ (for record-keeping)
        layout_dest = output_dir / "layouts" / safe_layout_name
        layout_dest.parent.mkdir(parents=True, exist_ok=True)
        if not layout_dest.exists() or not os.path.samefile(str(layout_dest), str(Path(layout_image_path).resolve())):
            shutil.copy2(layout_image_path, str(layout_dest))

        # Copy to ComfyUI's input/ directory so LoadImage can resolve the filename.
        # Uses configured comfyui_input_dirs or auto-detects from /system_stats.
        comfyui_input = await self._ensure_comfyui_input_dir()
        if comfyui_input:
            comfyui_input.mkdir(parents=True, exist_ok=True)
            dest = comfyui_input / safe_layout_name
            if not dest.exists():
                try:
                    shutil.copy2(layout_image_path, str(dest))
                    logger.debug(f"[Layout] Copied mask to ComfyUI input: {dest}")
                except Exception as e:
                    logger.warning(f"[Layout] Could not copy mask to ComfyUI input dir {comfyui_input}: {e}")
        else:
            logger.warning(
                "[Layout] ComfyUI input dir unknown — LoadImage will likely fail. "
                "Set COMFYUI_INPUT_DIRS in .env to fix."
            )

        logger.info(f"[Layout] ControlNet map generation: layout={safe_layout_name}")
        return await self._submit_and_wait(workflow, output_dir, "map_controlnet")

    # ── NPC portraits ──
    # SD 1.5 alone read a bare role ("a lieutenant of the Ironclad Regiment") as a modern officer or a
    # photograph, and after the 9/28 first-sentence-only prompt there was nothing left to say "fantasy". The
    # z-image turbo model gives a consistent painterly D&D look but, given only a role, draws the same face
    # every time, so each NPC gets explicit gender/age/skin/hair/mood: taken from their data or description
    # when stated, otherwise chosen deterministically from their name (the same NPC always looks the same).
    ZIMAGE = {"unet": "z_image_turbo_bf16.safetensors", "clip": "qwen_3_4b.safetensors", "vae": "ae.safetensors"}
    _ANCESTRIES = ("elf", "dwarf", "gnome", "halfling", "half-orc", "half-elf", "tiefling", "dragonborn", "goblin",
                   "orc", "kender", "minotaur", "draconian", "kobold")
    # Plurals/adjectives an LLM uses for the same people ("elven archer", "the dwarves"), beyond the bare name + "s".
    _ANCESTRY_FORMS = {"elf": "elf|elves|elven", "dwarf": "dwarf|dwarves|dwarven", "gnome": "gnome|gnomish",
                       "halfling": "halfling", "goblin": "goblin|goblinoid", "orc": "orc|orcish"}
    _FEMALE = re.compile(r"\b(she|her|woman|lady|queen|daughter|girl|mother|sister|priestess|witch|duchess|princess)\b", re.I)
    _MALE = re.compile(r"\b(he|his|him|man|lord|king|son|boy|father|brother|priest|duke|prince|sir)\b", re.I)
    _OLD = re.compile(r"\b(elderly|old|ancient|aged|grey-haired|gray-haired|venerable|veteran)\b", re.I)
    _YOUNG = re.compile(r"\b(young|youth|child|boy|girl|apprentice|squire|novice|teen\w*)\b", re.I)
    _PORTRAIT_NEGATIVE = (
        "blurry, low quality, deformed, ugly, bad anatomy, extra limbs, missing fingers, fused fingers, mutation, "
        "extra heads, poorly drawn face, disfigured, cartoon, anime, sketch, abstract, two faces, multiple faces, "
        "duplicate, multiple people, group, split image, collage, text, letters, watermark, logo, title, frame, "
        "border, poster, modern, modern clothing, t-shirt, uniform, military uniform, peaked cap, suit, necktie, "
        "glasses, contemporary, photograph, photo, sci-fi, firearm, gun, 21st century, smartphone, "
        "full body, standing, legs, feet, landscape, scenery, tarot card, ornate border, transparent background, "
        "checkerboard, engraving"
    )
    _zimage_ok: Optional[bool] = None

    @classmethod
    def _portrait_attributes(cls, name: str, description: str, npc: Optional[dict] = None) -> Dict[str, str]:
        """Who this NPC looks like. Stated facts (their data, then their description) win; the rest comes
        from a hash of the name, so a re-generated portrait keeps the same person."""
        npc = npc or {}
        h = int(hashlib.sha256((name or description).lower().encode()).hexdigest(), 16)

        def pick(options, shift):
            return options[(h >> shift) % len(options)]

        text = " ".join([description or ""] + [str(npc.get(k, "")) for k in ("race", "ancestry", "gender", "age", "appearance")])
        ancestry = next((a for a in cls._ANCESTRIES if re.search(rf"\b(?:{cls._ANCESTRY_FORMS.get(a, a)})s?\b", text, re.I)), "human")
        stated_gender = str(npc.get("gender", "")).lower()
        if stated_gender in ("male", "man", "m"):
            gender = "man"
        elif stated_gender in ("female", "woman", "f"):
            gender = "woman"
        elif cls._FEMALE.search(text) and not cls._MALE.search(text):
            gender = "woman"
        elif cls._MALE.search(text) and not cls._FEMALE.search(text):
            gender = "man"
        else:
            gender = pick(["man", "woman"], 0)
        age = "elderly" if cls._OLD.search(text) else "young" if cls._YOUNG.search(text) else pick(["young", "middle-aged", "elderly", "middle-aged"], 3)
        return {
            "ancestry": ancestry, "gender": gender, "age": age,
            "skin": pick(["pale", "fair", "olive", "tan", "brown", "dark brown"], 7),
            "hair": pick(["short black hair", "long red hair", "braided blond hair", "shaved head", "grey hair",
                          "curly brown hair", "long white hair", "dark tied-back hair"], 11),
            "mood": pick(["stern", "kind", "wary", "amused", "weary", "fierce"], 17),
        }

    @staticmethod
    def _portrait_subject(description: str) -> str:
        """The description's first sentence, at most 30 words: the rest is story text that pulls the model off the face."""
        return " ".join(re.split(r"(?<=[.!?])\s", (description or "").strip())[0].split()[:30])

    def _portrait_prompt(self, description: str, attrs: Dict[str, str], zimage: bool) -> str:
        who = f"{attrs['age']} {attrs['skin']}-skinned {attrs['ancestry']} {attrs['gender']}, {attrs['hair']}, {attrs['mood']} expression"
        subject = self._portrait_subject(description)
        if zimage:
            return (f"fantasy portrait painting, bust portrait, head and shoulders, close-up on face, {who}, {subject}, "
                    "wearing medieval fantasy attire, painterly oil illustration filling the entire canvas edge to edge, "
                    "full-bleed, no border, no frame, no white margin, dramatic lighting, simple dark background")
        return (f"high fantasy Dungeons and Dragons character portrait, extreme close-up bust, face and shoulders only, {who}, "
                f"{subject}, wearing medieval fantasy attire, hand-painted fantasy illustration, dramatic lighting, "
                "simple dark background")

    @staticmethod
    def _trim_margins(path: Path) -> bool:
        """Crop a light matte (and its soft shadow) off a generated portrait, in place. z-image sometimes paints the
        picture as a card on a white wall whatever the prompt says. A row or column belongs to the picture when at
        least a sixth of its pixels are darker than the matte; returns True when anything was cut."""
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            rgb = im.convert("RGB")
            gray = rgb.convert("L")
            w, h = gray.size
            px = gray.load()
            dark_cols = [x for x in range(w) if sum(1 for y in range(0, h, 4) if px[x, y] < 170) * 4 >= h / 6]
            dark_rows = [y for y in range(h) if sum(1 for x in range(0, w, 4) if px[x, y] < 170) * 4 >= w / 6]
            if not dark_cols or not dark_rows:
                return False
            box = (dark_cols[0], dark_rows[0], dark_cols[-1] + 1, dark_rows[-1] + 1)
            if box == (0, 0, w, h) or (box[2] - box[0]) < w * 0.5 or (box[3] - box[1]) < h * 0.5:
                return False                      # nothing to trim, or too little left to trust
            rgb.crop(box).save(path)
        return True

    async def _zimage_available(self) -> bool:
        """Does this ComfyUI have the z-image turbo model, its text encoder and VAE? (Checked once.)"""
        if MapGenerator._zimage_ok is not None:
            return MapGenerator._zimage_ok
        try:
            async def choices(node: str, field: str) -> List[str]:
                info = (await self._client.get(f"{self.comfyui_base_url}/object_info/{node}", timeout=15)).json()
                return info[node]["input"]["required"][field][0]
            ok = (self.ZIMAGE["unet"] in await choices("UNETLoader", "unet_name")
                  and self.ZIMAGE["clip"] in await choices("CLIPLoader", "clip_name")
                  and self.ZIMAGE["vae"] in await choices("VAELoader", "vae_name"))
        except Exception as e:
            logger.info(f"[Portrait] could not check for z-image ({e}); using SD 1.5")
            return False                     # not cached: a transient failure should not pin the fallback
        MapGenerator._zimage_ok = ok
        return ok

    async def generate_portrait_comfyui(
        self, prompt: str, output_dir: Path, seed: int = -1, name: str = "", npc: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Generate an NPC portrait via ComfyUI: z-image turbo when available (portrait_model auto/zimage), else SD 1.5.

        SD 1.5 runs at 512x640 (taller canvases produced stacked faces); z-image at 768x960.
        """
        output_dir = self._checked_output_dir(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if seed < 0:
            seed = random.getrandbits(31)
        npc = npc or {}
        wanted = settings.portrait_model
        use_zimage = wanted != "sd15" and (wanted == "zimage" or await self._zimage_available())
        filename_prefix = f"portrait_{int(time.time())}_{uuid.uuid4().hex[:6]}"

        if npc.get("monster"):                # a creature, not a person: no human attributes
            positive = f"{prompt}, bust portrait, fantasy creature art, painterly, dramatic lighting, simple dark background"
        else:
            positive = self._portrait_prompt(prompt, self._portrait_attributes(name, prompt, npc), zimage=use_zimage)

        if use_zimage:
            workflow = {
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": self.ZIMAGE["unet"], "weight_dtype": "default"}},
                "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": self.ZIMAGE["clip"], "type": "lumina2", "device": "default"}},
                "3": {"class_type": "VAELoader", "inputs": {"vae_name": self.ZIMAGE["vae"]}},
                "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
                "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": positive}},
                "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
                "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 768, "height": 960, "batch_size": 1}},
                "8": {"class_type": "KSampler", "inputs": {
                    "model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": seed,
                    "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
                "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
                "11": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": filename_prefix}},
            }
            logger.info("Portrait generation: using z-image turbo")
        else:
            checkpoint = "v1-5-pruned-emaonly-fp16.safetensors"
            workflow = {
                "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
                "4": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["3", 1]}},
                "5": {"class_type": "CLIPTextEncode", "inputs": {"text": self._PORTRAIT_NEGATIVE, "clip": ["3", 1]}},
                "6": {"class_type": "KSampler", "inputs": {
                    "seed": seed, "steps": 30, "cfg": 7.0, "sampler_name": "euler", "scheduler": "karras", "denoise": 1.0,
                    "model": ["3", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["7", 0]}},
                "7": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 640, "batch_size": 1}},
                "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 2]}},
                "11": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": filename_prefix}},
            }
            logger.info(f"Portrait generation: using SD 1.5 ({checkpoint})")
        result = await self._submit_and_wait(workflow, output_dir, "portrait")
        result["model"] = "zimage" if use_zimage else "sd15"
        if use_zimage and result.get("output_file"):
            try:
                if await asyncio.to_thread(self._trim_margins, Path(result["output_file"])):
                    logger.info("[Portrait] trimmed a light matte off the z-image output")
            except Exception as e:                # a keepsake crop must never lose the portrait
                logger.warning(f"[Portrait] margin trim skipped: {e}")
        return result

    async def generate_map(
        self,
        prompt: str,
        output_dir: Path,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 768,
        steps: int = 28,
        cfg: Optional[float] = None,
        seed: int = -1,
        size: str = None,
        style: str = None,
    ) -> Dict[str, Any]:
        """Generate a map image. The negative prompt defaults to the style's own.

        Checks ComfyUI health upfront and returns an error immediately if
        unreachable, avoiding cascading connection failures across all maps.
        """
        health = await self.health_check()
        if not health.get("comfyui"):
            logger.warning("Map generation skipped — ComfyUI is unreachable")
            return {
                "status": "error",
                "error": "ComfyUI backend is not available",
                "provider": "none",
            }

        # Parse size string (e.g. "1024x768") if provided
        if size:
            try:
                w, h = size.lower().split("x")
                width, height = int(w), int(h)
            except ValueError:
                pass

        if negative_prompt is None:
            negative_prompt = (
                self._BATTLEMAP_NEGATIVE if style == "battlemap"
                else "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render"
            )
        return await self.generate_map_comfyui(
            prompt=prompt,
            output_dir=output_dir,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            seed=seed,
            style=style or "fantasy_map",
        )

    async def generate_portrait(
        self, prompt: str, output_dir: Path, name: str = "", npc: Optional[dict] = None
    ) -> Dict[str, Any]:
        """Generate an NPC portrait. `name` and `npc` (their data) pick a consistent, stated-or-derived appearance."""
        health = await self.health_check()
        if not health.get("comfyui"):
            logger.warning("Portrait generation skipped — ComfyUI is unreachable")
            return {
                "status": "error",
                "error": "ComfyUI backend is not available",
                "provider": "none",
            }
        return await self.generate_portrait_comfyui(prompt, output_dir, name=name, npc=npc)

    async def generate_prologue_panel(
        self,
        prompt: str,
        vessel: str,
        output_dir: Path,
        width: int = 1344,
        height: int = 768,
        seed: int = -1,
    ) -> Dict[str, Any]:
        """Generate a single prologue panel illustration.

        Uses the vessel preset for art style + the panel's image_prompt.
        Landscape 1344x768 so panels fill a journal image page.

        Args:
            prompt: The panel's image_prompt (scene description WITHOUT style words)
            vessel: One of the vessel keys (tome, scroll, gallery, tapestry, stained_glass, mural, cartographer)
            output_dir: Directory to save the generated image
            width: Output width in pixels (default 1344 for landscape journal)
            height: Output height in pixels (default 768)
            seed: Random seed (-1 for random)

        Returns:
            Dict with status, output_file, provider, or error
        """
        health = await self.health_check()
        if not health.get("comfyui"):
            logger.warning("Prologue panel generation skipped — ComfyUI is unreachable")
            return {
                "status": "error",
                "error": "ComfyUI backend is not available",
                "provider": "none",
            }

        output_dir = self._checked_output_dir(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if seed < 0:
            seed = random.getrandbits(31)

        vessel_prefix = self._VESSEL_PREFIXES.get(vessel, self._VESSEL_PREFIXES["tome"])
        styled_prompt = vessel_prefix + prompt

        filename_prefix = f"prologue_{vessel}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        workflow = self._build_sdxl_workflow(
            prompt=styled_prompt,
            negative_prompt=(
                "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render, "
                "text, watermark, logo, oversaturated, washed out, flat lighting, "
                "uniformly gray, featureless, empty, simplistic shapes"
            ),
            width=width,
            height=height,
            steps=28,
            cfg=7.5,
            seed=seed,
            filename_prefix=filename_prefix,
            hires=False,
        )

        logger.info(f"Prologue panel generation: vessel={vessel}, {width}x{height}")
        return await self._submit_and_wait(workflow, output_dir, "prologue")

    async def generate_batch(
        self,
        prompts: List[str],
        output_dir: Path,
        provider: str = None,
    ) -> List[Dict[str, Any]]:
        """Generate multiple map images sequentially via ComfyUI."""
        output_dir = self._checked_output_dir(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for prompt in prompts:
            r = await self.generate_map(prompt, output_dir)
            r["prompt"] = prompt
            results.append(r)
        return results

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
