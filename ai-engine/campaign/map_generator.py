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
import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

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
        "fantasy_map": "high-quality fantasy top-down map, aged parchment texture with burn marks, medieval cartography style, detailed terrain features, ornate compass rose, visible grid lines, rich earth tones and forest greens, ",
        "dungeon": "professional top-down dungeon map, weathered stone corridors with dynamic lighting, flickering torchlight creating dramatic shadows, trap markers and hazards visible, scattered bones and treasure, atmospheric mist on floor, gritty parchment aesthetic with worn edges, ",
        "overworld": "stunning isometric fantasy world map, layered terrain with mountains casting shadows, dense forests with texture, winding rivers reflecting light, scattered villages and settlements, trade route markers, elegant borders, vibrant yet cohesive color palette, ",
        "portrait": "professional fantasy character portrait, digital painting quality, dramatic cinematic lighting, intricate facial features and expressions, rich clothing details, epic fantasy illustration style with atmospheric background, ",
    }

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

        gs = grid_size_px
        # CRITICAL: Always create the mask at the full requested dimensions.
        # The mask must match the scene canvas dimensions so walls align with
        # the Foundry grid. Black padding around walls is necessary to ensure
        # the ControlNet doesn't scale/center walls when upscaling to final size.
        # DO NOT shrink based on wall coordinates — that causes walls to be
        # centered in the image when ComfyUI scales up to requested dimensions.

        mask = PILImage.new("L", (width, height), 0)  # black background
        draw = ImageDraw.Draw(mask)

        def to_pixel_coords(grid_coord_list):
            """Convert grid coordinates to pixel coordinates.
            
            Normalizes grid coordinates to fit within the image dimensions.
            """
            # Find the actual grid bounds from the scene_setup
            walls = scene_setup.get("walls", [])
            max_x = max((max(seg[0], seg[2]) for seg in walls if len(seg) == 4), default=0)
            max_y = max((max(seg[1], seg[3]) for seg in walls if len(seg) == 4), default=0)
            
            # Calculate scale factors to fit within image
            scale_x = width / (max_x + 1) if max_x > 0 else 1
            scale_y = height / (max_y + 1) if max_y > 0 else 1
            scale = min(scale_x, scale_y)  # Use the smaller scale to fit both dimensions
            
            # Apply scale and convert to int
            return [int(v * scale) for v in grid_coord_list]

        # Draw all wall segments as white lines
        for seg in walls:
            if len(seg) == 4:
                x0, y0, x1, y1 = to_pixel_coords(seg)
                draw.line([(x0, y0), (x1, y1)], fill=255, width=3)

        # Draw door gaps — overlay black on wall segments where doors exist
        for door in doors:
            c_raw = door.get("c", [])
            if len(c_raw) == 4:
                x0, y0, x1, y1 = to_pixel_coords(c_raw)
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
        gs = grid_size_px
        mask = PILImage.new("L", (width, height), 0)
        from PIL import ImageDraw
        draw = ImageDraw.Draw(mask)

        def to_pixel_coords(seg):
            """Convert grid coordinates to pixel coordinates.
            
            Normalizes grid coordinates to fit within the image dimensions.
            """
            # Find the actual grid bounds from the fallback_setup
            walls = fallback_setup.get("walls", [])
            max_x = max((max(s[0], s[2]) for s in walls if len(s) == 4), default=0)
            max_y = max((max(s[1], s[3]) for s in walls if len(s) == 4), default=0)
            
            # Calculate scale factors to fit within image
            scale_x = width / (max_x + 1) if max_x > 0 else 1
            scale_y = height / (max_y + 1) if max_y > 0 else 1
            scale = min(scale_x, scale_y)  # Use the smaller scale to fit both dimensions
            
            # Apply scale and convert to int
            return [int(v * scale) for v in seg]

        # Draw walls
        for seg in fallback_setup.get("walls", []):
            if len(seg) == 4:
                x0, y0, x1, y1 = to_pixel_coords(seg)
                draw.line([(x0, y0), (x1, y1)], fill=255, width=3)

        # Draw door gaps
        for door in fallback_setup.get("doors", []):
            c_raw = door.get("c", [])
            if len(c_raw) == 4:
                x0, y0, x1, y1 = to_pixel_coords(c_raw)
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
            return {
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
            }
        else:
            # Standard text-only workflow (unchanged)
            return {
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
            }

    # ─── ComfyUI execution helpers ────────────────────────────────────────────

    async def _submit_and_wait(
        self, workflow: Dict, output_dir: Path, filename_hint: str
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
        output_file = await self._wait_for_completion(prompt_id, output_dir)
        return {
            "status": "success" if output_file else "error",
            "prompt_id": prompt_id,
            "output_file": str(output_file) if output_file else None,
            "provider": "comfyui",
        }

    async def _wait_for_completion(
        self, prompt_id: str, output_dir: Path
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
                                    if filename.endswith((".png", ".jpg", ".jpeg")):
                                        return await self._download_image(
                                            filename, output_dir
                                        )
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
        cfg: float = 7.5,
        seed: int = -1,
        style: str = "fantasy_map",
    ) -> Dict[str, Any]:
        """Generate a map image via ComfyUI using SDXL.

        Optimized for dDBattlemapsSDXL checkpoint with dpmpp_3m_sde sampler.
        Higher step count (28) ensures detailed terrain, architecture, and elements.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        if seed < 0:
            seed = random.getrandbits(31)

        styled_prompt = self._STYLE_PREFIXES.get(style, self._STYLE_PREFIXES["fantasy_map"]) + prompt

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
        cfg: float = 7.5,
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

        output_dir.mkdir(parents=True, exist_ok=True)
        layout_image_path = str(Path(layout_image_path).resolve())
        if seed < 0:
            seed = random.getrandbits(31)

        styled_prompt = self._STYLE_PREFIXES.get(style, self._STYLE_PREFIXES["dungeon"]) + prompt

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

    async def generate_portrait_comfyui(
        self, prompt: str, output_dir: Path, seed: int = -1
    ) -> Dict[str, Any]:
        """Generate an NPC portrait via ComfyUI using SD 1.5.

        Uses v1-5-pruned-emaonly for character portraits — the battlemaps SDXL
        checkpoint produces abstract/artistic results, not recognisable faces.
        SD 1.5 at 512×768 with euler/karras is well-suited for character art.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        if seed < 0:
            seed = random.getrandbits(31)

        portrait_prefix = self._STYLE_PREFIXES.get("portrait", "")
        portrait_prompt = (
            f"{portrait_prefix}{prompt}, "
            "sharp focus on face and eyes, upper body portrait, "
            "highly detailed face, realistic skin texture, correct human anatomy"
        )

        # Build a generic SD-1.5-compatible workflow: euler sampler, cfg ~7,
        # 512×768, 30 steps — same KSampler node graph as _build_sdxl_workflow
        # but with the SD 1.5 checkpoint and sampler settings.
        portrait_checkpoint = "v1-5-pruned-emaonly-fp16.safetensors"
        filename_prefix = f"portrait_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        workflow = {
            "3": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": portrait_checkpoint},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": portrait_prompt, "clip": ["3", 1]},
            },
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": (
                        "blurry, low quality, deformed, ugly, bad anatomy, extra limbs, "
                        "missing fingers, fused fingers, mutation, extra heads, poorly drawn face, "
                        "disfigured, cartoon, anime, sketch, abstract, modern"
                    ),
                    "clip": ["3", 1],
                },
            },
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": 30,
                    "cfg": 7.0,
                    "sampler_name": "euler",
                    "scheduler": "karras",
                    "denoise": 1.0,
                    "model": ["3", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["7", 0],
                },
            },
            "7": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 768, "batch_size": 1},
            },
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 2]}},
            "11": {
                "class_type": "SaveImage",
                "inputs": {"images": ["8", 0], "filename_prefix": filename_prefix},
            },
        }

        logger.info(f"Portrait generation: using SD 1.5 ({portrait_checkpoint})")
        return await self._submit_and_wait(workflow, output_dir, "portrait")

    async def generate_map(
        self,
        prompt: str,
        output_dir: Path,
        negative_prompt: str = "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render",
        width: int = 1024,
        height: int = 768,
        steps: int = 28,
        cfg: float = 7.5,
        seed: int = -1,
        size: str = None,
        style: str = None,
    ) -> Dict[str, Any]:
        """Generate a map image.

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
        self, prompt: str, output_dir: Path
    ) -> Dict[str, Any]:
        """Generate an NPC portrait."""
        health = await self.health_check()
        if not health.get("comfyui"):
            logger.warning("Portrait generation skipped — ComfyUI is unreachable")
            return {
                "status": "error",
                "error": "ComfyUI backend is not available",
                "provider": "none",
            }
        return await self.generate_portrait_comfyui(prompt, output_dir)

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
