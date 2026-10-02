"""Lets FoundryClient prefer World CLI over the relay/execute_js for the calls it can answer.

The client's methods keep their return shapes; each migrated method asks the router first and, if
it gets None, runs its original relay or execute_js path. So nothing that calls the client changes,
and anything World CLI cannot answer (or that it answers badly) still has the old route.

Reads are idempotent, so any failure at all, including a bug in an adapter, falls back. Writes get
the stricter rule in foundry/world_cli_writes.py: never run twice.

After the daemon or bridge proves unreachable, the router skips World CLI for a short while, so a
down daemon costs one failed attempt, not a connect timeout on every call.
"""

import logging
import time
from typing import Any, Awaitable, Callable, Optional

from foundry.world_cli import WorldCLI, WorldCLIError

logger = logging.getLogger(__name__)

TRIP_SECONDS = 30.0
_UNREACHABLE = frozenset({"DAEMON_UNAVAILABLE", "NOT_CONFIGURED", "BRIDGE_NOT_READY"})


class WorldCLIRouter:
    def __init__(self, cli: WorldCLI, *, reads: bool = False, writes: bool = False):
        self.cli, self.reads, self.writes = cli, reads, writes
        self._skip_until = 0.0

    def tripped(self) -> bool:
        return time.monotonic() < self._skip_until

    def note_failure(self, error: WorldCLIError) -> None:
        if error.code in _UNREACHABLE:
            self._skip_until = time.monotonic() + TRIP_SECONDS
            logger.info(f"[WorldCLI] {error.code}; using the relay for {TRIP_SECONDS:g}s")

    async def read(self, adapter: Callable[..., Awaitable[Any]], *args) -> Optional[Any]:
        """Run `adapter(cli, *args)` and return its result, or None meaning "use the old path"."""
        if not self.reads or self.tripped():
            return None
        try:
            return await adapter(self.cli, *args)
        except WorldCLIError as e:
            self.note_failure(e)
            logger.debug(f"[WorldCLI] {adapter.__name__} -> {e.code}: {e.message}")
            return None
        except Exception:
            logger.warning(f"[WorldCLI] {adapter.__name__} failed; using the relay", exc_info=True)
            return None
