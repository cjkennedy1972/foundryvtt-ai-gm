"""Clone a pre-configured Foundry template world for a new campaign.

Foundry's setup ``createWorld`` action produces a *blank* world; module
enablement and per-module settings live in the world's LevelDB
``data/settings`` store, written only on first launch. To guarantee every new
campaign world starts with the same base module configuration (the REST-API
relay module enabled, relay URL set, left unpaired), we clone a prepared
template world on the filesystem rather than creating a blank one.

The AI-GM runs on the same machine as Foundry, so it has direct filesystem
access to the Foundry user-data ``worlds`` directory. The template world must
never be launched by Foundry while a clone runs, which is the normal case: it
exists only as a source to copy.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from config import settings

logger = logging.getLogger("ai-gm")

# macOS default Foundry user-data directory.
_DEFAULT_DATA_PATH = "~/Library/Application Support/FoundryVTT/Data"


@dataclass
class CloneResult:
    world_id: str    # folder name / world.json "id" (slug)
    world_name: str  # world.json "title" — what the relay's selectWorld matches on
    system: str      # game system id inherited from the template


def worlds_dir() -> Path:
    base = settings.foundry_data_path or _DEFAULT_DATA_PATH
    return Path(base).expanduser() / "worlds"


def _slugify(name: str) -> str:
    """Match the relay createWorld id rule: lowercase, non-alnum runs -> '-'."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _unique_world_id(base_slug: str, worlds: Path) -> str:
    if not (worlds / base_slug).exists():
        return base_slug
    n = 2
    while (worlds / f"{base_slug}-{n}").exists():
        n += 1
    return f"{base_slug}-{n}"


def clone_world(campaign_name: str, *, description: str = "",
                expected_system: str = "") -> CloneResult:
    """Clone the template world into a new world for ``campaign_name``.

    Returns the new world's id (folder), title, and system. Raises ValueError
    with an actionable message if the data dir or template is missing, the name
    does not yield a valid world id, or ``expected_system`` (when given) does
    not match the template's system.
    """
    worlds = worlds_dir()
    if not worlds.is_dir():
        raise ValueError(
            f"Foundry worlds directory not found at '{worlds}'. Set "
            "FOUNDRY_DATA_PATH to your Foundry user-data folder."
        )

    template_id = settings.foundry_world_template_id
    template_dir = worlds / template_id
    manifest_src = template_dir / "world.json"
    if not manifest_src.is_file():
        raise ValueError(
            f"Template world '{template_id}' not found in '{worlds}'. Create it "
            "once in Foundry (blank world, enable base modules, set the relay "
            "URL, leave it unpaired), then retry."
        )

    template = json.loads(manifest_src.read_text(encoding="utf-8"))
    template_system = template.get("system", "")
    # ponytail: one template defines the system. A per-system template set is a
    # future extension keyed by foundry_world_template_id, not needed for one.
    if expected_system and template_system and expected_system != template_system:
        raise ValueError(
            f"Template world '{template_id}' uses system '{template_system}', "
            f"but system '{expected_system}' was requested. Prepare a matching "
            "template or request the template's system."
        )

    slug = _slugify(campaign_name)
    if not slug:
        raise ValueError(
            f"Campaign name '{campaign_name}' does not produce a valid world id."
        )
    world_id = _unique_world_id(slug, worlds)
    new_dir = worlds / world_id

    shutil.copytree(template_dir, new_dir)

    # LevelDB LOCK files are process-local; a copied one must not travel or the
    # world can appear locked. Foundry recreates them on launch.
    for lock in new_dir.glob("data/**/LOCK"):
        lock.unlink()

    # Rewrite identity fields only; keep system/coreVersion/systemVersion from
    # the template so the clone always matches the Foundry core it was built on.
    manifest = dict(template)
    manifest["id"] = world_id
    manifest["title"] = campaign_name
    if description:
        manifest["description"] = description
    (new_dir / "world.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    logger.info(
        "Cloned template world '%s' -> '%s' (title=%r, system=%r)",
        template_id, world_id, campaign_name, template_system,
    )
    return CloneResult(world_id=world_id, world_name=campaign_name, system=template_system)
