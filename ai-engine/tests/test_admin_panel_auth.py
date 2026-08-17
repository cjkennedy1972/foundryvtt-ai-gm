"""Source guard: every admin-panel request must go through the fetch wrapper.

apiFetch/safeFetch (admin-panel/src/fetch.js) is what attaches the
Authorization header from localStorage. Two call sites once used a bare
fetch() instead, so campaign deploy and asset regeneration were the only
panel actions that failed with ADMIN_TOKEN set — a bug invisible to anyone
running loopback-only. The panel has no JS test runner, so this guard lives
here rather than adding one.
"""

import re
from pathlib import Path

PANEL_SRC = Path(__file__).resolve().parent.parent / "admin-panel" / "src"

# `fetch(` not preceded by a word character — matches a bare call but not
# safeFetch(/apiFetch(.
_BARE_FETCH = re.compile(r"(?<![\w.])fetch\s*\(")


def test_no_bare_fetch_outside_the_wrapper():
    offenders = []
    for path in PANEL_SRC.rglob("*.js"):
        if path.name == "fetch.js":  # the wrapper itself is the one real caller
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            code = line.split("//", 1)[0]
            if _BARE_FETCH.search(code):
                offenders.append(f"{path.relative_to(PANEL_SRC)}:{lineno}")
    assert not offenders, (
        "these call fetch() directly and so send no Authorization header; "
        f"use safeFetch/apiFetch instead: {offenders}"
    )
