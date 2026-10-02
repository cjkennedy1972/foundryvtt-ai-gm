"""Cinematic art for Storyteller's Cinema: an establishing still per social scene, and optionally a short clip.

A battlemap is top-down; Cinema's stage wants a widescreen picture you look *into*. For each tavern/castle/village-type
scene this makes one (z-image turbo, ~40 s on Apple silicon) and, if enabled, animates it (LTX-Video 2B distilled,
~30 s for 4 s of footage). Measured on the real stack: LTX gave a stable, gentle push-in; Wan 2.2 5B drifted colors or
collapsed on Apple's GPU, so it is not used. All of it is optional and slow, hence off by default (cinema_art_enabled).

ComfyUI needs the models; `available()` says what is missing. The clip is re-encoded to WebM (VP9) with ffmpeg when
ffmpeg is present, else the H.264 mp4 is kept (Foundry plays both).
"""

from __future__ import annotations

import asyncio
import logging
import random
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

STILL_SIZE = (1280, 704)        # 16:9-ish, multiples of 32
CLIP_SIZE = (832, 480)
CLIP_FRAMES = 97                # 8n+1 (LTX); 4 s at 24 fps
Z = {"unet": "z_image_turbo_bf16.safetensors", "clip": "qwen_3_4b.safetensors", "vae": "ae.safetensors"}
MOTION_PROMPT = ("slow steady camera push-in, thin mist drifting low across the ground, torch and candle flames flickering, "
                 "everything else stays still, consistent colors and lighting")


def still_prompt(description: str) -> str:
    return (f"cinematic wide establishing shot, {description}, painterly high fantasy illustration, muted natural palette, "
            "soft atmospheric lighting, no people in the foreground, full-bleed, no border, no frame, no text")


class CinematicArtist:
    """Makes a scene's establishing still and clip through the MapGenerator's ComfyUI connection."""

    def __init__(self, generator):
        self.g = generator

    async def available(self) -> Dict[str, bool]:
        """Which stages ComfyUI can run: {"still": bool, "clip": bool}."""
        async def choices(node: str, field: str) -> List[str]:
            info = (await self.g._client.get(f"{self.g.comfyui_base_url}/object_info/{node}", timeout=15)).json()
            return info[node]["input"]["required"][field][0]
        try:
            still = (Z["unet"] in await choices("UNETLoader", "unet_name") and Z["clip"] in await choices("CLIPLoader", "clip_name")
                     and Z["vae"] in await choices("VAELoader", "vae_name"))
            clip = (settings.ltx_checkpoint in await choices("CheckpointLoaderSimple", "ckpt_name")
                    and settings.ltx_text_encoder in await choices("CLIPLoader", "clip_name"))
            await choices("LTXVImgToVideo", "width")              # the node itself must exist (older ComfyUI lacks it)
        except Exception as e:
            logger.info(f"[Cinematic] ComfyUI capability check failed ({e})")
            return {"still": False, "clip": False}
        return {"still": still, "clip": clip}

    def _still_graph(self, description: str, seed: int, prefix: str) -> Dict[str, Any]:
        w, h = STILL_SIZE
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": Z["unet"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": Z["clip"], "type": "lumina2", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": Z["vae"]}},
            "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": still_prompt(description)}},
            "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
            "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {
                "model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": seed,
                "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "11": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
        }

    def _clip_graph(self, staged_image: str, seed: int, prefix: str) -> Dict[str, Any]:
        w, h = CLIP_SIZE
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": settings.ltx_checkpoint}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": settings.ltx_text_encoder, "type": "ltxv", "device": "default"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": MOTION_PROMPT}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "static, blurry, low quality, watermark, text, distorted, jitter, color shift"}},
            "5": {"class_type": "LoadImage", "inputs": {"image": staged_image}},
            "6": {"class_type": "LTXVImgToVideo", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "vae": ["1", 2], "image": ["5", 0],
                                                            "width": w, "height": h, "length": CLIP_FRAMES, "batch_size": 1, "strength": 1.0}},
            "7": {"class_type": "LTXVConditioning", "inputs": {"positive": ["6", 0], "negative": ["6", 1], "frame_rate": 24.0}},
            "8": {"class_type": "LTXVScheduler", "inputs": {"steps": 8, "max_shift": 2.05, "base_shift": 0.95, "stretch": True, "terminal": 0.1, "latent": ["6", 2]}},
            "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "10": {"class_type": "SamplerCustom", "inputs": {"model": ["1", 0], "add_noise": True, "noise_seed": seed, "cfg": 1.0,
                                                           "positive": ["7", 0], "negative": ["7", 1], "sampler": ["9", 0], "sigmas": ["8", 0], "latent_image": ["6", 2]}},
            "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["1", 2]}},
            "12": {"class_type": "CreateVideo", "inputs": {"images": ["11", 0], "fps": 24.0}},
            "13": {"class_type": "SaveVideo", "inputs": {"video": ["12", 0], "filename_prefix": prefix, "format": "mp4", "codec": "h264"}},
        }

    async def still(self, description: str, output_dir: Path, seed: int = -1) -> Optional[Path]:
        output_dir = self.g._checked_output_dir(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        seed = random.getrandbits(31) if seed < 0 else seed
        prefix = f"cinematic_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        result = await self.g._submit_and_wait(self._still_graph(description, seed, prefix), output_dir, "cinematic")
        return Path(result["output_file"]) if result.get("output_file") else None

    async def clip(self, still_path: Path, output_dir: Path, seed: int = -1) -> Optional[Path]:
        """Animate `still_path` into a ~4 s clip (WebM when ffmpeg is there, else mp4). None when it could not be made."""
        output_dir = self.g._checked_output_dir(output_dir)
        input_dir = await self.g._ensure_comfyui_input_dir()
        if input_dir is None:
            logger.warning("[Cinematic] no ComfyUI input directory: cannot stage the still for animation")
            return None
        seed = random.getrandbits(31) if seed < 0 else seed
        staged = f"cinema_{uuid.uuid4().hex[:8]}.png"
        await asyncio.to_thread(shutil.copy, still_path, input_dir / staged)
        prefix = f"cinematic_clip_{int(time.time())}_{uuid.uuid4().hex[:6]}"          # no subfolder: _download_image fetches the root
        result = await self.g._submit_and_wait(self._clip_graph(staged, seed, prefix), output_dir, "cinematic", accept=(".mp4", ".webm"))
        if not result.get("output_file"):
            return None
        mp4 = Path(result["output_file"])
        return await to_webm(mp4) or mp4


async def to_webm(mp4: Path) -> Optional[Path]:
    """Re-encode to VP9 WebM (~0.15 MB per 1.4 s) with ffmpeg; None when ffmpeg is missing or fails."""
    if not shutil.which("ffmpeg"):
        return None
    dest = mp4.with_suffix(".webm")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "34", "-an", str(dest),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0 or not dest.exists():
        logger.warning(f"[Cinematic] ffmpeg could not make a WebM: {(err or b'').decode()[:200]}")
        return None
    return dest
