"""Enriching an existing campaign's world and lore from additional sources.

Mixin composed into CampaignOrchestrator like WorldImportMixin. Where import
builds a campaign from one source, this folds further sources (another PDF,
markdown notes, Foundry journals) into a campaign that already exists, without
regenerating it: see campaign/enrichment.py for the merge rules.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from campaign.enrichment import (
    LORE_SECTIONS,
    append_section,
    build_delta_prompt,
    build_entities_prompt,
    drop_conflicting_lines,
    load_sources,
    lore_view,
    merge_entities,
    norm_name,
    parse_delta_response,
    source_id,
    strip_section,
)
from campaign.importer import (
    build_pass1_prompt,
    build_pass1_user,
    chunk_pages,
    journal_entries_to_pages,
)
from campaign.vault import CampaignNotFound, CampaignStore

logger = logging.getLogger(__name__)

# A source's notes are fed to the world/history delta call in groups no bigger
# than this. Its answer is the NEW lore in them, so it grows with the group: at
# 93k chars of a two-pack module it outgrew max_tokens and was cut off inside
# the WORLD section, losing HISTORY and CONFLICTS with it. A group whose answer
# is still cut off is redone in halves.
_NOTES_GROUP_CHARS = 48_000
# Entity extraction answers with every NPC, place, faction and artifact in its
# input, so its OUTPUT grows with the input: at 120k chars (25k tokens) of a
# two-pack module the answer outgrew max_tokens (16k) — and a model that
# reasons first can spend the whole budget before answering. Much smaller
# batches, split further if one still comes back unusable.
_ENTITY_GROUP_CHARS = 24_000
_NOTES_SEPARATOR = "\n\n---\n\n"
# A single note is split in half (at a paragraph) when its answer still won't
# fit: one note of a dense module listed more entities than 16k tokens hold.
# Below twice this, a note is retried instead of split.
_MIN_SPLIT_CHARS = 4_000


def _halve(notes: List[str]) -> Optional[Tuple[List[str], List[str]]]:
    """Two halves of a batch of notes: by note, or a lone note by its text at
    the paragraph break nearest the middle. None when too small to split."""
    if len(notes) > 1:
        mid = len(notes) // 2
        return notes[:mid], notes[mid:]
    text = notes[0]
    if len(text) < 2 * _MIN_SPLIT_CHARS:
        return None
    mid = len(text) // 2
    cut = text.rfind("\n\n", 0, mid + 2)  # + 2: a break that starts at mid counts
    if cut < len(text) // 4:
        cut = text.find("\n\n", mid)
    if cut <= 0 or cut > len(text) * 3 // 4:
        cut = mid
    return [text[:cut].strip()], [text[cut:].strip()]

# Returns False when it did not queue the conflict; anything else counts as queued.
ConflictSink = Callable[[str, str, str], Awaitable[Any]]


class LoreEnrichmentMixin:
    """Folding new sources into an existing campaign's world and lore."""

    async def enrich_campaign(
        self,
        campaign_name: str,
        llm_client,
        source_path: Optional[str] = None,
        journal_pack: Optional[str] = None,
        journal_folder: Optional[str] = None,
        foundry_client=None,
        vault_path: Optional[str] = None,
        force: bool = False,
        on_progress: Optional[Callable] = None,
        conflict_sink: Optional[ConflictSink] = None,
    ) -> Dict[str, Any]:
        """Fold each source into the campaign and report what changed.

        Existing content wins: gaps are filled and new entities added, while
        anything that disagrees goes to conflict_sink(claim, rationale,
        existing_text) rather than being applied. A source already recorded in
        campaign.json["sources"] is skipped unless force. Each source is saved
        as soon as it is done, so a failure loses only the source in progress.
        """
        result: Dict[str, Any] = {"status": "ok", "sources": [], "skipped": [], "conflicts": 0}

        def progress(msg: str) -> None:
            logger.info(f"[Enrich] {msg}")
            if on_progress:
                try:
                    on_progress(msg, "enrich", "")
                except Exception:
                    logger.debug("on_progress callback raised", exc_info=True)

        store = CampaignStore(campaign_name, vault_path)
        try:
            data = await store.load()
        except CampaignNotFound:
            return {**result, "status": "error", "error": f"Campaign '{campaign_name}' not found in the vault"}

        try:
            sources = await self._enrich_collect_sources(
                source_path, journal_pack, journal_folder, foundry_client)
        except (FileNotFoundError, ValueError) as e:
            return {**result, "status": "error", "error": str(e)}
        if not sources:
            return {**result, "status": "error", "error": "No readable sources found (PDF, .md or .txt files, or Foundry journals)"}

        done = {s.get("id") for s in data.get("sources", [])}
        endpoint = self._chat_endpoint()
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}", "Content-Type": "application/json"}
        world_md = await self._read_lore_file(store, "Worldbuilding.md")
        history_md = await self._read_lore_file(store, "History.md")

        for src in sources:
            if src["id"] in done and not force:
                progress(f"Skipping '{src['title']}': already added (use force to redo)")
                result["skipped"].append(src["id"])
                continue
            progress(f"Reading '{src['title']}' ({len(src['pages'])} pages)")
            try:
                summary = await self._enrich_one_source(
                    src, data, store, world_md, history_md, llm_client, endpoint, headers,
                    conflict_sink, progress)
            except Exception as e:
                # Sources before this one are already saved. Stop here: a failure
                # is usually the LLM or Foundry being down, which the rest would hit too.
                logger.exception(f"[Enrich] '{src['title']}' failed")
                result["error"] = f"'{src['title']}' failed: {type(e).__name__}"
                result["status"] = "partial" if result["sources"] else "error"
                break
            world_md, history_md = summary.pop("_world"), summary.pop("_history")
            result["sources"].append(summary)
            result["conflicts"] += summary["conflicts"]

        return result

    async def _enrich_collect_sources(self, source_path, journal_pack, journal_folder, foundry_client):
        sources: List[Dict[str, Any]] = []
        if source_path:
            sources += await asyncio.to_thread(load_sources, source_path)
        for label, fetch in ((journal_pack, self._fetch_journal_pack), (journal_folder, self._fetch_world_journals)):
            if not label:
                continue
            if foundry_client is None:
                raise ValueError("A connected Foundry client is required to read journals")
            pages = journal_entries_to_pages(await fetch(foundry_client, label))
            if pages:
                sources.append({"id": source_id(label), "title": label, "type": "foundry",
                                "path": label, "pages": pages})
        return sources

    @staticmethod
    async def _read_lore_file(store: CampaignStore, name: str) -> str:
        path = store.folder / name
        if not await asyncio.to_thread(path.exists):
            return ""
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    @staticmethod
    async def _write_text(path, text: str) -> None:
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")

    async def _enrich_one_source(self, src, data, store, world_md, history_md, llm_client,
                                 endpoint, headers, conflict_sink, progress) -> Dict[str, Any]:
        sid, title = src["id"], src["title"]
        chunks = chunk_pages(src["pages"])
        notes: List[str] = []
        notes_dir = store.folder / "Lore" / "Sources" / sid
        await asyncio.to_thread(self._reset_notes_dir, notes_dir)
        for i, chunk in enumerate(chunks, 1):
            payload = {"model": self.settings.model,
                       "messages": [{"role": "system", "content": build_pass1_prompt(chunk)},
                                    {"role": "user", "content": build_pass1_user(chunk)}],
                       "temperature": 0.3, "max_tokens": 8192}
            self._suppress_thinking(payload)
            resp = await llm_client.post(endpoint, headers=headers, json=payload,
                                         timeout=self.settings.campaign_gen_timeout)
            resp.raise_for_status()
            text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            notes.append(text)
            await self._write_text(notes_dir / f"{i:02d}.md", f"# {title} — notes {i}/{len(chunks)}\n\n{text}\n")
            progress(f"  '{title}': chunk {i}/{len(chunks)} notes extracted")

        totals = {"added": 0, "enriched": 0}
        per_section = {s: {"added": 0, "enriched": 0} for s in LORE_SECTIONS}
        changed: Dict[str, Dict[str, Any]] = {s: {} for s in LORE_SECTIONS}
        conflicts: List[Dict[str, str]] = []
        world_added = history_added = False
        # A forced re-run replaces this source's earlier section: work out what
        # is new against the documents without it, and put it back if nothing is.
        world_before, history_before = world_md, history_md
        world_md, history_md = strip_section(world_md, title), strip_section(history_md, title)
        queue = self._batch_notes(notes, _NOTES_GROUP_CHARS)
        while queue:
            group = queue.pop(0)
            w_add, h_add, delta_conflicts, cut_off = await self._enrich_delta(
                llm_client, endpoint, headers, world_md, history_md, _NOTES_SEPARATOR.join(group), title)
            halves = _halve(group) if cut_off else None
            if halves:
                # Cut off inside WORLD loses HISTORY and CONFLICTS with it:
                # redo this group as two smaller ones instead of keeping part.
                progress(f"  '{title}': world/history additions were cut off; redoing them in halves")
                queue[:0] = list(halves)
                continue
            w_add, h_add = (drop_conflicting_lines(t, delta_conflicts) for t in (w_add, h_add))
            world_md, history_md = append_section(world_md, w_add, title), append_section(history_md, h_add, title)
            world_added |= bool(w_add.strip())
            history_added |= bool(h_add.strip())
            conflicts += [{"entity": "", "field": "world", **c} for c in delta_conflicts]

        results: List[Dict[str, Any]] = []
        for batch in self._batch_notes(notes, _ENTITY_GROUP_CHARS):
            results += await self._enrich_entities_split(llm_client, endpoint, headers, batch, progress)
        for incoming in results:
            for section in LORE_SECTIONS:
                items = [i for i in incoming.get(section, []) if isinstance(i, dict)]
                existing = data.setdefault(section, [])
                aliases = await self._enrich_aliases(llm_client, section, existing, items)
                before = {e.get("name"): e for e in existing}
                data[section], stats, found = merge_entities(existing, items, sid, aliases)
                changed[section].update({e["name"]: e for e in data[section]
                                         if e["name"] not in before or lore_view(before[e["name"]]) != lore_view(e)})
                for k in totals:
                    totals[k] += stats[k]
                    per_section[section][k] += stats[k]
                conflicts += [{"field": section, **c} for c in found]

        world_md = world_md if world_added else world_before
        history_md = history_md if history_added else history_before
        data["sources"] = [s for s in data.get("sources", []) if s.get("id") != sid] + [{
            "id": sid, "title": title, "type": src["type"], "path": src["path"],
            "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}]
        await self._enrich_save(store, data, world_md, history_md, world_added, history_added, changed)
        queued = sum([await self._enrich_report(conflict_sink, title, c) for c in conflicts])
        progress(f"'{title}': {totals['added']} added, {totals['enriched']} enriched, {queued} conflict(s) queued")
        return {"id": sid, "title": title, "chunks": len(chunks), "world_added": world_added,
                "history_added": history_added, "entities": per_section, "conflicts": queued,
                "_world": world_md, "_history": history_md}

    @staticmethod
    def _reset_notes_dir(notes_dir) -> None:
        """An empty Lore/Sources/<id>/, so a re-run with fewer chunks leaves no stale notes."""
        notes_dir.mkdir(parents=True, exist_ok=True)
        for old in notes_dir.glob("*.md"):
            old.unlink()

    @staticmethod
    def _batch_notes(notes: List[str], limit: int) -> List[List[str]]:
        """Consecutive notes in batches of at most `limit` characters (a single
        note bigger than that is a batch of its own)."""
        batches, current, size = [], [], 0
        for n in notes:
            if current and size + len(n) > limit:
                batches.append(current)
                current, size = [], 0
            current.append(n)
            size += len(n)
        return batches + ([current] if current else [])

    async def _enrich_entities_split(self, llm_client, endpoint, headers, notes: List[str],
                                     progress) -> List[Dict[str, Any]]:
        """Entities from `notes`, halving the batch when its answer can't be
        used — by note, and a lone note by its text. Repeating the same request
        only fails the same way when the cause is size (finish_reason=length),
        so anything that can still be split gets one attempt; a piece too small
        to split gets the usual retries before the error stands."""
        halves = _halve(notes)
        try:
            return [await self._enrich_entities(llm_client, endpoint, headers,
                                                _NOTES_SEPARATOR.join(notes),
                                                max_attempts=1 if halves else 3)]
        except Exception as e:
            if not halves:
                raise
            size = sum(map(len, notes))
            progress(f"  entity batch ({len(notes)} note(s), {size} chars) came back unusable "
                     f"({type(e).__name__}); splitting it")
            return (await self._enrich_entities_split(llm_client, endpoint, headers, halves[0], progress)
                    + await self._enrich_entities_split(llm_client, endpoint, headers, halves[1], progress))

    async def _enrich_delta(self, llm_client, endpoint, headers, world_md, history_md, notes, title):
        system, user = build_delta_prompt(world_md, history_md, notes, title)
        payload = {"model": self.settings.model,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "temperature": 0.4, "max_tokens": 16384}
        self._suppress_thinking(payload)
        resp = await llm_client.post(endpoint, headers=headers, json=payload,
                                     timeout=self.settings.campaign_gen_timeout)
        resp.raise_for_status()
        choice = resp.json().get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "") or ""
        cut_off = choice.get("finish_reason") == "length"
        if cut_off:
            logger.warning(
                f"[Enrich] '{title}': world/history additions hit max_tokens "
                + ("with no answer (the model spent the budget reasoning)" if not content.strip() else "mid-answer")
            )
        return (*parse_delta_response(content), cut_off)

    async def _enrich_entities(self, llm_client, endpoint, headers, notes, max_attempts: int = 3) -> Dict[str, Any]:
        system, user = build_entities_prompt(notes)
        payload = {"model": self.settings.model,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "temperature": 0.3, "max_tokens": 16384}
        self._suppress_thinking(payload)
        return await self._post_and_parse_campaign_json(llm_client, endpoint, headers, payload,
                                                        max_attempts=max_attempts)

    async def _enrich_aliases(self, llm_client, section, existing, incoming) -> Dict[str, str]:
        """incoming name -> existing name for entities that are the same thing
        under a different name ("Kansaldi" / "Kansaldi Fire-Eyes"). Only names
        that do not already match exactly are sent to the LLM."""
        # _semantic_match_names words its prompt for NPCs ("npc") or locations
        # ("scene"); factions and artifacts would be described to the model as
        # NPCs, so they match on normalized name only.
        kind = {"npcs": "npc", "locations": "scene"}.get(section)
        have = {norm_name(e.get("name", "")) for e in existing}
        unmatched = [i for i in incoming if i.get("name") and norm_name(i["name"]) not in have]
        if kind is None or not unmatched or not existing:
            return {}
        candidates = [{"name": e["name"], "uuid": e["name"]} for e in existing if e.get("name")]
        return await self._semantic_match_names(llm_client, kind, unmatched, candidates)

    async def _enrich_save(self, store, data, world_md, history_md, world_added, history_added, changed):
        from campaign import obsidian_sync as vault

        if world_added:
            await self._write_text(store.folder / "Worldbuilding.md", world_md)
        if history_added:
            await self._write_text(store.folder / "History.md", history_md)
        await store.save(data)
        # Re-render only what this run touched; scenes, quests, loot and the
        # rest are not lore and are left exactly as they are. NPC and location
        # notes are one file per entity, so only the changed ones are written;
        # factions and artifacts are a single file each.
        if changed["npcs"]:
            await vault.save_npc_notes(store.folder, {"npcs": list(changed["npcs"].values())})
        if changed["locations"]:
            await vault.save_location_notes(store.folder, {"locations": list(changed["locations"].values())})
        if changed["factions"]:
            await vault.save_factions(store.folder, {"factions": data["factions"]})
        if changed["artifacts"]:
            await vault.save_artifacts(store.folder, {"artifacts": data["artifacts"]})

    async def _enrich_report(self, sink: Optional[ConflictSink], title: str, conflict: Dict[str, str]) -> bool:
        """Hand one conflict to the sink; True if it was queued. A sink returns
        False for one it did not record (already pending, or nowhere to put it)."""
        if sink is None:
            return False
        who = f"{conflict['entity']} — {conflict['field']}" if conflict.get("entity") else conflict.get("field", "world")
        try:
            return (await sink(conflict.get("claim", ""), f"From source '{title}' ({who})",
                               conflict.get("existing", ""))) is not False
        except Exception:
            logger.warning("[Enrich] Could not record a conflict for review", exc_info=True)
            return False
