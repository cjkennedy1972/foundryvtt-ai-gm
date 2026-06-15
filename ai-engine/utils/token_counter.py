"""Token counter utility — centralized token estimation for LLM context management.

Provides a single source of truth for token counting across all components.
Uses consistent char-to-token ratio to prevent budget disagreement.
"""

from typing import List, Dict, Any


# Centralized token estimation ratio
# Most LLMs use ~4 characters per token on average
# This is validated empirically and should be consistent across all components
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Args:
        text: Text to estimate tokens for

    Returns:
        Estimated token count
    """
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_message_tokens(messages: List[Dict[str, str]]) -> int:
    """Estimate total tokens for a list of messages.

    Each message has overhead (role tag, newline, etc.) counted as ~8 tokens.

    Args:
        messages: List of dicts with 'role' and 'content' keys

    Returns:
        Estimated total token count
    """
    total = 0
    for msg in messages:
        # Role overhead (about 8 tokens)
        total += 8
        # Content tokens
        total += estimate_tokens(msg.get("content", ""))
    return total


def estimate_system_prompt_tokens(system_prompt: str) -> int:
    """Estimate tokens for system prompt.

    System prompts are often long (SRD, world context, etc.).
    Add 50 token overhead for system message framing.

    Args:
        system_prompt: System prompt text

    Returns:
        Estimated token count
    """
    return estimate_tokens(system_prompt) + 50


def calculate_available_budget(
    max_context_tokens: int = 32000,
    max_output_tokens: int = 8192,
    system_prompt: str = "",
    reserved_tokens: int = 500
) -> int:
    """Calculate available tokens for conversation history.

    Budget = max_context - max_output - system_prompt - reserved

    Args:
        max_context_tokens: Maximum context window size (default ~32k)
        max_output_tokens: Reserved for LLM output (default 8k)
        system_prompt: System prompt text (to calculate its size)
        reserved_tokens: Additional safety reserve (default 500)

    Returns:
        Available tokens for conversation history
    """
    system_tokens = estimate_system_prompt_tokens(system_prompt)
    available = max_context_tokens - max_output_tokens - system_tokens - reserved_tokens

    # Ensure budget is non-negative
    return max(0, available)


def trim_messages_to_budget(
    messages: List[Dict[str, str]],
    budget: int,
    always_keep_system: bool = True
) -> List[Dict[str, str]]:
    """Trim message history to fit within token budget.

    Keeps the most recent messages and optionally the first system message.
    Trims from the oldest non-system messages first.

    Args:
        messages: List of messages to trim
        budget: Target token budget
        always_keep_system: If True, always keep first system message

    Returns:
        Trimmed message list
    """
    if not messages:
        return []

    # Separate system messages from others
    system_msgs = []
    other_msgs = []

    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            other_msgs.append(msg)

    # Keep first system message if requested
    kept_systems = []
    if always_keep_system and system_msgs:
        kept_systems = system_msgs[:1]

    # Calculate how many other messages fit in budget
    current_tokens = estimate_message_tokens(kept_systems)
    trimmed_others = []

    # Work backwards from most recent messages
    for msg in reversed(other_msgs):
        msg_tokens = estimate_tokens(msg.get("content", "")) + 8  # 8 for role overhead
        if current_tokens + msg_tokens <= budget:
            trimmed_others.insert(0, msg)
            current_tokens += msg_tokens
        else:
            break  # Stop when budget exceeded

    return kept_systems + trimmed_others


def calculate_turn_tokens(user_message: str, assistant_response: str) -> int:
    """Calculate tokens for a user-assistant turn.

    Args:
        user_message: User's message
        assistant_response: Assistant's response

    Returns:
        Total tokens for the turn
    """
    tokens = 0
    tokens += estimate_tokens(user_message) + 8  # user role overhead
    tokens += estimate_tokens(assistant_response) + 8  # assistant role overhead
    return tokens
