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
            """Convert grid coordinates to pixel coordinates."""
            return [int(v * gs) for v in grid_coord_list]

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
