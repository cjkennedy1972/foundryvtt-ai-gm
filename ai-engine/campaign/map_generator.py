"""
Campaign Map Generator — Generate fantasy map images via ComfyUI or oMLX Z-Image-Turbo.

Uses either:
- ComfyUI: Stable Diffusion/Flux workflows for map/portrait generation
- oMLX Z-Image-Turbo: MLX-based image generation (local, fast)

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
    """Generate fantasy maps via ComfyUI or oMLX REST APIs."""

    # Default ComfyUI workflow for top-down maps
    MAP_WORKFLOW = {
        "3": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "juggernautXL_v11.safetensors"
            }
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "dark fantasy top-down map, parchment texture, old world map, fantasy terrain, roads, buildings, mountains, rivers, forests, detailed, intricate"
            }
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render"
            }
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": -1,
                "steps": 30,
                "cfg": 8.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["3", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["7", 0]
            }
        },
        "7": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": 1024,
                "height": 1024,
                "batch_size": 1
            }
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["6", 0]
            }
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename": "map_[timestamp]",
                "images": ["8", 0]
            }
        }
    }

    # Portrait/note icon workflow (slightly different aspect ratio)
    PORTRAIT_WORKFLOW = {
        "3": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "juggernautXL_v11.safetensors"}
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "fantasy portrait, character art, digital painting, detailed face, dramatic lighting, epic fantasy style"}
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry, low quality, modern, photorealistic, anime, cartoon, deformed, ugly, bad anatomy"}
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": -1, "steps": 25, "cfg": 7.5, "sampler_name": "euler",
                "scheduler": "normal", "denoise": 1.0,
                "model": ["3", 0], "positive": ["4", 0], "negative": ["5", 0],
                "latent_image": ["7", 0]
            }
        },
        "7": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 768, "batch_size": 1}
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename": "portrait_[timestamp]", "images": ["8", 0]}}
    }

    def __init__(
        self,
        comfyui_url: str = "http://127.0.0.1:18188",
        omlx_url: str = "http://localhost:8800/v1/images/generations",
        omlx_model: str = "Z-Image-Turbo",
        omlx_api_key: str = "",
        omlx_size: str = "1024x1024",
        omlx_style: str = "fantasy_map",
        timeout: int = 300,
        checkpoint_name: str = "juggernautXL_v11.safetensors",
        provider: str = "auto",  # "comfyui" | "omlx" | "auto"
    ):
        self.comfyui_base_url = comfyui_url.rstrip("/")
        self.omlx_url = omlx_url.rstrip("/")
        self.omlx_model = omlx_model
        self.omlx_api_key = omlx_api_key
        self.omlx_size = omlx_size
        self.omlx_style = omlx_style
        self.timeout = timeout
        self.checkpoint_name = checkpoint_name
        self.provider = provider
        self._client = httpx.AsyncClient(timeout=timeout)
        self._client_id = hashlib.sha256(os.urandom(32)).hexdigest()[:16]

    async def health_check(self) -> Dict[str, bool]:
        """Check availability of both generation backends."""
        return {
            "comfyui": await self._comfyui_healthy(),
            "omlx": await self._omlx_healthy(),
        }

    async def _comfyui_healthy(self) -> bool:
        try:
            resp = await self._client.get(f"{self.comfyui_base_url}/system_stats")
            return resp.status_code == 200
        except Exception:
            return False

    async def _omlx_healthy(self) -> bool:
        try:
            payload = {"model": self.omlx_model, "prompt": "test", "size": self.omlx_size, "n": 1}
            headers = {"Authorization": f"Bearer {self.omlx_api_key}"} if self.omlx_api_key else {}
            resp = await self._client.post(self.omlx_url, json=payload, headers=headers, timeout=30)
            return resp.status_code == 200
        except Exception:
            return False

    async def _choose_provider(self) -> str:
        """Auto-select the best available provider."""
        if self.provider == "omlx":
            return "omlx"
        if self.provider == "comfyui":
            return "comfyui"
        # Auto: prefer whichever is healthy
        health = await self.health_check()
        if health["comfyui"]:
            return "comfyui"
        if health["omlx"]:
            return "omlx"
        return "comfyui"  # fallback

    # ─── ComfyUI methods ────────────────────────────────────────────────────

    async def generate_map_comfyui(
        self,
        prompt: str,
        output_dir: Path,
        negative_prompt: str = "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render",
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        cfg: float = 8.0,
        seed: int = -1,
    ) -> Dict[str, Any]:
        """Generate a single map image via ComfyUI."""
        output_dir.mkdir(parents=True, exist_ok=True)

        workflow = {
            "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self.checkpoint_name}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {
                "text": f"top-down fantasy map, parchment style, old world map, {prompt}, detailed terrain, roads, buildings, mountains, rivers, forests, intricate, high quality"
            }},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt}},
            "6": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler",
                "scheduler": "normal", "denoise": 1.0,
                "model": ["3", 0], "positive": ["4", 0], "negative": ["5", 0],
                "latent_image": ["7", 0]
            }},
            "7": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename": f"map_{int(time.time())}", "images": ["8", 0]}}
        }

        resp = await self._client.post(
            f"{self.comfyui_base_url}/prompt",
            json={"prompt": workflow, "client_id": self._client_id},
        )
        if resp.status_code != 200:
            return {"status": "error", "prompt_id": None, "error": resp.text}

        prompt_id = resp.json().get("prompt_id")
        output_file = await self._wait_for_completion(prompt_id, output_dir)

        return {
            "status": "success" if output_file else "error",
            "prompt_id": prompt_id,
            "output_file": str(output_file) if output_file else None,
            "provider": "comfyui",
        }

    async def _wait_for_completion(self, prompt_id: str, output_dir: Path) -> Optional[Path]:
        """Poll ComfyUI history until the prompt completes."""
        max_wait = self.timeout
        start = time.time()

        while time.time() - start < max_wait:
            try:
                resp = await self._client.get(f"{self.comfyui_base_url}/history/{prompt_id}")
                if resp.status_code == 200:
                    history = resp.json()
                    entry = history.get(prompt_id)
                    if entry:
                        status = entry.get("status", {})
                        if status.get("status_str") == "success":
                            outputs = entry.get("outputs", {})
                            for node_id, node_output in outputs.items():
                                images = node_output.get("images", [])
                                for img in images:
                                    filename = img.get("filename", "")
                                    if filename.endswith(('.png', '.jpg', '.jpeg')):
                                        img_path = await self._download_image(filename, output_dir)
                                        if img_path:
                                            return img_path

                        if status.get("status_str") == "error":
                            logger.warning(f"ComfyUI error for prompt {prompt_id}")
                            return None

            except Exception as e:
                logger.debug(f"History poll failed: {e}")

            await asyncio.sleep(2)

        logger.warning(f"Timeout waiting for prompt {prompt_id}")
        return None

    async def _download_image(self, filename: str, output_dir: Path) -> Optional[Path]:
        """Download an image from ComfyUI's output folder."""
        try:
            resp = await self._client.get(
                f"{self.comfyui_base_url}/view?filename={filename}&type=output&subfolder="
            )
            if resp.status_code == 200:
                filepath = output_dir / filename
                filepath.write_bytes(resp.content)
                return filepath
        except Exception as e:
            logger.warning(f"Failed to download image {filename}: {e}")
        return None

    async def generate_portrait_comfyui(
        self, prompt: str, output_dir: Path, seed: int = -1
    ) -> Dict[str, Any]:
        """Generate an NPC portrait image via ComfyUI."""
        output_dir.mkdir(parents=True, exist_ok=True)

        workflow = {
            "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self.checkpoint_name}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {
                "text": f"fantasy portrait, character art, digital painting, {prompt}, detailed face, dramatic lighting, epic fantasy style, high quality"
            }},
            "5": {"class_type": "CLIPTextEncode", "inputs": {
                "text": "blurry, low quality, modern, photorealistic, anime, cartoon, deformed, ugly, bad anatomy"
            }},
            "6": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": 25, "cfg": 7.5, "sampler_name": "euler",
                "scheduler": "normal", "denoise": 1.0,
                "model": ["3", 0], "positive": ["4", 0], "negative": ["5", 0],
                "latent_image": ["7", 0]
            }},
            "7": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename": f"portrait_{int(time.time())}", "images": ["8", 0]}}
        }

        resp = await self._client.post(
            f"{self.comfyui_base_url}/prompt",
            json={"prompt": workflow, "client_id": self._client_id},
        )
        if resp.status_code != 200:
            return {"status": "error", "prompt_id": None, "error": resp.text}

        prompt_id = resp.json().get("prompt_id")
        output_file = await self._wait_for_completion(prompt_id, output_dir)

        return {
            "status": "success" if output_file else "error",
            "prompt_id": prompt_id,
            "output_file": str(output_file) if output_file else None,
            "provider": "comfyui",
        }

    async def generate_batch_comfyui(
        self,
        prompts: List[str],
        output_dir: Path,
        steps: int = 28,
        cfg: float = 8.0,
    ) -> List[Dict[str, Any]]:
        """Generate multiple maps via ComfyUI (queued)."""
        output_dir.mkdir(parents=True, exist_ok=True)
        prompt_ids = []
        for prompt in prompts:
            workflow = self._build_map_workflow(prompt, 1024, 1024, steps, cfg)
            resp = await self._client.post(
                f"{self.comfyui_base_url}/prompt",
                json={"prompt": workflow, "client_id": self._client_id},
            )
            if resp.status_code == 200:
                prompt_ids.append(resp.json().get("prompt_id"))
            else:
                prompt_ids.append(None)

        results = []
        for pid in prompt_ids:
            if pid is None:
                results.append({"status": "error", "prompt_id": None, "error": "submission failed", "provider": "comfyui"})
            else:
                output_file = await self._wait_for_completion(pid, output_dir)
                results.append({
                    "status": "success" if output_file else "error",
                    "prompt_id": pid,
                    "output_file": str(output_file) if output_file else None,
                    "provider": "comfyui",
                })
        return results

    def _build_map_workflow(self, prompt: str, width: int, height: int, steps: int, cfg: float) -> Dict:
        return {
            "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self.checkpoint_name}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {
                "text": f"top-down fantasy map, parchment style, old world map, {prompt}, detailed terrain, roads, buildings, mountains, rivers, forests, intricate, high quality"
            }},
            "5": {"class_type": "CLIPTextEncode", "inputs": {
                "text": "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render"
            }},
            "6": {"class_type": "KSampler", "inputs": {
                "seed": -1, "steps": steps, "cfg": cfg, "sampler_name": "euler",
                "scheduler": "normal", "denoise": 1.0,
                "model": ["3", 0], "positive": ["4", 0], "negative": ["5", 0],
                "latent_image": ["7", 0]
            }},
            "7": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename": f"map_{int(time.time())}", "images": ["8", 0]}},
        }

    # ─── oMLX Z-Image-Turbo methods ──────────────────────────────────────────

    async def generate_map_omlx(
        self,
        prompt: str,
        output_dir: Path,
        size: str = None,
        style: str = None,
        negative_prompt: str = "",
    ) -> Dict[str, Any]:
        """Generate a map image via oMLX Z-Image-Turbo."""
        output_dir.mkdir(parents=True, exist_ok=True)
        size = size or self.omlx_size
        style = style or self.omlx_style

        style_map = {
            "fantasy_map": "top-down fantasy map, parchment texture, intricate dungeons and cities, medieval style, detailed terrain",
            "dungeon": "top-down dungeon map, dark corridors, torchlight, stone walls, traps, treasure",
            "portrait": "fantasy character portrait, digital painting, detailed face, dramatic lighting, epic style",
            "overworld": "isometric fantasy world map, mountains, forests, rivers, towns, trade routes, elegant cartography",
        }
        style_text = style_map.get(style, style_map["fantasy_map"])

        payload = {
            "model": self.omlx_model,
            "prompt": f"{style_text}, {prompt}",
            "size": size,
            "n": 1,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        headers = {}
        if self.omlx_api_key:
            headers["Authorization"] = f"Bearer {self.omlx_api_key}"

        resp = await self._client.post(self.omlx_url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code != 200:
            return {"status": "error", "prompt_id": None, "error": resp.text, "provider": "omlx"}

        result = resp.json()

        # Handle different response formats (OpenAI-compatible or direct)
        if "data" in result and isinstance(result["data"], list):
            # OpenAI-compatible: {"data": [{"b64_json": "...", ...}]}
            image_data = result["data"][0].get("b64_json") or result["data"][0].get("url")
            if image_data:
                filename = f"map_{int(time.time())}.png"
                filepath = output_dir / filename
                if image_data.startswith("data:"):
                    # data URL — strip prefix
                    image_data = image_data.split(",", 1)[1]
                filepath.write_bytes(self._b64_to_bytes(image_data))
                return {
                    "status": "success",
                    "prompt_id": None,
                    "output_file": str(filepath),
                    "provider": "omlx",
                }

        # Direct image URL
        url = result.get("url") or result.get("images", [{}])[0].get("url")
        if url:
            try:
                img_resp = await self._client.get(url)
                if img_resp.status_code == 200:
                    filename = f"map_{int(time.time())}.png"
                    filepath = output_dir / filename
                    filepath.write_bytes(img_resp.content)
                    return {
                        "status": "success",
                        "prompt_id": None,
                        "output_file": str(filepath),
                        "provider": "omlx",
                    }
            except Exception as e:
                return {"status": "error", "prompt_id": None, "error": str(e), "provider": "omlx"}

        return {"status": "error", "prompt_id": None, "error": "Unexpected oMLX response format", "provider": "omlx"}

    async def generate_portrait_omlx(
        self, prompt: str, output_dir: Path
    ) -> Dict[str, Any]:
        """Generate an NPC portrait via oMLX Z-Image-Turbo."""
        output_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "model": self.omlx_model,
            "prompt": f"fantasy portrait, digital painting, {prompt}, detailed face, dramatic lighting, epic fantasy style",
            "size": self.omlx_size,
            "n": 1,
        }

        headers = {}
        if self.omlx_api_key:
            headers["Authorization"] = f"Bearer {self.omlx_api_key}"

        resp = await self._client.post(self.omlx_url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code != 200:
            return {"status": "error", "prompt_id": None, "error": resp.text, "provider": "omlx"}

        result = resp.json()
        if "data" in result and isinstance(result["data"], list):
            image_data = result["data"][0].get("b64_json") or result["data"][0].get("url")
            if image_data:
                filename = f"portrait_{int(time.time())}.png"
                filepath = output_dir / filename
                if isinstance(image_data, str) and image_data.startswith("data:"):
                    image_data = image_data.split(",", 1)[1]
                filepath.write_bytes(self._b64_to_bytes(image_data))
                return {"status": "success", "prompt_id": None, "output_file": str(filepath), "provider": "omlx"}

        return {"status": "error", "prompt_id": None, "error": "Unexpected oMLX response", "provider": "omlx"}

    # ─── Unified API ──────────────────────────────────────────────────────────

    async def generate_map(
        self,
        prompt: str,
        output_dir: Path,
        negative_prompt: str = "blurry, low quality, modern, photorealistic, anime, cartoon, 3d render",
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        cfg: float = 8.0,
        seed: int = -1,
        size: str = None,
        style: str = None,
    ) -> Dict[str, Any]:
        """Generate a map image, auto-selecting the best available backend.

        Returns an error result immediately if no backend is reachable,
        avoiding cascading connection-refused failures across all maps.
        """
        health = await self.health_check()
        if self.provider == "omlx" or (self.provider == "auto" and health.get("omlx")):
            logger.info(f"Map generation: using omlx for prompt='{prompt[:60]}...'")
            return await self.generate_map_omlx(
                prompt, output_dir, size=size, style=style, negative_prompt=negative_prompt
            )
        if self.provider == "comfyui" or (self.provider == "auto" and health.get("comfyui")):
            logger.info(f"Map generation: using comfyui for prompt='{prompt[:60]}...'")
            return await self.generate_map_comfyui(
                prompt, output_dir, negative_prompt=negative_prompt,
                width=width, height=height, steps=steps, cfg=cfg, seed=seed,
            )
        logger.warning("Map generation skipped — no image backend available (ComfyUI and oMLX both unreachable)")
        return {"status": "error", "error": "No image backend available", "provider": "none"}

    async def generate_portrait(
        self, prompt: str, output_dir: Path
    ) -> Dict[str, Any]:
        """Generate an NPC portrait, auto-selecting the best backend."""
        provider = await self._choose_provider()
        logger.info(f"Portrait generation: using {provider} for prompt='{prompt[:60]}...'")

        if provider == "omlx":
            return await self.generate_portrait_omlx(prompt, output_dir)
        return await self.generate_portrait_comfyui(prompt, output_dir)

    async def generate_batch(
        self,
        prompts: List[str],
        output_dir: Path,
        provider: str = None,
    ) -> List[Dict[str, Any]]:
        """Generate multiple images, auto-selecting the best backend."""
        provider = provider or self._choose_provider()
        logger.info(f"Batch generation ({len(prompts)} images): using {provider}")

        output_dir.mkdir(parents=True, exist_ok=True)

        if provider == "omlx":
            results = []
            for prompt in prompts:
                r = await self.generate_map_omlx(prompt, output_dir)
                r["prompt"] = prompt
                results.append(r)
            return results

        # ComfyUI batch
        results = await self.generate_batch_comfyui(prompts, output_dir)
        for i, r in enumerate(results):
            r["prompt"] = prompts[i] if i < len(prompts) else ""
        return results

    # ─── Utilities ────────────────────────────────────────────────────────────

    @staticmethod
    def _b64_to_bytes(data: str) -> bytes:
        import base64
        return base64.b64decode(data)

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
