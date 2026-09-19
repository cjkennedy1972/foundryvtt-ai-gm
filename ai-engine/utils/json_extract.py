"""Pull a JSON object out of an LLM response.

Local models do not answer with bare JSON. They wrap it in ```json fences,
open with a sentence of preamble, and — for the reasoning models this project
targets — emit a <think>...</think> block first. campaign/generator.py's
parse_campaign_response has documented all three for a while, naming Qwen3's
reasoning tokens specifically.

This was two byte-identical copies of _extract_json_object, in
campaign/importer.py and context/canon.py, while campaign/module_discovery.py
called json.loads on the raw response six times and swallowed the
JSONDecodeError. One copy here, with the <think> strip the other two lacked.
"""

import json
import re
from typing import Any, Optional

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def extract_json_object(text: str) -> Optional[Any]:
    """The outermost {...} in *text*, or None if there isn't a parseable one.

    Tolerant of code fences, reasoning blocks and commentary either side.
    Returns None rather than raising, so callers can treat "the model gave me
    nothing usable" as data.
    """
    if not text:
        return None
    # No fence stripping: ```json markers carry no braces, so scanning for the
    # outermost {...} steps over them already. Both copies this replaces
    # stripped fences first; removing it changes no result here.
    cleaned = _THINK.sub("", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
