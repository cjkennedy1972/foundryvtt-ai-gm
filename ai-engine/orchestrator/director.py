"""SceneDirector — decides which ready NPC acts this tick when more than
one has a goal matching the same event. Pure sequencing: it owns no
narration/rules/memory logic of its own, only picks among candidates
Phase 5 (NPCAgent) and Phase 4 (WorldClockAgent) already produce.

The vast majority of ticks have zero or one NPC ready, in which case the
director is invisible — it just returns that one candidate (or None). It
only matters when two or more NPCs are ready in the same tick: it picks the
highest matched-goal priority and leaves the rest 'active' (not consumed),
so they're reconsidered on the next tick instead of every NPC talking over
each other at once.
"""

from dataclasses import dataclass
from typing import List, Optional

from npc.goals import Goal
from npc.registry import NPCRecord


@dataclass
class Candidate:
    npc: NPCRecord
    matched_goals: List[Goal]

    @property
    def priority(self) -> int:
        return max((g.priority for g in self.matched_goals), default=0)


class SceneDirector:
    def next_turn(self, candidates: List[Candidate]) -> Optional[Candidate]:
        """Highest matched-goal priority wins; ties keep list order (first
        candidate is registry order, i.e. registration order)."""
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.priority)
