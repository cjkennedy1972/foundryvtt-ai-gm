"""Campaign memory — what happened in play, kept so the GM can find it again.

The rolling chat history only holds what fits the token budget, so anything
older used to be gone. This keeps it in layers:

  raw        ai_conversations rows. Append-only, never summarised in place,
             never deleted by retention. Everything below is rebuilt from it.
  level 1    a summary of a run of raw rows (every N player turns), with the
             topics it mentions and any durable facts it established.
  level 2    a summary of a whole session, written when the session closes.
  facts      promises, debts, injuries, items, deaths... held apart from the
             summaries so compaction can't blur them, shown while still open.
  index      the topics of every node: a single line, visible on every turn.

Each turn the index and the open facts are always shown. Details are
retrieved only when the player's message names a topic the index holds —
the index is what decides relevance, so an unrelated turn costs one line.

Summaries are disposable: rebuild() drops them and compacts the raw rows
again. Compaction runs off the player's turn (callers spawn it) and through
generate_text, which reads no conversation history.
"""

import asyncio
import json
import logging
import re
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

FACT_KINDS = ("promise", "debt", "injury", "item", "death", "relationship", "secret", "quest")
MAX_ROWS_PER_PASS = 200       # raw rows read per compaction pass; bounds the transcript
MAX_LINE_CHARS = 600          # one raw row in the transcript
MAX_TOPICS_PER_NODE = 12
MAX_FACTS_PER_NODE = 8
INDEX_TOPICS = 40             # topics shown in the always-visible index
OPEN_FACTS_SHOWN = 12
SESSION_NODES_SHOWN = 3       # this session's most recent level-1 summaries
RECALLED_NODES = 3
BACKOFF_BASE_S = 30           # first wait after a failed compaction; doubles per failure
BACKOFF_MAX_S = 600

COMPACT_PROMPT = (
    "You compact a tabletop RPG transcript into memory the Game Master will "
    "rely on in later sessions. Reply with ONLY a JSON object:\n"
    '{"summary": "<under 150 words: who did what, decisions, outcomes, '
    'unresolved threads; keep proper nouns; drop dice and flavour>", '
    '"topics": ["<named people, places, factions, items or quests that could '
    'come up again, as written in the transcript, at most 12>"], '
    '"facts": [{"kind": "<' + "|".join(FACT_KINDS) + '>", "text": "<one '
    'durable fact: a promise made, a debt owed, an injury, an item gained or '
    'lost, a death, a changed relationship, a secret revealed, a quest taken>"}], '
    '"resolved": [<ids of OPEN FACTS this transcript settles>]}\n'
    "Only record facts that will still matter later. Empty lists are fine."
)

SESSION_PROMPT = (
    "You condense a tabletop RPG session's notes into one recap the Game "
    "Master will read at the start of the next session. Reply with ONLY a "
    'JSON object: {"summary": "<under 200 words, keep proper nouns, end on '
    'where the party stands and what is unresolved>", "topics": ["<at most '
    '12 named people, places, factions, items or quests>"]}'
)


def _parse_json(text: str) -> Optional[dict]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_topics(raw) -> List[str]:
    topics, seen = [], set()
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, str):
            continue
        t = t.strip()
        if 2 <= len(t) <= 60 and t.lower() not in seen:
            seen.add(t.lower())
            topics.append(t)
    return topics[:MAX_TOPICS_PER_NODE]


def _clean_facts(raw) -> List[dict]:
    facts = []
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict):
            continue
        kind, text = f.get("kind"), f.get("text")
        if kind in FACT_KINDS and isinstance(text, str) and text.strip():
            facts.append({"kind": kind, "text": text.strip()[:300]})
    return facts[:MAX_FACTS_PER_NODE]


def _render_row(row: dict) -> str:
    """One raw row as a transcript line, or "" for rows that aren't play."""
    role, content = row["role"], row["content"] or ""
    if role == "user":
        line = f"PLAYER: {content}"
    elif role == "event":
        line = f"EVENT: {content}"
    elif role == "assistant":
        try:
            action = json.loads(content)
        except json.JSONDecodeError:
            action = None
        if isinstance(action, dict) and action.get("type") in ("narrate", "speak"):
            who = action.get("npc_name") or "GM"
            line = f"{who}: {action.get('text', '')}"
        elif isinstance(action, dict):
            args = {k: v for k, v in action.items() if k != "type"}
            line = f"GM action {action.get('type')}: {json.dumps(args)}"
        else:
            line = f"GM: {content}"
    else:
        # "system" rows are summaries an older build wrote into the raw log.
        # They are derived, not play, and must not be compacted as if they were.
        return ""
    return line[:MAX_LINE_CHARS]


def _mentions(query: str, topic: str) -> bool:
    # ponytail: exact phrase match on the topic as the model wrote it. Misses
    # "the tower" for "Black Tower"; add aliases or embeddings if that bites.
    return re.search(r"\b" + re.escape(topic.lower()) + r"\b", query) is not None


class CampaignMemory:
    def __init__(self, db, llm_manager, every_n_turns: int = 10):
        self.db = db
        self.llm = llm_manager
        self.every = max(1, every_n_turns)
        # ponytail: one lock for every campaign. Compaction is rare and off the
        # turn path; per-campaign locks only if several run concurrently.
        self._lock = asyncio.Lock()
        # Automatic compaction backs off after failures, so a model that keeps
        # answering badly isn't asked again on every player turn.
        self._failures = 0
        self._retry_at = 0.0

    # --- reading (every turn) ---------------------------------------------

    async def _load(self, campaign: str) -> dict:
        # Read fresh every time. A cache here served a restarted campaign's
        # wiped memory (delete_campaign_history bypasses this class) until
        # the process restarted; two small indexed reads a turn is cheaper
        # than keeping it honest.
        return {
            "nodes": await self.db.get_memory_nodes(campaign),
            "facts": await self.db.get_open_memory_facts(campaign, OPEN_FACTS_SHOWN),
        }

    async def context_block(self, campaign: str, session_id: Optional[str], query: str) -> str:
        """The memory section of this turn's prompt. Empty until something
        has been compacted."""
        if not campaign:
            return ""
        mem = await self._load(campaign)
        nodes, facts = mem["nodes"], mem["facts"]
        if not nodes and not facts:
            return ""
        # Blank summaries only mark legacy rows as covered; they aren't memory.
        level1 = [n for n in nodes if n["level"] == 1 and n["summary"]]

        # Index: every topic, most recently mentioned first.
        counts: Dict[str, int] = {}
        latest: Dict[str, int] = {}
        display: Dict[str, str] = {}
        for n in level1:
            for t in n["topics"]:
                key = t.lower()
                counts[key] = counts.get(key, 0) + 1
                latest[key] = n["id"]
                display[key] = t
        ordered = sorted(counts, key=lambda k: latest[k], reverse=True)

        parts = ["## CAMPAIGN MEMORY"]
        if ordered:
            parts.append(
                "Topics with recorded history (named again, their history is recalled below): "
                + ", ".join(display[k] for k in ordered[:INDEX_TOPICS])
            )
        if facts:
            parts.append("Open threads — stay consistent with these:\n" + "\n".join(
                f"- [{f['kind']}] {f['text']}" for f in facts
            ))

        shown = set()
        previous = [n for n in nodes if n["level"] == 2 and n["session_id"] != session_id]
        if previous:
            parts.append("Previously (last session): " + previous[-1]["summary"])
            # A one-segment session's recap is that segment, verbatim.
            shown.update(n["id"] for n in level1 if n["summary"] in previous[-1]["summary"])
        this_session = [n for n in level1 if n["session_id"] == session_id][-SESSION_NODES_SHOWN:]
        if this_session:
            parts.append("Earlier this session:\n" + "\n".join(f"- {n['summary']}" for n in this_session))
            shown.update(n["id"] for n in this_session)

        q = (query or "").lower()
        matched = {k for k in ordered if _mentions(q, k)}
        if matched:
            recalled = [
                n for n in reversed(level1)
                if n["id"] not in shown and matched & {t.lower() for t in n["topics"]}
            ][:RECALLED_NODES]
            if recalled:
                names = ", ".join(display[k] for k in sorted(matched))
                parts.append(f"Recalled because {names} came up:\n" + "\n".join(
                    f"- ({'this session' if n['session_id'] == session_id else 'a past session'}) "
                    f"{n['summary']}" for n in reversed(recalled)
                ))
        return "\n\n".join(parts)

    async def recent_messages(self, campaign: str, session_id: str, limit: int = 40) -> List[dict]:
        """The session's newest raw rows in chat-history form, so a restart
        resumes with the recent exchanges instead of an empty history."""
        rows = await self.db.get_conversation_history(campaign, session_id, limit=limit)
        messages: List[dict] = []
        actions: List[dict] = []

        def flush():
            if actions:
                messages.append({"role": "assistant", "content": json.dumps({"actions": list(actions)})})
                actions.clear()

        for row in rows:
            if row["role"] == "assistant":
                try:
                    actions.append(json.loads(row["content"]))
                except json.JSONDecodeError:
                    actions.append({"type": "narrate", "text": row["content"]})
                continue
            flush()
            if row["role"] == "user":
                messages.append({"role": "user", "content": row["content"]})
            elif row["role"] == "event":
                messages.append({"role": "user", "content": f"[Event] {row['content']}"})
        flush()
        # History must open on a player message, not on half an exchange.
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        return messages

    async def canon_candidates(self, campaign: str, session_id: str, recap: str) -> List[str]:
        """Source material for end-of-session canon proposals: the session
        recap plus the durable facts this session established."""
        nodes = (await self._load(campaign))["nodes"]
        session_nodes = {n["id"] for n in nodes if n["session_id"] == session_id}
        facts = await self.db.get_open_memory_facts(campaign, limit=200)
        candidates = [recap] if recap else []
        candidates += [f"[{f['kind']}] {f['text']}" for f in facts if f["node_id"] in session_nodes]
        return candidates

    # --- compaction (off the turn path) -------------------------------------

    async def maybe_compact(
        self, campaign: str, session_id: str, include_partial: bool = False, force: bool = False,
    ) -> int:
        """Compact every full run of N player turns not yet compacted (and
        the shorter tail too, with include_partial). Returns nodes written.

        Skipped while backing off from a failure unless force (an explicit
        request rather than the per-turn trigger). Cancellation is safe: the
        caller cancels this to give a player turn the model, and nothing is
        written until the model has replied."""
        if not (campaign and session_id):
            return 0
        if not force and time.monotonic() < self._retry_at:
            return 0
        async with self._lock:
            return await self._compact_pending(campaign, session_id, include_partial)

    async def session_notes(self, campaign: str, session_id: str) -> str:
        """This session's summaries so far, without closing it."""
        await self.maybe_compact(campaign, session_id, include_partial=True, force=True)
        nodes = (await self._load(campaign))["nodes"]
        return "\n\n".join(
            n["summary"] for n in nodes if n["session_id"] == session_id and n["level"] == 1 and n["summary"]
        )

    async def close_session(self, campaign: str, session_id: str) -> str:
        """Compact what is left of the session, then write its level-2 recap.
        Returns the recap text ("" when the session has no play in it)."""
        if not (campaign and session_id):
            return ""
        async with self._lock:
            return await self._close(campaign, session_id)

    async def rebuild(self, campaign: str) -> int:
        """Drop the campaign's summaries and facts and compact the raw log
        again. Returns the number of nodes written."""
        async with self._lock:
            await self.db.delete_derived_memory(campaign)
            active = await self.db.get_active_session()
            for session_id in await self.db.get_campaign_sessions_in_order(campaign):
                if session_id == active:
                    await self._compact_pending(campaign, session_id, include_partial=False)
                else:
                    await self._close(campaign, session_id)
            return len((await self._load(campaign))["nodes"])

    async def _close(self, campaign: str, session_id: str) -> str:
        await self._compact_pending(campaign, session_id, include_partial=True)
        nodes = (await self._load(campaign))["nodes"]
        existing = [n for n in nodes if n["session_id"] == session_id and n["level"] == 2]
        if existing:
            return existing[-1]["summary"]
        level1 = [n for n in nodes if n["session_id"] == session_id and n["level"] == 1 and n["summary"]]
        if not level1:
            return ""

        notes = "\n\n".join(n["summary"] for n in level1)
        data = await self._ask(SESSION_PROMPT, notes) if len(level1) > 1 else None
        if data:
            summary, topics = data["summary"], _clean_topics(data.get("topics"))
        else:
            # One segment, or the model is unavailable: the level-1 notes are
            # already model-written summaries, so joining them is honest.
            summary = notes[:3000]
            topics = _clean_topics([t for n in level1 for t in n["topics"]])
        await self.db.add_memory_node(
            campaign, session_id, 2, level1[0]["first_raw_id"], level1[-1]["last_raw_id"], summary, topics,
        )
        return summary

    async def _compact_pending(self, campaign: str, session_id: str, include_partial: bool) -> int:
        written = 0
        while True:
            nodes = (await self._load(campaign))["nodes"]
            done = max(
                (n["last_raw_id"] for n in nodes if n["session_id"] == session_id and n["level"] == 1),
                default=0,
            )
            rows = await self.db.get_raw_after(campaign, session_id, done, MAX_ROWS_PER_PASS)
            segment = self._next_segment(rows, include_partial)
            if not segment:
                return written
            if not await self._compact_segment(campaign, session_id, segment):
                # Nothing is written on failure: the rows stay pending and the
                # next pass tries again. A made-up summary would be worse.
                self._failures += 1
                self._retry_at = time.monotonic() + min(
                    BACKOFF_BASE_S * 2 ** (self._failures - 1), BACKOFF_MAX_S,
                )
                return written
            self._failures, self._retry_at = 0, 0.0
            written += 1

    def _next_segment(self, rows: List[dict], include_partial: bool) -> List[dict]:
        """The first N player turns of `rows`, running up to the next player
        message so a turn's GM reply stays with it. A turn's rows are written
        together (_record_exchange), so N turns at the end of `rows` are
        complete."""
        turns = 0
        for i, row in enumerate(rows):
            if row["role"] == "user":
                turns += 1
                if turns > self.every:
                    return rows[:i]
        if turns >= self.every or include_partial or len(rows) >= MAX_ROWS_PER_PASS:
            return rows
        return []

    async def _compact_segment(self, campaign: str, session_id: str, rows: List[dict]) -> bool:
        transcript = "\n".join(line for line in map(_render_row, rows) if line)
        if not transcript:
            # Nothing but legacy summary rows: mark them covered (blank
            # summary, never shown) so they aren't re-read on every pass.
            await self.db.add_memory_node(campaign, session_id, 1, rows[0]["id"], rows[-1]["id"], "", [])
            return True

        open_facts = (await self._load(campaign))["facts"]
        if open_facts:
            transcript = "OPEN FACTS:\n" + "\n".join(
                f"#{f['id']} [{f['kind']}] {f['text']}" for f in open_facts
            ) + "\n\nTRANSCRIPT:\n" + transcript

        data = await self._ask(COMPACT_PROMPT, transcript)
        if not data:
            return False
        # Shielded: a player turn may cancel compaction at any await, and a
        # cancel between these writes would keep the summary but drop its facts.
        await asyncio.shield(self._store(campaign, session_id, rows, data, open_facts))
        return True

    async def _store(self, campaign: str, session_id: str, rows: List[dict], data: dict, open_facts: List[dict]):
        node_id = await self.db.add_memory_node(
            campaign, session_id, 1, rows[0]["id"], rows[-1]["id"],
            data["summary"], _clean_topics(data.get("topics")),
        )
        await self.db.add_memory_facts(campaign, node_id, _clean_facts(data.get("facts")))
        open_ids = {f["id"] for f in open_facts}
        resolved = data.get("resolved") if isinstance(data.get("resolved"), list) else []
        await self.db.resolve_memory_facts(campaign, [
            int(i) for i in resolved
            # Models write ids as "3" as often as 3; and True == 1 in Python.
            if not isinstance(i, bool) and str(i).strip().isdigit() and int(i) in open_ids
        ])

    async def _ask(self, system_prompt: str, context: str) -> Optional[dict]:
        try:
            reply = await self.llm.generate_text(
                user_message="Compact this now.", system_prompt=system_prompt, context=context,
            )
        except Exception as e:
            logger.warning(f"[Memory] Compaction call failed: {e}")
            return None
        data = _parse_json(reply or "")
        summary = data.get("summary") if data else None
        if not (isinstance(summary, str) and summary.strip()):
            logger.warning("[Memory] Compaction reply had no usable summary")
            return None
        data["summary"] = summary.strip()[:1500]
        return data
