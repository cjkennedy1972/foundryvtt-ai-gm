"""Lets FoundryClient prefer World CLI over the relay/execute_js for the calls it can answer.

The client's methods keep their return shapes; each migrated method asks the router first and, if
it gets None, runs its original relay or execute_js path. So nothing that calls the client changes,
and anything World CLI cannot answer (or that it answers badly) still has the old route.

Reads are idempotent, so any failure at all, including a bug in an adapter, falls back. Writes follow
the stricter rule in write(): never run twice.

After the daemon or bridge proves unreachable, the router skips World CLI for a short while, so a
down daemon costs one failed attempt, not a connect timeout on every call.
"""

import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from foundry.world_cli import WorldCLI, WorldCLIError

logger = logging.getLogger(__name__)

TRIP_SECONDS = 30.0
_UNREACHABLE = frozenset({"DAEMON_UNAVAILABLE", "NOT_CONFIGURED", "BRIDGE_NOT_READY"})
# Where the protocol says the same idempotency key may be re-sent while the bridge session lasts.
_SAME_KEY_RETRY = frozenset({"TIMEOUT", "BRIDGE_TIMEOUT"})


class WorldCLIWriteUncertain(Exception):
    """A World CLI write may have applied and could not be confirmed. The relay is deliberately NOT tried:
    running it again could apply it twice. Callers handle this like any failed write; not a ConnectionError
    or RuntimeError, so a retry wrapper cannot mistake it for a transient failure."""

    def __init__(self, error: WorldCLIError):
        super().__init__(f"World CLI write may have applied ({error.code}): {error.message}")
        self.error = error


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

    async def write(
        self,
        preflight: Optional[Callable[[WorldCLI], Awaitable[Any]]],
        execute: Callable[[WorldCLI, str], Awaitable[Any]],
        *, same_key_retry: bool = False,
    ) -> Optional[Any]:
        """Run a write through World CLI, or return None meaning "use the relay".

        `preflight(cli)` is a dry run: it persists nothing, so ANY failure of it falls back. `execute(cli,
        key)` is the real call and must pass `key` as the idempotency key if the command takes one (set
        same_key_retry then). It falls back only when the error proves nothing executed
        (WorldCLIError.maybe_applied is False). A timeout is retried once with the same key, which is
        safe; any other error that may have applied raises WorldCLIWriteUncertain instead of risking a
        second application on the relay.
        """
        if not self.writes or self.tripped():
            return None
        if preflight is not None:
            try:
                await preflight(self.cli)
            except WorldCLIError as e:
                self.note_failure(e)
                logger.debug(f"[WorldCLI] preflight -> {e.code}: {e.message}")
                return None
            except Exception:
                logger.warning("[WorldCLI] preflight failed; using the relay", exc_info=True)
                return None
        key = uuid.uuid4().hex
        for attempt in (0, 1):
            try:
                return await execute(self.cli, key)
            except WorldCLIError as e:
                self.note_failure(e)
                if not e.maybe_applied:
                    logger.info(f"[WorldCLI] write refused ({e.code}), nothing executed; using the relay: {e.message}")
                    return None
                if attempt == 0 and same_key_retry and e.code in _SAME_KEY_RETRY:
                    logger.warning(f"[WorldCLI] write {e.code}; retrying once with the same idempotency key")
                    continue
                logger.error(f"[WorldCLI] write may have applied ({e.code}); NOT retrying on the relay: {e.message}")
                raise WorldCLIWriteUncertain(e) from e
