"""
Filters AI GM log lines to surface only operational status —
no game content, character names, campaign lore, or LLM output.
"""

import re

_LOG_RE = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \[(\w+)\] ([\w.\-]+): (.*)"
)

# Loggers whose output is operational (connection status, lifecycle, errors)
_SAFE_LOGGERS = frozenset({
    "ai-gm",
    "foundry.client",
    "foundry.chat_listener",
    "relay",
    "relay_proc",
    "relay_proc.manager",
    "combat.loop",
    "persistence.db",
    "context.reinforcement_manager",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
})

# Substrings that reveal game content even in safe loggers
_BLOCKED = (
    "vault path",
    "campaign context for",
    "loaded campaign context",
    "continuing session",
    "llm generation",
    "loaded campaign",
)


def is_safe_line(line: str) -> bool:
    m = _LOG_RE.match(line)
    if not m:
        return False
    logger_name = m.group(2)
    safe = any(
        logger_name == s or logger_name.startswith(s + ".")
        for s in _SAFE_LOGGERS
    )
    if not safe:
        return False
    lower = line.lower()
    return not any(pattern in lower for pattern in _BLOCKED)


def log_parts(line: str):
    """Return (level, logger, message) or None for non-log lines."""
    m = _LOG_RE.match(line)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)
