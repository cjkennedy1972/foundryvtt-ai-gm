"""Data shapes exchanged between the narrator LLM and the RefereeAgent."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Ruling:
    """The Referee's verdict on a single proposed action.

    `action` is either the original payload (approved unchanged) or a
    rules-adjusted copy (e.g. a clamped DC) — callers should always dispatch
    `ruling.action`, never the original proposal, once a ruling exists.
    """

    approved: bool
    action: Dict[str, Any]
    reason: Optional[str] = None
    notes: list = field(default_factory=list)
