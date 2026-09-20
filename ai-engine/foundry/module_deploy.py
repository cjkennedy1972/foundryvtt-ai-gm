"""Auto-deploy the bundled AI GM Foundry modules into the user's Foundry
Data/modules directory, so they ship with the app and never drift from the repo.
The user only has to enable a module once in the world.
"""
import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bundled modules live in <repo>/foundry-module/<module id>
_MODULES_SRC = Path(__file__).resolve().parent.parent.parent / "foundry-module"

# Not part of a deployed module: dev-only files that may sit inside a module dir.
_IGNORE = shutil.ignore_patterns("tests", "node_modules", "*.test.mjs", ".DS_Store")

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


def deploy_module(module_id: str, configured_path: str = "") -> bool:
    """Copy a bundled module into Foundry's modules dir.

    Idempotent: replaces the installed copy so updates ship automatically. The
    directory name is the module id, which is what Foundry requires; a copy
    under any other name (for example a renamed ``.disabled`` one) is reported
    as an invalid module on every boot.
    Returns True on success.
    """
    src = _MODULES_SRC / module_id
    if not src.is_dir():
        logger.warning(f"[{module_id}] Module source not found at {src}")
        return False

    modules_dir = resolve_modules_path(configured_path)
    if not modules_dir:
        logger.warning(
            f"[{module_id}] Could not locate Foundry Data/modules. Set "
            "FOUNDRY_MODULES_PATH in .env to enable module auto-deploy."
        )
        return False

    dest = modules_dir / module_id
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=_IGNORE)
        logger.info(f"[{module_id}] Deployed to {dest}")
        return True
    except OSError as e:
        logger.warning(f"[{module_id}] Failed to deploy to {dest}: {e}")
        return False


def deploy_aigm_tts(configured_path: str = "") -> bool:
    """Deploy the browser-TTS module."""
    return deploy_module("aigm-tts", configured_path)


def deploy_aigm_control_panel(configured_path: str = "") -> bool:
    """Deploy the in-Foundry control panel (left disabled until enabled in a world)."""
    return deploy_module("aigm-control-panel", configured_path)
