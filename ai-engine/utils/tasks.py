"""Fire-and-forget task helper that retains a strong reference.

asyncio.create_task only keeps a *weak* reference to the task; if the caller
drops its own reference, the event loop can garbage-collect the task while it is
still running. Routing background tasks through spawn() keeps each one alive in
a module-level set until it finishes, then discards it via a done-callback.
"""

import asyncio

_bg_tasks: set = set()


def spawn(coro) -> asyncio.Task:
    """Schedule *coro* as a background task with a retained strong reference."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task
