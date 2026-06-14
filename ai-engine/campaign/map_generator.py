"""
Campaign Map Generator — Generate fantasy map images via ComfyUI.

Supports two ComfyUI workflows selected automatically at generation time:

1. Z-Image-Turbo (preferred) — uses the UNETLoader + CLIPLoader (qwen_image)
   + VAELoader + TextEncodeZImageOmni + SamplerCustomAdvanced pipeline when the
   required model files are present in ComfyUI.

2. SDXL fallback — standard CheckpointLoaderSimple + KSampler workflow used
   when Z-Image model files are not available.

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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class MapGenerator:
    """Generate fantasy maps via ComfyUI, preferring Z-Image-Turbo when available."""

    # ── Z-Image-Turbo model file names (must match ComfyUI model registry) ──
    ZIMAGE_UNET = "z_image_turbo_bf16.safetensors"
    ZIMAGE_CLIP = "qwen_3_4b.safetensors"
    ZIMAGE_VAE = "ae.safetensors"

    # ── Default SDXL fallback checkpoint ──
    SDXL_CHECKPOINT = "dDBattlemapsSDXL10_upscaleV10.safetensors"

    def __init__(
        self,
        comfyui_url: str = "http://127.0.0.1:18188",
        timeout: int = 300,
        checkpoint_name: str = "",
        provider: str = "auto",
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
        self._zimage_cache: Optional[bool] = None  # memoize availability check

    # ─── Health / availability ────────────────────────────────────────────────

    async def health_check(self) -> Dict[str, bool]:
        """Check ComfyUI availability."""
        comfyui_ok = await self._comfyui_healthy()
        return {"comfyui": comfyui_ok, "omlx": False}

    async def _comfyui_healthy(self) -> bool:
        try:
            resp = await self._client.get(
                f"{self.comfyui_base_url}/system_stats", timeout=5
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def _zimage_available(self) -> bool:
        """Return True if Z-Image-Turbo model files are registered in ComfyUI."""
        if self._zimage_cache is not None:
            return self._zimage_cache
        try:
            resp = await self._client.get(
                f"{self.comfyui_base_url}/object_info/UNETLoader", timeout=10
            )
            if resp.status_code != 200:
                self._zimage_cache = False
                return False
            unet_opts = (
                resp.json()
                .get("UNETLoader", {})
                .get("input", {})
                .get("required", {})
                .get("unet_name", [[]])[0]
            )
            self._zimage_cache = self.ZIMAGE_UNET in unet_opts
        except Exception:
            self._zimage_cache = False
        return self._zimage_cache

    # ─── Z-Image-Turbo workflow ───────────────────────────────────────────────

    def _build_zimage_workflow(
        self,
        prompt: str,
        width: int,
        height: int,
        steps: int,
        seed: int,
        filename_prefix: str = "zimage",
    ) -> Dict:
        """Build a ComfyUI workflow for Z-Image-Turbo (flow-matching pipeline).

        Node graph:
            UNETLoader ──────────────────────────┐
            CLIPLoader → TextEncodeZImageOmni → BasicGuider ──┐
            VAELoader ──────────────────────────────────────── VAEDecode → SaveImage
                                                   ↑
            RandomNoise → SamplerCustomAdvanced ───┘
            KSamplerSelect ────────────────────────┘
            BasicScheduler ────────────────────────┘
            EmptyLatentImage ──────────────────────┘
        """
        return {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {
                    "unet_name": self.ZIMAGE_UNET,
                    "weight_dtype": "default",
                },
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": self.ZIMAGE_CLIP,
                    "type": "qwen_image",
                },
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": self.ZIMAGE_VAE},
            },
            "4": {
                "class_type": "TextEncodeZImageOmni",
                "inputs": {
                    "clip": ["2", 0],
                    "prompt": prompt,
                    "auto_resize_images": True,
                },
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "6": {
                "class_type": "RandomNoise",
                "inputs": {"noise_seed": seed},
            },
            "7": {
                "class_type": "BasicGuider",
                "inputs": {
                    "model": ["1", 0],
                    "conditioning": ["4", 0],
                },
            },
            "8": {
                "class_type": "KSamplerSelect",
                "inputs": {"sampler_name": "euler"},
            },
            "9": {
                "class_type": "BasicScheduler",
                "inputs": {
                    "model": ["1", 0],
                    "scheduler": "simple",
                    "steps": steps,
                    "denoise": 1.0,
                },
            },
            "10": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["6", 0],
                    "guider": ["7", 0],
                    "sampler": ["8", 0],
                    "sigmas": ["9", 0],
                    "latent_image": ["5", 0],
                },
            },
            "11": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["10", 0], "vae": ["3", 0]},
            },
            "12": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["11", 0],
                    "filename_prefix": filename_prefix,
                },
            },
        }

    # ─── SDXL fallback workflow ───────────────────────────────────────────────

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
    ) -> Dict:
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
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["3", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["7", 0],
                },
            },
            "7": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 2]}},
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["8", 0],
                    "filename_prefix": filename_prefix,
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
        """Download an image from ComfyUI's output folder."""
        try:
            resp = await self._client.get(
                f"{self.comfyui_base_url}/view",
                params={"filename": filename, "type": "output", "subfolder": ""},
            )
            if resp.status_code == 200:
                filepath = output_dir / filename
                filepath.write_bytes(resp.content)
                return filepath
        except Exception as e:
            logger.warning(f"Failed to download {filename}: {e}")
        return None

    # ─── Public map generation API ────────────────────────────────────────────

    async def generate_map_comfyui(
        self,
        prompt: str,
        output_dir: Path,
        negative_prompt: str = "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render",
        width: int = 1024,
        height: int = 768,
        steps: int = 8,
        cfg: float = 1.0,
        seed: int = -1,
        style: str = "fantasy_map",
    ) -> Dict[str, Any]:
        """Generate a map image via ComfyUI.

        Uses Z-Image-Turbo workflow when available; falls back to SDXL.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        if seed < 0:
            seed = int(time.time()) % (2**31)

        style_prefixes = {
            "fantasy_map": "top-down fantasy map, parchment texture, medieval cartography, detailed terrain, ",
            "dungeon": "top-down dungeon map, stone corridors, torchlight, traps, treasure, grid, ",
            "overworld": "isometric fantasy world map, mountains, forests, rivers, towns, trade routes, elegant cartography, ",
            "portrait": "fantasy character portrait, digital painting, dramatic lighting, detailed face, epic style, ",
        }
        styled_prompt = style_prefixes.get(style, style_prefixes["fantasy_map"]) + prompt

        if await self._zimage_available():
            logger.info("Map generation: using Z-Image-Turbo via ComfyUI")
            workflow = self._build_zimage_workflow(
                prompt=styled_prompt,
                width=width,
                height=height,
                steps=steps,
                seed=seed,
                filename_prefix=f"map_{int(time.time())}",
            )
        else:
            logger.info("Map generation: Z-Image not available, using SDXL via ComfyUI")
            workflow = self._build_sdxl_workflow(
                prompt=styled_prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                steps=max(steps, 20),
                cfg=cfg if cfg > 1.0 else 7.5,
                seed=seed,
                filename_prefix=f"map_{int(time.time())}",
            )

        return await self._submit_and_wait(workflow, output_dir, "map")

    async def generate_portrait_comfyui(
        self, prompt: str, output_dir: Path, seed: int = -1
    ) -> Dict[str, Any]:
        """Generate an NPC portrait via ComfyUI."""
        output_dir.mkdir(parents=True, exist_ok=True)
        if seed < 0:
            seed = int(time.time()) % (2**31)

        portrait_prompt = (
            f"fantasy character portrait, digital painting, {prompt}, "
            "detailed face, dramatic lighting, epic fantasy style, high quality"
        )

        if await self._zimage_available():
            logger.info("Portrait generation: using Z-Image-Turbo via ComfyUI")
            workflow = self._build_zimage_workflow(
                prompt=portrait_prompt,
                width=512,
                height=768,
                steps=8,
                seed=seed,
                filename_prefix=f"portrait_{int(time.time())}",
            )
        else:
            logger.info("Portrait generation: using SDXL via ComfyUI")
            workflow = self._build_sdxl_workflow(
                prompt=portrait_prompt,
                negative_prompt="blurry, low quality, modern, photorealistic, anime, cartoon, deformed, ugly, bad anatomy",
                width=512,
                height=768,
                steps=25,
                cfg=7.5,
                seed=seed,
                filename_prefix=f"portrait_{int(time.time())}",
            )

        return await self._submit_and_wait(workflow, output_dir, "portrait")

    async def generate_map(
        self,
        prompt: str,
        output_dir: Path,
        negative_prompt: str = "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render",
        width: int = 1024,
        height: int = 768,
        steps: int = 8,
        cfg: float = 1.0,
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
