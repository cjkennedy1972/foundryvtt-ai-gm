"""Auto-deploy the bundled aigm-tts Foundry module into the user's Foundry
Data/modules directory so browser-side TTS ships with the app — the user only
has to enable it once in the world.
"""
import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Source of the module inside this repo: <repo>/foundry-module/aigm-tts
_MODULE_SRC = Path(__file__).resolve().parent.parent.parent / "foundry-module" / "aigm-tts"
_MODULE_ID = "aigm-tts"

# Common Foundry Data/modules locations per OS (newest naming first).
_CANDIDATE_DIRS = [
    "~/Library/Application Support/FoundryVTT/Data/modules",           # macOS
    "~/Library/Application Support/Foundry Virtual Tabletop/Data/modules",
    "~/.local/share/FoundryVTT/Data/modules",                          # Linux
    "~/.local/share/Foundry Virtual Tabletop/Data/modules",
    "~/AppData/Local/FoundryVTT/Data/modules",                         # Windows
    "~/AppData/Local/Foundry Virtual Tabletop/Data/modules",
]


def resolve_modules_path(configured: str = "") -> Optional[Path]:
    """Return the Foundry Data/modules directory, or None if not found."""
    if configured:
        p = Path(configured).expanduser()
        return p if p.is_dir() else None
    for cand in _CANDIDATE_DIRS:
        p = Path(cand).expanduser()
        if p.is_dir():
            return p
    return None


def deploy_aigm_tts(configured_path: str = "") -> bool:
    """Copy the bundled aigm-tts module into Foundry's modules dir.

    Idempotent: overwrites the installed copy so updates ship automatically.
    Returns True on success.
    """
    if not _MODULE_SRC.is_dir():
        logger.warning(f"[aigm-tts] Module source not found at {_MODULE_SRC}")
        return False

    modules_dir = resolve_modules_path(configured_path)
    if not modules_dir:
        logger.warning(
            "[aigm-tts] Could not locate Foundry Data/modules. Set "
            "FOUNDRY_MODULES_PATH in .env to enable browser TTS auto-deploy."
        )
        return False

    dest = modules_dir / _MODULE_ID
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(_MODULE_SRC, dest)
        logger.info(f"[aigm-tts] Deployed browser-TTS module to {dest}")
        return True
    except OSError as e:
        logger.warning(f"[aigm-tts] Failed to deploy module to {dest}: {e}")
        return False
