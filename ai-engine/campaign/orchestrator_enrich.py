"""Enriching an existing campaign's world and lore from additional sources.

Mixin composed into CampaignOrchestrator like WorldImportMixin. Where import
builds a campaign from one source, this folds further sources (another PDF,
markdown notes, Foundry journals) into a campaign that already exists, without
regenerating it: see campaign/enrichment.py for the merge rules.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from campaign.enrichment import (
    LORE_SECTIONS,
    append_section,
    build_delta_prompt,
    build_entities_prompt,
    drop_conflicting_lines,
    load_sources,
    merge_entities,
    parse_delta_response,
    source_id,
)
from campaign.importer import (
    build_pass1_prompt,
    build_pass1_user,
    chunk_pages,
    journal_entries_to_pages,
)
from campaign.vault import CampaignNotFound, CampaignStore

logger = logging.getLogger(__name__)

# A source's notes are fed to the delta/entity calls in groups no bigger than
# this, so a whole sourcebook never overflows the model's context in one call.
_NOTES_GROUP_CHARS = 120_000

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
        world_md = self._read_lore_file(store, "Worldbuilding.md")
        history_md = self._read_lore_file(store, "History.md")

        for src in sources:
            if src["id"] in done and not force:
                progress(f"Skipping '{src['title']}': already added (use force to redo)")
                result["skipped"].append(src["id"])
                continue
            progress(f"Reading '{src['title']}' ({len(src['pages'])} pages)")
            summary = await self._enrich_one_source(
                src, data, store, world_md, history_md, llm_client, endpoint, headers,
                conflict_sink, progress)
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
    def _read_lore_file(store: CampaignStore, name: str) -> str:
        path = store.folder / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    async def _enrich_one_source(self, src, data, store, world_md, history_md, llm_client,
                                 endpoint, headers, conflict_sink, progress) -> Dict[str, Any]:
        sid, title = src["id"], src["title"]
        chunks = chunk_pages(src["pages"])
        notes: List[str] = []
        notes_dir = store.folder / "Lore" / "Sources" / sid
        notes_dir.mkdir(parents=True, exist_ok=True)
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
            (notes_dir / f"{i:02d}.md").write_text(f"# {title} — notes {i}/{len(chunks)}\n\n{text}\n", encoding="utf-8")
            progress(f"  '{title}': chunk {i}/{len(chunks)} notes extracted")

        totals = {"added": 0, "enriched": 0}
        per_section = {s: {"added": 0, "enriched": 0} for s in LORE_SECTIONS}
        changed: Dict[str, Dict[str, Any]] = {s: {} for s in LORE_SECTIONS}
        conflicts: List[Dict[str, str]] = []
        world_added = history_added = False
        for group in self._group_notes(notes):
            w_add, h_add, delta_conflicts = await self._enrich_delta(
                llm_client, endpoint, headers, world_md, history_md, group, title)
            w_add, h_add = (drop_conflicting_lines(t, delta_conflicts) for t in (w_add, h_add))
            world_md, history_md = append_section(world_md, w_add, title), append_section(history_md, h_add, title)
            world_added |= bool(w_add.strip())
            history_added |= bool(h_add.strip())
            conflicts += [{"entity": "", "field": "world", **c} for c in delta_conflicts]

            incoming = await self._enrich_entities(llm_client, endpoint, headers, group)
            for section in LORE_SECTIONS:
                items = [i for i in incoming.get(section, []) if isinstance(i, dict)]
                existing = data.setdefault(section, [])
                aliases = await self._enrich_aliases(llm_client, section, existing, items)
                before = {e.get("name"): e for e in existing}
                data[section], stats, found = merge_entities(existing, items, sid, aliases)
                changed[section].update({e["name"]: e for e in data[section] if before.get(e["name"]) != e})
                for k in totals:
                    totals[k] += stats[k]
                    per_section[section][k] += stats[k]
                conflicts += [{"field": section, **c} for c in found]

        data.setdefault("sources", [])
        data["sources"] = [s for s in data["sources"] if s.get("id") != sid] + [{
            "id": sid, "title": title, "type": src["type"], "path": src["path"],
            "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}]
        await self._enrich_save(store, data, world_md, history_md, world_added, history_added, changed)
        for c in conflicts:
            await self._enrich_report(conflict_sink, title, c)
        progress(f"'{title}': {totals['added']} added, {totals['enriched']} enriched, {len(conflicts)} conflict(s)")
        return {"id": sid, "title": title, "chunks": len(chunks), "world_added": world_added,
                "history_added": history_added, "entities": per_section, "conflicts": len(conflicts),
                "_world": world_md, "_history": history_md}

    @staticmethod
    def _group_notes(notes: List[str]) -> List[str]:
        groups, current = [], ""
        for n in notes:
            if current and len(current) + len(n) > _NOTES_GROUP_CHARS:
                groups.append(current)
                current = ""
            current += ("\n\n---\n\n" if current else "") + n
        return groups + ([current] if current else [])

    async def _enrich_delta(self, llm_client, endpoint, headers, world_md, history_md, notes, title):
        system, user = build_delta_prompt(world_md, history_md, notes, title)
        payload = {"model": self.settings.model,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "temperature": 0.4, "max_tokens": 16384}
        self._suppress_thinking(payload)
        resp = await llm_client.post(endpoint, headers=headers, json=payload,
                                     timeout=self.settings.campaign_gen_timeout)
        resp.raise_for_status()
        return parse_delta_response(resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or "")

    async def _enrich_entities(self, llm_client, endpoint, headers, notes) -> Dict[str, Any]:
        system, user = build_entities_prompt(notes)
        payload = {"model": self.settings.model,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "temperature": 0.3, "max_tokens": 16384}
        self._suppress_thinking(payload)
        return await self._post_and_parse_campaign_json(llm_client, endpoint, headers, payload)

    async def _enrich_aliases(self, llm_client, section, existing, incoming) -> Dict[str, str]:
        """incoming name -> existing name for entities that are the same thing
        under a different name ("Kansaldi" / "Kansaldi Fire-Eyes"). Only names
        that do not already match exactly are sent to the LLM."""
        from campaign.enrichment import _norm_scene
        have = {_norm_scene(e.get("name", "")) for e in existing}
        unmatched = [i for i in incoming if i.get("name") and _norm_scene(i["name"]) not in have]
        if not unmatched or not existing:
            return {}
        # _semantic_match_names words its prompt for NPCs ("npc") or locations
        # ("scene"); factions and artifacts would be described to the model as
        # NPCs, so they match on normalized name only.
        kind = {"npcs": "npc", "locations": "scene"}.get(section)
        if kind is None:
            return {}
        candidates = [{"name": e["name"], "uuid": e["name"]} for e in existing if e.get("name")]
        return await self._semantic_match_names(llm_client, kind, unmatched, candidates)

    async def _enrich_save(self, store, data, world_md, history_md, world_added, history_added, changed):
        from campaign import obsidian_sync as vault

        if world_added:
            (store.folder / "Worldbuilding.md").write_text(world_md, encoding="utf-8")
        if history_added:
            (store.folder / "History.md").write_text(history_md, encoding="utf-8")
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

    async def _enrich_report(self, sink: Optional[ConflictSink], title: str, conflict: Dict[str, str]) -> None:
        if sink is None:
            return
        who = f"{conflict['entity']} — {conflict['field']}" if conflict.get("entity") else conflict.get("field", "world")
        try:
            await sink(conflict.get("claim", ""), f"From source '{title}' ({who})", conflict.get("existing", ""))
        except Exception:
            logger.warning("[Enrich] Could not record a conflict for review", exc_info=True)
