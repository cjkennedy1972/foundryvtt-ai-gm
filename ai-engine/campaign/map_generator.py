"""
Campaign Map Generator — Generate fantasy map images via ComfyUI.

Uses Stable Diffusion/Flux workflows to create:
- Top-down dungeon maps (combat-scale)
- Exploration maps (overworld, village, city scale)
- Portrait images (NPC headshots, NPC art)

Integration with ComfyUI via REST API:
  POST /prompt — submit workflow
  GET /history/{id} — check results
  GET /view — download output images
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
    """Generate fantasy maps via ComfyUI REST API."""

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
        timeout: int = 300,
        checkpoint_name: str = "juggernautXL_v11.safetensors",
    ):
        self.base_url = comfyui_url.rstrip("/")
        self.timeout = timeout
        self.checkpoint_name = checkpoint_name
        self._client = httpx.AsyncClient(timeout=timeout)
        self._client_id = hashlib.sha256(os.urandom(32)).hexdigest()[:16]

    async def health_check(self) -> bool:
        """Check if ComfyUI server is reachable."""
        try:
            resp = await self._client.get(f"{self.base_url}/system_stats")
            return resp.status_code == 200
        except Exception:
            return False

    async def get_models(self) -> List[str]:
        """List available checkpoint models."""
        try:
            resp = await self._client.get(f"{self.base_url}/models/checkpoints")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return []

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
    ) -> Dict[str, Any]:
        """Generate a single map image.

        Args:
            prompt: Positive prompt describing the map
            output_dir: Directory to save output image
            negative_prompt: Negative prompt to avoid unwanted styles
            width: Image width in pixels
            height: Image height in pixels
            steps: KSampler steps
            cfg: Classifier-free guidance scale
            seed: Random seed (-1 = random)

        Returns:
            Dict with 'status', 'prompt_id', 'output_file' keys
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        workflow = {
            "3": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self.checkpoint_name}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": f"top-down fantasy map, parchment style, old world map, {prompt}, detailed terrain, roads, buildings, mountains, rivers, forests, intricate, high quality"}
            },
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative_prompt}
            },
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler",
                    "scheduler": "normal", "denoise": 1.0,
                    "model": ["3", 0], "positive": ["4", 0], "negative": ["5", 0],
                    "latent_image": ["7", 0]
                }
            },
            "7": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1}
            },
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0]}},
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename": f"map_{int(time.time())}", "images": ["8", 0]}
            }
        }

        # Submit workflow
        prompt_payload = {
            "prompt": workflow,
            "client_id": self._client_id,
        }

        resp = await self._client.post(f"{self.base_url}/prompt", json=prompt_payload)
        if resp.status_code != 200:
            return {"status": "error", "prompt_id": None, "error": resp.text}

        result = resp.json()
        prompt_id = result.get("prompt_id")

        # Wait for completion by polling history
        output_file = await self._wait_for_completion(prompt_id, output_dir)

        return {
            "status": "success" if output_file else "error",
            "prompt_id": prompt_id,
            "output_file": str(output_file) if output_file else None,
        }

    async def generate_batch(
        self,
        prompts: List[str],
        output_dir: Path,
        steps: int = 28,
        cfg: float = 8.0,
        concurrent: int = 1,
    ) -> List[Dict[str, Any]]:
        """Generate multiple maps with different prompts.

        Args:
            prompts: List of map prompts
            output_dir: Output directory
            steps: KSampler steps
            cfg: CFG scale
            concurrent: Number of parallel generations (server-dependent)

        Returns:
            List of result dicts
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Submit all workflows (ComfyUI queues them automatically)
        prompt_ids = []
        for i, prompt in enumerate(prompts):
            workflow = self._build_map_workflow(prompt, 1024, 1024, steps, cfg)
            prompt_payload = {
                "prompt": workflow,
                "client_id": self._client_id,
            }
            resp = await self._client.post(f"{self.base_url}/prompt", json=prompt_payload)
            if resp.status_code == 200:
                result = resp.json()
                prompt_ids.append(result.get("prompt_id"))
            else:
                prompt_ids.append(None)
                logger.warning(f"Failed to submit map generation {i}: {resp.text}")

        # Poll for all completions
        results = []
        for pid in prompt_ids:
            if pid is None:
                results.append({"status": "error", "prompt_id": None, "error": "submission failed"})
            else:
                output_file = await self._wait_for_completion(pid, output_dir)
                results.append({
                    "status": "success" if output_file else "error",
                    "prompt_id": pid,
                    "output_file": str(output_file) if output_file else None,
                })

        return results

    def _build_map_workflow(
        self, prompt: str, width: int, height: int, steps: int, cfg: float
    ) -> Dict:
        """Build a map generation workflow dict."""
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

    async def _wait_for_completion(self, prompt_id: str, output_dir: Path) -> Optional[Path]:
        """Poll ComfyUI history until the prompt completes."""
        max_wait = self.timeout
        start = time.time()

        while time.time() - start < max_wait:
            try:
                resp = await self._client.get(f"{self.base_url}/history/{prompt_id}")
                if resp.status_code == 200:
                    history = resp.json()
                    entry = history.get(prompt_id)
                    if entry:
                        status = entry.get("status", {})
                        if status.get("status_str") == "success":
                            # Extract output file from history
                            outputs = entry.get("outputs", {})
                            for node_id, node_output in outputs.items():
                                images = node_output.get("images", [])
                                for img in images:
                                    filename = img.get("filename", "")
                                    if filename.endswith(('.png', '.jpg', '.jpeg')):
                                        # Download the image
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
                f"{self.base_url}/view?filename={filename}&type=output&subfolder="
            )
            if resp.status_code == 200:
                filepath = output_dir / filename
                filepath.write_bytes(resp.content)
                return filepath
        except Exception as e:
            logger.warning(f"Failed to download image {filename}: {e}")
        return None

    async def generate_portrait(
        self, prompt: str, output_dir: Path, seed: int = -1
    ) -> Dict[str, Any]:
        """Generate an NPC portrait image."""
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
            "9": {"class_type": "SaveImage", "inputs": {"filename": f"portrait_{int(time.time())}", "images": ["8", 0]}},
        }

        resp = await self._client.post(f"{self.base_url}/prompt", json={"prompt": workflow, "client_id": self._client_id})
        if resp.status_code != 200:
            return {"status": "error", "prompt_id": None, "error": resp.text}

        prompt_id = resp.json().get("prompt_id")
        output_file = await self._wait_for_completion(prompt_id, output_dir)

        return {
            "status": "success" if output_file else "error",
            "prompt_id": prompt_id,
            "output_file": str(output_file) if output_file else None,
        }

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
