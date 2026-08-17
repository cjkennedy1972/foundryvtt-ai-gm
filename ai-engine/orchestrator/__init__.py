"""Scene orchestration — picks among ready NPC actors when more than one
is available in the same tick."""

from orchestrator.director import Candidate, SceneDirector

__all__ = ["SceneDirector", "Candidate"]
