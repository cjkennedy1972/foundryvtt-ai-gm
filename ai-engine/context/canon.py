"""Canon-proposal generation — end-of-session review queue.

At session end, the AI reviews the session's recorded highlights and
proposes candidate canon facts (permanent world truth) for GM review, each
with a confidence bucket and a one-line rationale, checked against existing
canon for contradictions along the way. Nothing here is auto-approved —
see persistence/db.py's canon_proposals table and foundry/chat_listener.py's
/gm canon review|approve|reject commands. A fully autonomous canonization
was deliberately ruled out: canon is permanent and compounds, so a bad
judgment here isn't forgotten like a bad ad-lib would be — it stays load-
bearing for every future turn.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_VALID_CONFIDENCE = {"high", "medium", "low"}


def _extract_json_object(text: str) -> Optional[Any]:
    """Pull the outermost {...} JSON object out of an LLM response, tolerant
    of markdown code fences and stray commentary before/after it. Returns
    None on anything unparseable rather than raising."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


def build_canon_proposal_prompt(highlights: List[str], existing_canon_text: str) -> Tuple[str, str]:
    """Build a system/user prompt asking the LLM to propose canon-worthy
    facts from this session's highlights, flagging any that conflict with
    existing canon along the way.

    Returns (system_prompt, user_prompt).
    """
    system = (
        "You are reviewing a tabletop RPG session's recorded highlights to "
        "propose which moments should become permanent campaign canon — "
        "durable world truth that will constrain all future narration. Be "
        "conservative: only propose a fact if it represents a genuine, "
        "consequential, permanent change (an NPC's fate, a world-state "
        "change, a revealed truth) — not flavor, not something reversible, "
        "not something already obviously implied by existing canon.\n\n"
        "For each proposal, give:\n"
        "- fact: the permanent truth, stated plainly and standalone (a "
        "future reader won't have this session's context)\n"
        "- confidence: \"high\", \"medium\", or \"low\" — how sure you are "
        "this is genuinely canon-worthy and not just interesting flavor\n"
        "- rationale: one sentence on why it matters for future consistency\n"
        "- contradiction_note: if this fact conflicts with anything in the "
        "EXISTING CANON below, briefly state what it conflicts with — "
        "otherwise null\n\n"
        "It is correct to propose ZERO facts if nothing this session rises "
        "to the level of permanent canon.\n\n"
        "Respond with ONLY a JSON object: "
        '{"proposals": [{"fact": "...", "confidence": "high", '
        '"rationale": "...", "contradiction_note": null}, ...]}\n'
        "No commentary before or after the JSON."
    )
    highlights_block = "\n".join(f"- {h}" for h in highlights) if highlights else "(no highlights recorded)"
    canon_block = (
        existing_canon_text.strip() if existing_canon_text and existing_canon_text.strip()
        else "(no canon established yet)"
    )
    user = (
        f"EXISTING CANON:\n{canon_block}\n\n"
        f"THIS SESSION'S HIGHLIGHTS:\n{highlights_block}\n\n"
        "Return the JSON now."
    )
    return system, user


def parse_canon_proposals(text: str) -> List[Dict[str, Any]]:
    """Parse a build_canon_proposal_prompt response into a validated list of
    proposals. Falls back to an empty list on anything unparseable or
    malformed — a missed proposal is far cheaper than a garbage one landing
    in the review queue."""
    data = _extract_json_object(text)
    if not isinstance(data, dict):
        return []
    proposals = data.get("proposals")
    if not isinstance(proposals, list):
        return []

    cleaned: List[Dict[str, Any]] = []
    for item in proposals:
        if not isinstance(item, dict):
            continue
        fact = item.get("fact")
        if not isinstance(fact, str) or not fact.strip():
            continue
        confidence = item.get("confidence")
        if not isinstance(confidence, str) or confidence.lower() not in _VALID_CONFIDENCE:
            # Conservative default when the model's own signal is missing/malformed.
            confidence = "low"
        else:
            confidence = confidence.lower()
        rationale = item.get("rationale")
        rationale = rationale.strip() if isinstance(rationale, str) else ""
        contradiction_note = item.get("contradiction_note")
        contradiction_note = (
            contradiction_note.strip()
            if isinstance(contradiction_note, str) and contradiction_note.strip()
            else None
        )
        cleaned.append({
            "fact": fact.strip(),
            "confidence": confidence,
            "rationale": rationale,
            "contradiction_note": contradiction_note,
        })
    return cleaned


async def generate_canon_proposals(
    llm_client,
    endpoint: str,
    headers: Dict[str, str],
    model: str,
    highlights: List[str],
    existing_canon_text: str,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> List[Dict[str, Any]]:
    """POST one canon-proposal-generation call and return the parsed list.

    Never raises — a failed or malformed generation degrades to an empty
    proposal list. Nothing gets silently canonized either way; the worst
    case is simply that this session contributed zero proposals.
    """
    if not highlights:
        return []
    system, user = build_canon_proposal_prompt(highlights, existing_canon_text)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            # /nothink + enable_thinking=False: model-agnostic reasoning-token
            # suppression, matching campaign/orchestrator.py's _suppress_thinking.
            {"role": "user", "content": "/nothink\n" + user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "enable_thinking": False,
    }
    try:
        resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        return parse_canon_proposals(text)
    except Exception as e:
        logger.warning(f"[Canon] Proposal generation failed: {e}")
        return []


async def approve_canon_proposal_with_vault_write(
    db,
    llm_manager,
    proposal_id: int,
    campaign_vault_path: str,
    final_text: Optional[str] = None,
) -> Tuple[bool, str]:
    """Approve a pending canon proposal and write it to the vault, safely:

    1. Atomically claim the proposal (db.approve_canon_proposal's compare-
       and-swap on status='pending') BEFORE attempting any vault write —
       if it's already been reviewed (a double-click, or two surfaces
       approving the same id), this call simply stops here instead of
       writing the fact a second time.
    2. Only after winning the claim, write to the vault and push it live.
    3. If the vault write fails, revert the claim back to 'pending' so the
       proposal can be retried — previously the DB was left permanently
       'approved' with the fact never actually written anywhere.

    Returns (success, message). On failure, message explains why (already
    reviewed, or the vault error) — callers surface it to the GM instead of
    reporting a bare success that didn't actually happen.
    """
    from campaign.obsidian_sync import get_campaign_folder, push_canon_fact_live, resolve_vault_path

    proposal = await db.get_canon_proposal(proposal_id)
    if proposal is None:
        return False, "Canon proposal not found."

    claimed = await db.approve_canon_proposal(proposal_id, final_text)
    if not claimed:
        return False, "That proposal was already reviewed."

    fact_text = final_text or proposal["fact"]
    try:
        vault_path = resolve_vault_path(campaign_vault_path)
        campaign_folder = get_campaign_folder(vault_path, proposal["campaign"])
        await push_canon_fact_live(campaign_folder, fact_text, llm_manager)
    except Exception as e:
        await db.revert_canon_proposal_to_pending(proposal_id)
        logger.warning(f"[Canon] Vault write failed for proposal {proposal_id}, reverted to pending: {e}")
        return False, f"Approved, but writing to the vault failed ({e}) — reverted to pending, please retry."

    return True, fact_text


async def reject_canon_proposal_safely(db, proposal_id: int) -> Tuple[bool, str]:
    """Reject a pending canon proposal. Returns (success, message) — success
    is False if the proposal doesn't exist or was already reviewed."""
    proposal = await db.get_canon_proposal(proposal_id)
    if proposal is None:
        return False, "Canon proposal not found."

    rejected = await db.reject_canon_proposal(proposal_id)
    if not rejected:
        return False, "That proposal was already reviewed."

    return True, ""
