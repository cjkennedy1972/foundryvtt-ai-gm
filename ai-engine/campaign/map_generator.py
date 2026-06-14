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
import sys
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

    # Python interpreter that has mlx installed
    ZIMAGE_PYTHON = "/opt/homebrew/bin/python3"
    # Absolute path to the Z-Image-Turbo model directory
    ZIMAGE_MODEL_PATH = Path("/Users/ckennedy/.omlx/models/illusion615/Z-Image-Turbo-MLX")
    # Runner script path (sibling of this file)
    ZIMAGE_RUNNER = Path(__file__).parent / "zimage_runner.py"
    # LLM model ID in oMLX to unload before Z-Image generation
    LLM_MODEL_ID = "Qwen3.6-35B-A3B-UD-MLX-4bit"

    def __init__(
        self,
        comfyui_url: str = "http://127.0.0.1:18188",
        omlx_base_url: str = "http://localhost:8800",
        omlx_api_key: str = "",
        omlx_size: str = "1024x1024",
        omlx_style: str = "fantasy_map",
        timeout: int = 300,
        checkpoint_name: str = "juggernautXL_v11.safetensors",
        provider: str = "auto",  # "comfyui" | "omlx" | "auto"
        # Legacy parameter aliases kept for backwards compatibility
        omlx_url: str = "",
        omlx_model: str = "",
    ):
        self.comfyui_base_url = comfyui_url.rstrip("/")
        self.omlx_base_url = omlx_base_url.rstrip("/")
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
        """oMLX is available if the server is reachable and the Z-Image model dir exists."""
        try:
            resp = await self._client.get(f"{self.omlx_base_url}/v1/models", timeout=5)
            return resp.status_code == 200 and self.ZIMAGE_MODEL_PATH.exists()
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
    # Z-Image-Turbo-MLX cannot be served through the oMLX REST API because its
    # weights live in subdirectories (transformer/, text_encoder/, vae/) rather
    # than at the model root.  It also cannot coexist in memory with Qwen3.6-35B.
    # Strategy: unload the LLM via admin API, run the pipeline as a subprocess
    # using the mlx-capable homebrew Python, then reload the LLM.

    async def _admin_login(self) -> Optional[httpx.Cookies]:
        """Obtain an oMLX admin session cookie using the API key."""
        if not self.omlx_api_key:
            return None
        try:
            jar = httpx.Cookies()
            resp = await self._client.post(
                f"{self.omlx_base_url}/admin/api/login",
                json={"api_key": self.omlx_api_key},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json().get("success"):
                for k, v in resp.cookies.items():
                    jar.set(k, v)
                return jar
        except Exception as e:
            logger.warning(f"oMLX admin login failed: {e}")
        return None

    async def _admin_model_action(self, action: str, cookies: httpx.Cookies) -> bool:
        """Call /admin/api/models/<LLM_MODEL_ID>/<action> (load or unload)."""
        try:
            resp = await self._client.post(
                f"{self.omlx_base_url}/admin/api/models/{self.LLM_MODEL_ID}/{action}",
                cookies=cookies,
                json={},
                timeout=60,
            )
            data = resp.json()
            ok = data.get("status") == "ok" or data.get("message", "").lower().startswith(action)
            if not ok:
                logger.warning(f"oMLX model {action} returned: {data}")
            return ok
        except Exception as e:
            logger.warning(f"oMLX model {action} failed: {e}")
            return False

    async def _run_zimage_subprocess(
        self,
        prompt: str,
        output_path: Path,
        width: int = 768,
        height: int = 768,
        num_steps: int = 8,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run Z-Image generation in a subprocess using the mlx Python."""
        cmd = [
            self.ZIMAGE_PYTHON, str(self.ZIMAGE_RUNNER),
            prompt, str(output_path),
            str(width), str(height), str(num_steps),
        ]
        if seed is not None:
            cmd.append(str(seed))

        logger.info(f"Z-Image subprocess: {width}x{height} {num_steps} steps")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            if proc.returncode != 0:
                err = stderr.decode(errors="replace")[-500:]
                return {"status": "error", "error": f"Z-Image subprocess failed: {err}", "provider": "omlx"}

            result = json.loads(stdout.decode().strip())
            result["provider"] = "omlx"
            return result
        except asyncio.TimeoutError:
            return {"status": "error", "error": "Z-Image subprocess timed out", "provider": "omlx"}
        except Exception as e:
            return {"status": "error", "error": str(e), "provider": "omlx"}

    async def generate_map_omlx(
        self,
        prompt: str,
        output_dir: Path,
        size: str = None,
        style: str = None,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 768,
        num_steps: int = 8,
    ) -> Dict[str, Any]:
        """Generate a map image via Z-Image-Turbo-MLX subprocess."""
        output_dir.mkdir(parents=True, exist_ok=True)
        style = style or self.omlx_style

        style_prefixes = {
            "fantasy_map": "top-down fantasy map, parchment texture, medieval cartography style, detailed terrain, ",
            "dungeon": "top-down dungeon map, dark stone corridors, torchlight, traps, treasure, grid map, ",
            "portrait": "fantasy character portrait, digital painting, dramatic lighting, detailed face, epic style, ",
            "overworld": "isometric fantasy world map, mountains, forests, rivers, towns, trade routes, elegant cartography, ",
        }
        full_prompt = style_prefixes.get(style, style_prefixes["fantasy_map"]) + prompt

        filename = f"map_{int(time.time())}.png"
        output_path = output_dir / filename

        cookies = await self._admin_login()
        llm_unloaded = False
        if cookies:
            llm_unloaded = await self._admin_model_action("unload", cookies)
            if llm_unloaded:
                logger.info(f"Unloaded {self.LLM_MODEL_ID} to free memory for Z-Image")
            else:
                logger.warning("Could not unload LLM — Z-Image generation may fail due to memory pressure")

        result = await self._run_zimage_subprocess(
            prompt=full_prompt,
            output_path=output_path,
            width=width,
            height=height,
            num_steps=num_steps,
        )

        # Always reload the LLM regardless of generation outcome
        if cookies and llm_unloaded:
            reloaded = await self._admin_model_action("load", cookies)
            if reloaded:
                logger.info(f"Reloaded {self.LLM_MODEL_ID}")
            else:
                logger.warning(f"Failed to reload {self.LLM_MODEL_ID} after Z-Image generation")

        if result.get("status") == "success":
            result["output_file"] = result.get("image_path") or str(output_path)
            result["prompt_id"] = None
        return result

    async def generate_portrait_omlx(
        self, prompt: str, output_dir: Path
    ) -> Dict[str, Any]:
        """Generate an NPC portrait via Z-Image-Turbo-MLX subprocess."""
        output_dir.mkdir(parents=True, exist_ok=True)
        full_prompt = f"fantasy character portrait, digital painting, detailed face, dramatic lighting, epic style, {prompt}"
        filename = f"portrait_{int(time.time())}.png"
        output_path = output_dir / filename

        cookies = await self._admin_login()
        llm_unloaded = False
        if cookies:
            llm_unloaded = await self._admin_model_action("unload", cookies)
            if llm_unloaded:
                logger.info(f"Unloaded {self.LLM_MODEL_ID} for portrait generation")

        result = await self._run_zimage_subprocess(
            prompt=full_prompt,
            output_path=output_path,
            width=512,
            height=768,
            num_steps=8,
        )

        if cookies and llm_unloaded:
            await self._admin_model_action("load", cookies)

        if result.get("status") == "success":
            result["output_file"] = result.get("image_path") or str(output_path)
            result["prompt_id"] = None
        return result

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
