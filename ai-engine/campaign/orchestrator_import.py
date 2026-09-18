"""Reading an existing Foundry world back into campaign structure.

Extracted verbatim from campaign/orchestrator.py, which had grown to 4,469
lines in a single class — 9x this project's 500-line limit. Mixins rather than
free functions so every `self.` call inside these methods keeps working
unchanged; CampaignOrchestrator composes them.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Set, Tuple

from utils.path_safety import sanitize_filename
from campaign.importer import (
    _PASS3_SYSTEM,
    build_pass3_user,
    filter_candidates_by_campaign_folder,
    match_maps_to_scenes,
    match_names_to_existing,
    match_scenes_to_existing,
    match_tokens_to_npcs,
    parse_pass3_response,
    prepare_handouts,
)

logger = logging.getLogger(__name__)


class WorldImportMixin:
    """Reading an existing Foundry world back into campaign structure."""

    async def import_campaign(
        self,
        source_path: str,
        campaign_name: str,
        llm_client=None,
        foundry_client=None,
        vault_path: str = None,
        comfyui_url: str = None,
        omlx_url: str = None,
        omlx_api_key: str = None,
        on_progress: Callable = None,
        level_range: str = "1-5",
        journal_pack: str = None,
    ) -> Dict[str, Any]:
        """Import a published campaign folder into the AI GM pipeline.

        Scans the folder, extracts lore from adventure PDFs (or, if
        journal_pack is given, from an already-imported Foundry JournalEntry
        compendium pack) via LLM, matches pre-made maps/tokens to
        scenes/NPCs, writes lore .md files into the vault, then delegates to
        build_campaign with pre-built campaign_data.

        Args:
            source_path: Path to the product folder (adventure PDFs + Maps/ + Tokens/).
            campaign_name: Name for the imported campaign.
            llm_client: httpx.AsyncClient for LLM calls.
            foundry_client: Connected FoundryClient instance.
            vault_path: Obsidian vault path.
            comfyui_url: ComfyUI URL.
            omlx_url: oMLX API URL.
            omlx_api_key: oMLX API key.
            on_progress: Optional callback(msg, step, detail).
            level_range: Target level range for the campaign.
            journal_pack: Name/collection id of a Foundry JournalEntry
                compendium pack (e.g. one created by DDBImporter) to read
                adventure text from instead of an adventure PDF. Requires a
                connected foundry_client with the execute-js scope enabled.
        """
        from campaign.importer import (
            scan_product_folder,
            extract_pdf_text,
            journal_entries_to_pages,
            chunk_pages,
            build_pass1_prompt,
            build_pass1_user,
            build_pass2_user,
            build_pass2_chapter_user,
            _PASS2_SYSTEM,
            format_rolltables_for_notes,
            checkpoint_matches_run,
        )
        from campaign.generator import CAMPAIGN_GENERATOR_PROMPT, validate_campaign
        import httpx

        result: Dict[str, Any] = {
            "status": "importing",
            "campaign_name": campaign_name,
            "steps": [],
            "import_summary": {},
        }

        def progress(msg: str, step: str = "", detail: str = ""):
            result["steps"].append({"message": msg, "step": step, "detail": detail})
            logger.info(f"[Import] {msg}")
            if on_progress:
                try:
                    on_progress(msg, step, detail)
                except Exception:
                    pass

        owns_client = llm_client is None
        # Own only what we create: the HTTP routes pass their own client and
        # close it themselves, so closing it here would reach into the
        # caller's resource.
        if owns_client:
            llm_client = httpx.AsyncClient(timeout=300)

        try:
            # ── Step 1: Scan product folder ──
            progress("📂 Scanning product folder...", step="scan")
            scan = scan_product_folder(source_path)
            if scan.get("errors"):
                for err in scan["errors"]:
                    progress(f"  ⚠️ {err}", step="scan")
                result["status"] = "error"
                result["error"] = "; ".join(scan["errors"])
                return result
            progress(
                f"✅ Found {len(scan['adventure_pdfs'])} PDF(s), "
                f"{len(scan['maps'])} map(s), {len(scan['tokens'])} token(s), "
                f"{len(scan['handouts'])} handout(s)",
                step="scan",
            )

            # ── Step 2: Extract adventure text, grouped by chapter ──
            # Each group is (chapter_label, pages). journal_pack gives one
            # group per real book chapter/appendix (each is its own
            # JournalEntry); the PDF path has no chapter boundaries, so it's
            # a single group covering everything (unchanged behavior there).
            chapter_groups: List[Tuple[str, List[Tuple[int, str]]]] = []
            use_foundry_text = bool(journal_pack) or (
                foundry_client is not None and not scan["adventure_pdfs"]
            )
            if use_foundry_text:
                if foundry_client is None:
                    result["status"] = "error"
                    result["error"] = "A connected Foundry client is required to read journal text."
                    return result
                await self._wait_for_foundry_ready(foundry_client)
                # Prefer the campaign's WORLD journals over a compendium pack:
                # DDBImporter populates them directly, so no manual "export to
                # journal" step is needed, and on a real world they carried
                # ~1.25M characters of adventure text against the pack's 739K
                # for the same book. Falls back to the named pack when the
                # world has no journals filed under this campaign.
                progress("📖 Reading adventure text from Foundry journals...", step="extract")
                text_source = "world journals"
                entries = await self._fetch_world_journals(foundry_client, campaign_name)
                if not entries and journal_pack:
                    text_source = f"compendium pack '{journal_pack}'"
                    progress(
                        f"  ↩︎ No world journals under '{campaign_name}' — "
                        f"falling back to {text_source}",
                        step="extract",
                    )
                    entries = await self._fetch_journal_pack(foundry_client, journal_pack)
                progress(f"  📖 Source: {text_source}", step="extract")
                raw_page_count = sum(len(e.get("pages", [])) for e in entries)
                for entry in entries:
                    pages = journal_entries_to_pages([entry])
                    if pages:
                        chapter_groups.append((entry.get("name", "Untitled"), pages))
                total_kept = sum(len(pages) for _, pages in chapter_groups)
                progress(
                    f"  📖 {len(entries)} journal entrie(s), {raw_page_count} raw page(s), "
                    f"{total_kept} page(s) kept after filtering, across {len(chapter_groups)} chapter(s)",
                    step="extract",
                )
                total_chars = sum(len(t) for _, pages in chapter_groups for _, t in pages)
                preview = (chapter_groups[0][1][0][1][:300] if chapter_groups and chapter_groups[0][1] else "")
                logger.info(
                    f"[Import] Adventure text from {text_source}: "
                    f"chapters={[name for name, _ in chapter_groups]}, "
                    f"total extracted chars={total_chars}, first page preview={preview!r}"
                )
            else:
                progress("📄 Extracting text from adventure PDFs...", step="extract")
                pdf_pages: List[Tuple[int, str]] = []
                for pdf in scan["adventure_pdfs"]:
                    pages = await asyncio.to_thread(extract_pdf_text, pdf)
                    pdf_pages.extend(pages)
                    progress(f"  📄 {Path(pdf).name}: {len(pages)} pages extracted", step="extract")
                if pdf_pages:
                    chapter_groups.append((campaign_name, pdf_pages))

            if not chapter_groups:
                result["status"] = "error"
                result["error"] = (
                    f"No text could be extracted from journal pack '{journal_pack}'."
                    if journal_pack
                    else "No text could be extracted from the adventure PDFs."
                )
                return result

            # ── Step 3-5: Per-chapter Pass 1 (extract) + Pass 2 (generate/merge) ──
            # One full generate-and-merge cycle per chapter instead of a
            # single whole-book call — a single Pass 2 call was capping
            # output at ~3-5 scenes regardless of how many real chapters/
            # pages were fed in: the verbose schema this system uses doesn't
            # fit a whole 7-chapter campaign in one response, and the model
            # defaults to a short-arc-sized result rather than exhaustively
            # enumerating everything. Each chapter gets its own full token
            # budget and is merged in (tagged with source_chapter), staying
            # strictly extract-only throughout — unlike extend_campaign_arc,
            # which deliberately invents/escalates for a NEW arc, chapters
            # here must never contradict or invent beyond their own notes.
            endpoint = self._chat_endpoint()
            headers = {
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            }

            campaign_data: Dict[str, Any] = {}
            all_notes: List[Tuple[str, str]] = []  # (chapter_label, notes)
            total_pages_extracted = 0
            total_chunks_processed = 0
            resume_from = 0

            # ── Per-chapter checkpoint ──
            # Pass 1+2 across a whole book can run 90+ minutes; a crash
            # anywhere downstream (validation, deploy) used to mean redoing
            # every chapter's generation from scratch. Each chapter's result
            # is checkpointed to disk as soon as it's merged in, so a retry
            # resumes after the last completed chapter instead of chapter 1.
            chapter_labels = [label for label, _ in chapter_groups]
            checkpoint_path = (
                Path("./campaign_assets") / sanitize_filename(campaign_name.lower()) / "import_checkpoint.json"
            )
            if checkpoint_path.exists():
                try:
                    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                except Exception:
                    checkpoint = None
                if checkpoint and checkpoint_matches_run(checkpoint, source_path, chapter_labels):
                    campaign_data = checkpoint["campaign_data"]
                    all_notes = [tuple(pair) for pair in checkpoint["all_notes"]]
                    total_pages_extracted = checkpoint["total_pages_extracted"]
                    total_chunks_processed = checkpoint["total_chunks_processed"]
                    resume_from = checkpoint["chapter_idx"]
                    progress(
                        f"♻️ Resuming from checkpoint: {resume_from}/{len(chapter_groups)} "
                        "chapter(s) already generated",
                        step="pass1",
                    )
                elif checkpoint:
                    logger.info(f"[Import] Ignoring checkpoint at {checkpoint_path}: source/chapters changed")

            # The book's own random encounter / event / rumour tables, filed
            # per chapter by DDBImporter. Appended to each chapter's notes so
            # generated content reflects them instead of inventing parallel
            # tables alongside them.
            rolltables_by_chapter: Dict[str, List[Dict[str, Any]]] = {}
            # The book's canonical area names, taken from the pin labels on
            # each chapter's published maps, so pass 2 names scenes after the
            # source material instead of inventing variations that then have
            # to be fuzzy-matched back.
            areas_by_chapter: Dict[str, List[str]] = {}
            scene_candidates_cache: List[Dict[str, str]] = []
            if foundry_client is not None:
                rolltables_by_chapter = await self._fetch_world_rolltables(
                    foundry_client, campaign_name
                )
                scene_candidates_cache = filter_candidates_by_campaign_folder(
                    await self._fetch_world_document_index(foundry_client, "Scene"),
                    campaign_name,
                )
                for cand in scene_candidates_cache:
                    chapter = (cand.get("folder") or "").split("/")[-1].strip()
                    if not chapter:
                        continue
                    bucket = areas_by_chapter.setdefault(chapter, [])
                    for note in cand.get("notes", []):
                        label = note.get("label", "") if isinstance(note, dict) else str(note)
                        if label and label not in bucket:
                            bucket.append(label)
                if areas_by_chapter:
                    logger.info(
                        "[Import] Canonical map areas per chapter: "
                        f"{ {k: len(v) for k, v in areas_by_chapter.items()} }"
                    )
            MERGE_SECTIONS = (
                "scenes", "npcs", "locations", "quest_logs", "encounters",
                "loot_tables", "loot_piles", "factions", "artifacts", "journal_entries",
            )

            def _save_checkpoint(idx: int) -> None:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint_path.write_text(
                    json.dumps({
                        "source_path": source_path,
                        "chapter_labels": chapter_labels,
                        "chapter_idx": idx,
                        "campaign_data": campaign_data,
                        "all_notes": all_notes,
                        "total_pages_extracted": total_pages_extracted,
                        "total_chunks_processed": total_chunks_processed,
                    }, indent=2),
                    encoding="utf-8",
                )

            for chapter_idx, (chapter_label, pages) in enumerate(chapter_groups, 1):
                if chapter_idx <= resume_from:
                    continue
                progress(
                    f"📖 Chapter {chapter_idx}/{len(chapter_groups)}: {chapter_label}",
                    step="pass1",
                )
                chunks = chunk_pages(pages)
                total_pages_extracted += len(pages)
                total_chunks_processed += len(chunks)
                chapter_notes: List[str] = []
                for i, chunk_text in enumerate(chunks, 1):
                    payload: Dict[str, Any] = {
                        "model": self.settings.model,
                        "messages": [
                            {"role": "system", "content": build_pass1_prompt(chunk_text)},
                            {"role": "user", "content": build_pass1_user(chunk_text)},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 8192,
                    }
                    self._suppress_thinking(payload)
                    resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=600)
                    resp.raise_for_status()
                    notes = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    chapter_notes.append(notes)
                    progress(f"  📝 {chapter_label}: chunk {i}/{len(chunks)} notes extracted", step="pass1")

                chapter_combined_notes = "\n\n---\n\n".join(chapter_notes)

                # Append this chapter's real published tables verbatim (see
                # format_rolltables_for_notes: after pass 1, not through it).
                chapter_tables = rolltables_by_chapter.get(chapter_label, [])
                if chapter_tables:
                    chapter_combined_notes += "\n\n---\n\n" + format_rolltables_for_notes(chapter_tables)
                    progress(
                        f"  🎲 {chapter_label}: folded in {len(chapter_tables)} published roll table(s)",
                        step="pass1",
                    )

                all_notes.append((chapter_label, chapter_combined_notes))

                progress(f"🏗️ {chapter_label}: generating campaign content...", step="pass2")
                if not campaign_data:
                    pass2_payload: Dict[str, Any] = {
                        "model": self.settings.model,
                        "messages": [
                            {"role": "system", "content": _PASS2_SYSTEM + "\n\n" + CAMPAIGN_GENERATOR_PROMPT},
                            {"role": "user", "content": build_pass2_user(
                                chapter_combined_notes, campaign_name, level_range,
                                known_areas=areas_by_chapter.get(chapter_label),
                            )},
                        ],
                        "temperature": self.settings.campaign_gen_temperature,
                        "max_tokens": self.CAMPAIGN_GEN_MAX_TOKENS,
                    }
                    self._suppress_thinking(pass2_payload)
                    campaign_data = await self._post_and_parse_campaign_json(
                        llm_client, endpoint, headers, pass2_payload,
                    )
                    for section in MERGE_SECTIONS:
                        for item in campaign_data.get(section, []):
                            item["source_chapter"] = chapter_label
                else:
                    existing_summary = {
                        "scenes": [s.get("name", "") for s in campaign_data.get("scenes", [])],
                        "NPCs": [n.get("name", "") for n in campaign_data.get("npcs", [])],
                        "locations": [l.get("name", "") for l in campaign_data.get("locations", [])],
                        "factions": [f.get("name", "") for f in campaign_data.get("factions", [])],
                    }
                    chapter_payload: Dict[str, Any] = {
                        "model": self.settings.model,
                        "messages": [
                            {"role": "system", "content": _PASS2_SYSTEM + "\n\n" + CAMPAIGN_GENERATOR_PROMPT},
                            {"role": "user", "content": build_pass2_chapter_user(
                                chapter_combined_notes, campaign_name, level_range,
                                chapter_label, existing_summary,
                                known_areas=areas_by_chapter.get(chapter_label),
                            )},
                        ],
                        "temperature": self.settings.campaign_gen_temperature,
                        "max_tokens": self.CAMPAIGN_GEN_MAX_TOKENS,
                    }
                    self._suppress_thinking(chapter_payload)
                    chapter_data = await self._post_and_parse_campaign_json(
                        llm_client, endpoint, headers, chapter_payload,
                    )
                    for section in MERGE_SECTIONS:
                        campaign_data.setdefault(section, [])
                        for item in chapter_data.get(section, []):
                            item["source_chapter"] = chapter_label
                            campaign_data[section].append(item)

                progress(
                    f"  ✅ {chapter_label}: {len(campaign_data.get('scenes', []))} total scene(s) so far",
                    step="pass2",
                )

                _save_checkpoint(chapter_idx)

            combined_notes = "\n\n---\n\n".join(notes for _, notes in all_notes)

            # ── Dedup recurring entities across chapters ──
            # A faction/NPC/location that recurs through the whole book (the
            # main villain army, a knightly order) gets independently
            # (re)declared by nearly every chapter despite being told what's
            # already extracted — telling the model isn't reliable enough on
            # its own. Verified directly: real duplicate pairs ('Red Dragon
            # Army' vs 'Dragon Army' vs 'The Dragon Armies') scored anywhere
            # from 0.35 to 0.76 on string similarity, overlapping with
            # genuinely-different pairs in the same range, so this needs one
            # batched semantic judgment call per section instead.
            if len(chapter_groups) > 1:
                progress("🧹 Deduping recurring factions/NPCs/locations across chapters...", step="pass2")
                for section, kind in (("factions", "faction"), ("npcs", "NPC"), ("locations", "location")):
                    campaign_data[section] = await self._semantic_dedupe_section(
                        llm_client, kind, campaign_data.get(section, []),
                    )

            # Validate but skip count-refill (counts come from the source)
            warnings = validate_campaign(campaign_data, level_range=level_range)
            for w in warnings:
                logger.warning(f"[Import] Validation: {w}")
            campaign_data["validation_warnings"] = warnings
            campaign_data["imported_from"] = source_path
            progress(
                f"✅ Campaign structure generated across {len(chapter_groups)} chapter(s) "
                f"({len(campaign_data.get('scenes', []))} scenes total)",
                step="pass2",
            )

            wb_md, hist_md = await self._import_worldbuilding(
                llm_client, endpoint, headers, combined_notes, progress
            )
            await self._import_link_existing(
                foundry_client, llm_client, campaign_name, campaign_data,
                scene_candidates_cache, progress,
            )
            store, map_match, token_match, handout_entries = await self._import_match_assets(
                campaign_name, vault_path, campaign_data, scan, progress
            )
            await self._import_write_lore(
                store, all_notes, wb_md, hist_md, handout_entries, progress
            )
            await self._import_upload_handouts(
                foundry_client, store, scan, handout_entries, progress
            )
            # ── Step 10: Delegate to build_campaign ──
            progress("🚀 Running build pipeline with imported data...", step="build")
            try:
                build_result = await self.build_campaign(
                    prompt=f"Imported campaign: {campaign_name}",
                    campaign_name=campaign_name,
                    llm_client=llm_client,
                    foundry_client=foundry_client,
                    vault_path=vault_path,
                    comfyui_url=comfyui_url,
                    omlx_url=omlx_url,
                    omlx_api_key=omlx_api_key,
                    on_progress=on_progress,
                    level_range=level_range,
                    campaign_data=campaign_data,
                )
            except Exception:
                # deploy_to_foundry records existing_uuid/_deployed_uuid on
                # campaign_data itself as each document is created — persist
                # that now (chapter_idx = all chapters done, so a resume
                # skips straight back to this point) so a retry treats
                # whatever this attempt already created as already-linked
                # instead of duplicating it.
                _save_checkpoint(len(chapter_groups))
                raise

            # Merge import summary into result
            build_result["import_summary"] = {
                "source_path": source_path,
                "pdfs_processed": len(scan["adventure_pdfs"]),
                "pages_extracted": total_pages_extracted,
                "chunks_processed": total_chunks_processed,
                "chapters_processed": len(chapter_groups),
                "maps_matched": sorted(map_match["matched_scenes"].keys()),
                "maps_unmatched": map_match["unmatched_scenes"],
                "tokens_matched": sorted(token_match["matched_npcs"].keys()),
                "tokens_unmatched": token_match["unmatched_npcs"],
                "handouts": [e["title"] for e in handout_entries],
                "warnings": map_match["warnings"] + token_match["warnings"],
            }
            build_result["steps"] = result["steps"] + build_result.get("steps", [])
            if checkpoint_path.exists():
                checkpoint_path.unlink()
            return build_result

        except Exception as e:
            logger.exception("Campaign import failed")
            result["status"] = "error"
            result["error"] = str(e)
            return result

        finally:
            if owns_client:
                await llm_client.aclose()


    async def _import_worldbuilding(self, llm_client, endpoint, headers, combined_notes, progress):
        # ── Step 6: Pass 3 — Generate Worldbuilding + History ──
        progress("📚 Pass 3: Generating worldbuilding documents...", step="pass3")
        pass3_payload: Dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": _PASS3_SYSTEM},
                {"role": "user", "content": build_pass3_user(combined_notes)},
            ],
            "temperature": 0.5,
            "max_tokens": 16384,
        }
        self._suppress_thinking(pass3_payload)
        resp3 = await llm_client.post(endpoint, headers=headers, json=pass3_payload, timeout=600)
        resp3.raise_for_status()
        pass3_text = resp3.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        wb_md, hist_md = parse_pass3_response(pass3_text)
        progress("✅ Worldbuilding documents generated", step="pass3")


        return wb_md, hist_md

    async def _import_link_existing(self, foundry_client, llm_client, campaign_name, campaign_data, scene_candidates_cache, progress):
        # ── Step 6.5: Link to pre-existing Foundry documents ──
        # A DDBImporter sync pre-creates the whole book as world Actors
        # and Scenes (folders/subfolders) — reuse those instead of
        # generating duplicate NPCs/maps when names match.
        if foundry_client is not None:
            # Reuse the index already fetched for the canonical area names
            # rather than paying for the same query twice.
            existing_scenes = scene_candidates_cache or filter_candidates_by_campaign_folder(
                await self._fetch_world_document_index(foundry_client, "Scene"), campaign_name
            )
            existing_actors = filter_candidates_by_campaign_folder(
                await self._fetch_world_document_index(foundry_client, "Actor"), campaign_name
            )

            scenes_all = campaign_data.get("scenes", [])
            # Scenes use the chapter-aware matcher: both sides know their
            # chapter (source_chapter from the per-chapter loop, the
            # candidate's folder), and Pass 1 often carries the book's own
            # "Map N.N" label into the scene name. NPCs stay on plain name
            # matching — actor candidates all sit in one flat folder with
            # no chapter to exploit.
            scene_link = match_scenes_to_existing(scenes_all, existing_scenes)
            # Semantic fallback for whatever fuzzy name matching missed —
            # content/context judgment catches cases like a generated
            # "Vogler — The Brass Crab" that should still link to an
            # existing "Map 3.1: Vogler" despite barely sharing any text.
            remaining_scenes = [
                c for c in existing_scenes if c.get("uuid") not in scene_link["matched"].values()
            ]
            unmatched_scenes = [s for s in scenes_all if s.get("name") in scene_link["unmatched"]]
            semantic_scenes = await self._semantic_match_names(
                llm_client, "scene", unmatched_scenes, remaining_scenes
            )
            matched_areas = scene_link.get("areas", {})
            # A linked scene's generated name (often a map-pin label, e.g.
            # "The Brass Crab") can be nearly unrelated text to the real
            # Foundry document's title (e.g. "Map 3.1: Vogler") — that's
            # the whole point of pin-label matching. Foundry-side calls
            # (activate scene, get scene data) resolve by the REAL name,
            # so record it here for deploy_to_foundry to carry forward.
            uuid_to_foundry_name = {c.get("uuid"): c.get("name") for c in existing_scenes if c.get("uuid")}
            # scene_link["matched"] is keyed by name, so two DISTINCT
            # scenes that happen to share an identical generated name
            # (e.g. "Ambush" recurring in two chapters) would otherwise
            # both read the same dict entry and silently link to the
            # same Foundry document. Different names sharing one uuid
            # (the legitimate tier-3 "many pins, one map" case) is
            # unaffected — this only guards a name seen more than once.
            seen_scene_names: Set[str] = set()
            for scene in scenes_all:
                name = scene.get("name", "")
                if name in seen_scene_names:
                    continue
                seen_scene_names.add(name)
                uuid = scene_link["matched"].get(name) or semantic_scenes.get(name)
                if uuid:
                    scene["existing_uuid"] = uuid
                    scene["foundry_scene_name"] = uuid_to_foundry_name.get(uuid, name)
                    scene["map_needed"] = False
                    # Record which map pin this scene resolved to, when it
                    # matched an area on a shared map rather than the map
                    # itself — the label identifies where on the canvas the
                    # scene actually happens.
                    if name in matched_areas:
                        scene["existing_area"] = matched_areas[name]
            if matched_areas:
                progress(
                    f"  📍 {len(matched_areas)} scene(s) matched a labelled area on a published map",
                    step="assets",
                )

            npcs_all = campaign_data.get("npcs", [])
            npc_link = match_names_to_existing(
                [n.get("name", "") for n in npcs_all], existing_actors
            )
            remaining_actors = [
                c for c in existing_actors if c.get("uuid") not in npc_link["matched"].values()
            ]
            unmatched_npcs = [n for n in npcs_all if n.get("name") in npc_link["unmatched"]]
            semantic_npcs = await self._semantic_match_names(
                llm_client, "NPC", unmatched_npcs, remaining_actors
            )
            # Unlike scenes, one Foundry Actor should never back two
            # different generated NPCs — guard by uuid so two NPCs that
            # happen to share a name (not caught by the dedup pass above,
            # e.g. a generic "Guard Captain" per chapter) don't both link
            # to the same pre-existing actor.
            claimed_npc_uuids: Set[str] = set()
            for npc in npcs_all:
                name = npc.get("name", "")
                uuid = npc_link["matched"].get(name) or semantic_npcs.get(name)
                if uuid and uuid not in claimed_npc_uuids:
                    npc["existing_uuid"] = uuid
                    claimed_npc_uuids.add(uuid)

            progress(
                f"🔗 Linked {len(scene_link['matched']) + len(semantic_scenes)} scene(s) "
                f"({len(semantic_scenes)} via semantic match) and "
                f"{len(npc_link['matched']) + len(semantic_npcs)} NPC(s) "
                f"({len(semantic_npcs)} via semantic match) to pre-existing Foundry documents",
                step="assets",
            )


    async def _import_match_assets(self, campaign_name, vault_path, campaign_data, scan, progress):
        # ── Step 7: Match assets ──
        progress("🗺️ Matching maps to scenes...", step="assets")
        from campaign.vault import CampaignStore
        store = CampaignStore(campaign_name, vault_path)
        store.maps_dir.mkdir(parents=True, exist_ok=True)

        scenes = campaign_data.get("scenes", [])
        scene_names = [s.get("name", "") for s in scenes]
        # Published maps are often named after regions/locations rather than
        # individual scenes. Let each scene also match its containing
        # location's name so regional maps get picked up as a fallback.
        scene_aliases: Dict[str, List[str]] = {}
        for loc in campaign_data.get("locations", []):
            loc_name = loc.get("name", "")
            if not loc_name:
                continue
            for sn in loc.get("scenes", []):
                scene_aliases.setdefault(sn, []).append(loc_name)
        map_match = match_maps_to_scenes(
            scene_names,
            scan["maps"],
            store.maps_dir,
            scene_aliases=scene_aliases,
        )
        # Apply matches onto the scene dicts: pre-placed file + flags mean
        # generate_assets skips the scene and upload picks the file up.
        for scene in scenes:
            match = map_match["matched_scenes"].get(scene.get("name", ""))
            if not match:
                scene.setdefault("map_needed", True)
                continue
            scene["map_file"] = Path(match["map_file"]).name
            scene["map_needed"] = False
            # Grid from the real image dimensions; empty walls/lights/sounds —
            # hallucinated walls won't align with professional maps, and
            # enrich_scenes no-ops on empty lists.
            setup = scene.setdefault("scene_setup", {})
            setup["grid_width"] = match["grid_width"]
            setup["grid_height"] = match["grid_height"]
            setup["grid_size_px"] = match["grid_size_px"]
            setup["walls"] = []
            setup["doors"] = []
            setup["lights"] = []
            setup["sounds"] = []
            # Exact pixel dims so deploy sizes the canvas to the image.
            scene["_map_width_px"] = match["width_px"]
            scene["_map_height_px"] = match["height_px"]
            scene["_grid_size_px"] = match["grid_size_px"]
        progress(
            f"  🗺️ {len(map_match['matched_scenes'])} matched, "
            f"{len(map_match['unmatched_scenes'])} unmatched",
            step="assets",
        )

        npcs = campaign_data.get("npcs", [])
        npc_names = [n.get("name", "") for n in npcs]
        portraits_dir = store.maps_dir / "portraits"
        portraits_dir.mkdir(parents=True, exist_ok=True)
        token_match = match_tokens_to_npcs(
            npc_names,
            scan["tokens"],
            portraits_dir,
        )
        for npc in npcs:
            match = token_match["matched_npcs"].get(npc.get("name", ""))
            if not match:
                continue
            # upload_portraits_to_foundry resolves <maps_dir>/portraits/<file>
            npc["portrait_file"] = Path(match["portrait_file"]).name
            npc["portrait_needed"] = False
        progress(
            f"  👤 {len(token_match['matched_npcs'])} token(s) matched, "
            f"{len(token_match['unmatched_npcs'])} unmatched",
            step="assets",
        )

        # Prepare handout journal entries
        handout_entries = prepare_handouts(scan["handouts"], campaign_data)
        if handout_entries:
            campaign_data.setdefault("journal_entries", []).extend(handout_entries)
            progress(f"  📜 {len(handout_entries)} handout(s) prepared", step="assets")


        return store, map_match, token_match, handout_entries

    async def _import_write_lore(self, store, all_notes, wb_md, hist_md, handout_entries, progress):
        # ── Step 8: Write lore .md files into vault ──
        progress("📝 Writing lore files to vault...", step="lore")
        store.folder.mkdir(parents=True, exist_ok=True)

        if wb_md:
            wb_path = store.folder / "Worldbuilding.md"
            await asyncio.to_thread(wb_path.write_text, wb_md, encoding="utf-8")
        if hist_md:
            hist_path = store.folder / "History.md"
            await asyncio.to_thread(hist_path.write_text, hist_md, encoding="utf-8")

        # Raw extraction notes as Lore/<NN> <chapter label>.md — one file
        # per chapter (rather than per arbitrary token-boundary chunk) so
        # they're actually browsable in Obsidian for a multi-chapter import.
        lore_dir = store.folder / "Lore"
        lore_dir.mkdir(exist_ok=True)
        for i, (chapter_label, notes) in enumerate(all_notes, 1):
            part_path = lore_dir / f"{i:02d} {sanitize_filename(chapter_label)}.md"
            await asyncio.to_thread(part_path.write_text, notes, encoding="utf-8")

        # Handout markdown files
        if handout_entries:
            handout_dir = store.folder / "Handouts"
            handout_dir.mkdir(exist_ok=True)
            for entry in handout_entries:
                md_path = handout_dir / f"{sanitize_filename(entry['title'])}.md"
                await asyncio.to_thread(
                    md_path.write_text,
                    f"# {entry['title']}\n\nSee attached PDF: {entry['pdf_file']}\n",
                    encoding="utf-8",
                )

        progress(f"✅ Lore files written to vault", step="lore")


    async def _import_upload_handouts(self, foundry_client, store, scan, handout_entries, progress):
        # ── Step 9: Upload handout PDFs to Foundry ──
        if foundry_client and scan["handouts"]:
            progress("📤 Uploading handout PDFs to Foundry...", step="upload_handouts")
            for entry in handout_entries:
                try:
                    pdf_path = Path(entry["pdf_src"])
                    pdf_bytes = await asyncio.to_thread(pdf_path.read_bytes)
                    upload_resp = await foundry_client.upload_file(
                        file_bytes=pdf_bytes,
                        path=f"campaigns/{store.safe_name}/handouts",
                        filename=entry["pdf_file"],
                        mime_type="application/pdf",
                    )
                    # Update pdf_src to the Foundry-relative path
                    saved_path = upload_resp.get("path", entry["pdf_src"])
                    entry["pdf_src"] = saved_path
                    progress(f"  📜 Uploaded {entry['pdf_file']}", step="upload_handouts")
                except Exception as e:
                    progress(f"  ⚠️ Failed to upload {entry['pdf_file']}: {e}", step="upload_handouts")

    async def _wait_for_foundry_ready(self, foundry_client, timeout: float = 45.0) -> None:
        """Poll until Foundry's `game` object has finished loading the world.

        The relay reports "Foundry connected" as soon as the WebSocket
        handshake completes, not once `game.ready` is true — a headless
        session firing a heavy compendium query (like the journal-pack
        fetch) in that window got garbled/oversized replies that the
        browser's own WS layer killed with close code 1009 ("message too
        big"). Every failure observed fired within ~0.5s of "connected";
        the one success happened ~80s in. Waiting here is cheap insurance.
        """
        elapsed = 0.0
        while elapsed < timeout:
            try:
                res = await foundry_client.execute_js("return { ready: !!(game && game.ready) };")
                payload = res.get("result") if isinstance(res, dict) else res
                if isinstance(payload, dict) and payload.get("ready"):
                    return
            except Exception as e:
                logger.warning(f"[Import] game.ready poll failed, retrying: {e}")
            await asyncio.sleep(1.0)
            elapsed += 1.0
        logger.warning(f"[Import] Foundry did not report game.ready within {timeout}s; proceeding anyway")

    async def _fetch_world_document_index(self, foundry_client, doc_type: str) -> List[Dict[str, str]]:
        """List every Actor or Scene document already in the world (name,
        uuid, and containing folder name).

        A DDBImporter sync pre-creates the whole book as world Actors and
        Scenes (organized into folders/subfolders), so a campaign import
        shouldn't blindly generate a brand-new NPC/map for something that
        already exists. This is metadata only — no HTML/portrait/background
        data — so unlike the journal-pack fetch, a single call is safe
        regardless of how many documents the world has. The folder name is
        included because it's often the single strongest semantic signal
        available (e.g. a scene filed under "Chapter 3: When Home Burns" is
        very likely that chapter's content) without the cost/size risk of
        fetching each document's own description text.

        Uses the FULL folder path, and for Scenes also returns each map's
        note (pin) labels. Those labels are the book's own canonical area
        names — 'The Brass Crab', 'R1: Hall of Knights' — already attached
        to the exact map they sit on, which is far stronger evidence than a
        map's own title (168 of them across 25 maps in a real world). Still
        metadata only: labels and coordinates, no page text.
        """
        collection = {
            "Actor": "game.actors", "Scene": "game.scenes",
            "JournalEntry": "game.journal", "RollTable": "game.tables",
        }[doc_type]
        notes_field = (
            """, notes: d.notes.contents.map(n => ({label: n.text || '', x: n.x, y: n.y}))
                        .filter(n => n.label)"""
            if doc_type == "Scene" else ""
        )
        js_query = f"""
        const path = (d) => {{ const p = []; let f = d.folder; while (f) {{ p.unshift(f.name); f = f.folder; }} return p.join(" / "); }};
        return {{ entries: {collection}.contents.map(d => ({{
            name: d.name, uuid: d.uuid, folder: path(d){notes_field}
        }})) }};
        """
        res = await foundry_client.execute_js(js_query)
        payload = res.get("result") if isinstance(res, dict) else res
        if not isinstance(payload, dict) or payload.get("error"):
            logger.warning(
                f"[Import] Failed to list existing {doc_type} documents: "
                f"{payload.get('error') if isinstance(payload, dict) else 'query failed'}"
            )
            return []
        return payload.get("entries", [])

    async def _semantic_match_names(
        self,
        llm_client,
        kind: str,
        items: List[Dict[str, Any]],
        candidates: List[Dict[str, str]],
    ) -> Dict[str, str]:
        """LLM-driven fallback for items fuzzy name-matching (match_names_to_existing)
        didn't catch.

        Runs ONE batched LLM call (not one per item) asking it to judge
        content/context rather than text similarity — e.g. a generated
        'Vogler — The Brass Crab' scene should still link to an existing
        'Map 3.1: Vogler' despite barely sharing any text, because it's the
        same in-world location the adventure describes.

        Best-effort by design: any failure (LLM error, malformed JSON, a
        hallucinated candidate name) just yields no matches for this pass
        rather than raising — import_campaign already falls back to full
        generation for anything left unmatched, so this must never be able
        to break the import.
        """
        if not items or not candidates:
            return {}

        from campaign.importer import build_semantic_match_prompt, parse_semantic_match_response

        system, user = build_semantic_match_prompt(kind, items, candidates)
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        self._suppress_thinking(payload)
        try:
            endpoint = self._chat_endpoint()
            headers = {
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            }
            resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            logger.warning(f"[Import] Semantic {kind} matching call failed: {e}")
            return {}

        name_to_existing = parse_semantic_match_response(text)
        candidate_by_name = {c.get("name", ""): c.get("uuid", "") for c in candidates}

        matched: Dict[str, str] = {}
        claimed: Set[str] = set()
        for generated_name, existing_name in name_to_existing.items():
            if not existing_name:
                continue
            uuid = candidate_by_name.get(existing_name)
            if not uuid:
                logger.warning(
                    f"[Import] Semantic match named a non-existent {kind} "
                    f"'{existing_name}' for '{generated_name}' — ignoring"
                )
                continue
            if uuid in claimed:
                logger.warning(
                    f"[Import] Semantic match for '{generated_name}' claimed "
                    f"already-used '{existing_name}' — skipping to avoid a double-link"
                )
                continue
            matched[generated_name] = uuid
            claimed.add(uuid)

        if matched:
            logger.info(f"[Import] Semantic matching linked {len(matched)} {kind}(s): {list(matched.keys())}")
        return matched

    async def _semantic_dedupe_section(
        self, llm_client, kind: str, items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Collapse near-duplicate entries a multi-chapter import produced —
        e.g. 'Red Dragon Army', 'Dragon Army', and 'The Dragon Armies' all
        independently (re)introduced as the same faction by different
        chapters. Verified directly against a real import: these scored
        anywhere from 0.35 to 0.76 on plain string similarity, overlapping
        with genuinely-different pairs in the same range, so this needs one
        batched LLM judgment call instead.

        Best-effort by design, same as _semantic_match_names: any failure
        (LLM error, malformed JSON) returns the items unchanged rather than
        raising — an import must never fail over a dedup pass.
        """
        if len(items) < 2:
            return items

        from campaign.importer import build_dedup_prompt, parse_dedup_groups, merge_duplicate_group

        names = [item.get("name", "") for item in items]
        system, user = build_dedup_prompt(kind, items)
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 4096,
        }
        self._suppress_thinking(payload)
        try:
            endpoint = self._chat_endpoint()
            headers = {
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            }
            resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            logger.warning(f"[Import] Semantic {kind} dedup call failed: {e}")
            return items

        groups = parse_dedup_groups(text, names)
        items_by_name = {item.get("name", ""): item for item in items}
        merged = [merge_duplicate_group(items_by_name, group) for group in groups]
        merged = [m for m in merged if m]

        removed = len(items) - len(merged)
        if removed > 0:
            logger.info(f"[Import] Deduped {removed} duplicate {kind}(s): {len(items)} → {len(merged)}")
        return merged

    async def _fetch_world_rolltables(
        self, foundry_client, campaign_name: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """The campaign's own RollTables, grouped by the chapter folder they
        sit in: {"Chapter 3: When Home Burns": [{name, description, results}]}.

        DDBImporter files the book's random encounter / event / rumour tables
        under the same "<campaign> / <chapter>" folders as its maps, so the
        deepest path segment keys them straight onto the per-chapter import
        loop. Result entries are short text, so one call carries all of them
        safely (unlike journal pages).
        """
        from campaign.importer import folder_matches_campaign

        js_query = """
        const path = (d) => { const p = []; let f = d.folder; while (f) { p.unshift(f.name); f = f.folder; } return p.join(" / "); };
        return { tables: game.tables.contents.map(t => ({
            name: t.name, folder: path(t), description: t.description || "",
            results: t.results.contents.map(r => r.text || r.name || "").filter(Boolean)
        })) };
        """
        try:
            res = await foundry_client.execute_js(js_query)
            payload = res.get("result") if isinstance(res, dict) else res
        except Exception as e:
            logger.warning(f"[Import] Could not read world RollTables: {e}")
            return {}
        if not isinstance(payload, dict) or payload.get("error"):
            return {}

        by_chapter: Dict[str, List[Dict[str, Any]]] = {}
        for table in payload.get("tables", []):
            folder = table.get("folder") or ""
            if not folder_matches_campaign(folder, campaign_name):
                continue
            chapter = folder.split("/")[-1].strip()
            if not chapter:
                continue
            by_chapter.setdefault(chapter, []).append(table)
        if by_chapter:
            counts = {k: len(v) for k, v in by_chapter.items()}
            logger.info(f"[Import] Found campaign RollTables per chapter: {counts}")
        return by_chapter

    async def _fetch_world_journals(
        self, foundry_client, campaign_name: str
    ) -> List[Dict[str, Any]]:
        """Read the campaign's adventure text from WORLD JournalEntries,
        returning the same {name, pages:[{name, html}]} shape as
        _fetch_journal_pack.

        Lets an import run straight off what DDBImporter already put in the
        world, with no manual "export to journal" step — and measured on a
        real world, the world journals held ~1.25M characters of adventure
        text against the compendium pack's 739K for the same book.

        Fetches ONE PAGE PER CALL. A single chapter here reaches 200K+
        characters, and batching journal content is exactly what previously
        got the relay's socket killed with close 1009 (message too big);
        images are stripped for the same reason.
        """
        from campaign.importer import folder_matches_campaign, is_adventure_content_entry

        index_query = """
        const path = (d) => { const p = []; let f = d.folder; while (f) { p.unshift(f.name); f = f.folder; } return p.join(" / "); };
        return { entries: game.journal.contents.map(j => ({
            id: j.id, name: j.name, folder: path(j),
            pages: j.pages.contents.slice().sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0))
                     .map(p => ({ id: p.id, name: p.name }))
        })) };
        """
        try:
            res = await foundry_client.execute_js(index_query)
            payload = res.get("result") if isinstance(res, dict) else res
        except Exception as e:
            logger.warning(f"[Import] Could not index world journals: {e}")
            return []
        if not isinstance(payload, dict) or payload.get("error"):
            return []

        wanted = [
            e for e in payload.get("entries", [])
            if folder_matches_campaign(e.get("folder"), campaign_name)
            and is_adventure_content_entry(e.get("name", ""))
            and e.get("pages")
        ]
        if not wanted:
            return []

        entries: List[Dict[str, Any]] = []
        for entry in wanted:
            pages: List[Dict[str, str]] = []
            for page in entry["pages"]:
                page_query = f"""
                const j = game.journal.get({entry['id']!r});
                if (!j) return {{ error: 'journal gone' }};
                const p = j.pages.get({page['id']!r});
                if (!p) return {{ error: 'page gone' }};
                const strip = (html) => (html || '')
                    .replace(/<img\\b[^>]*>/gi, '')
                    .replace(/data:[^"'\\s)]+/gi, '');
                return {{ html: strip(p.text && p.text.content) }};
                """
                try:
                    pres = await foundry_client.execute_js(page_query)
                    ppayload = pres.get("result") if isinstance(pres, dict) else pres
                except Exception as e:
                    logger.warning(
                        f"[Import] Skipping page {page.get('name')!r} of "
                        f"{entry.get('name')!r}: {e}"
                    )
                    continue
                if not isinstance(ppayload, dict) or ppayload.get("error"):
                    continue
                pages.append({"name": page.get("name", ""), "html": ppayload.get("html", "")})
            if pages:
                entries.append({"name": entry.get("name", ""), "pages": pages})
        return entries

    @staticmethod
    def _pack_finder_js(pack_name: str) -> str:
        """JS snippet binding `pack` to a JournalEntry compendium, or erroring."""
        return f"""
        const pack = game.packs.find(p => p.documentName === 'JournalEntry'
            && (p.collection === {pack_name!r} || p.metadata.name === {pack_name!r}));
        if (!pack) return {{ error: 'Journal pack not found: ' + {pack_name!r} }};
        """

    async def _fetch_journal_pack(self, foundry_client, pack_name: str) -> List[Dict[str, Any]]:
        """Read every JournalEntry document (with its pages) out of a Foundry
        compendium pack, one document per execute-js call.

        Fetching the whole pack in a single `pack.getDocuments()` call
        repeatedly got the relay's Foundry-module WebSocket connection
        killed with close code 1009 ("message too big") partway through
        this campaign's 15-entry pack — stripping embedded images and
        waiting for game.ready didn't stop it, so whatever the real byte
        threshold is, the fix is to never build one big reply in the first
        place. A lightweight index call gets just the document ids, then
        each document is fetched (and image-stripped) in its own small
        reply, so no single WS message can ever be large regardless of the
        pack's total size.

        The index is also filtered down to entries named like 'Chapter N'
        or 'Appendix X' before any full document is fetched — a DDBImporter
        journals pack is often shared across every sourcebook synced into
        the world, not just the adventure being imported (this campaign's
        pack had Player's Handbook, Xanathar's Guide, Tasha's Cauldron,
        etc. mixed in with its actual chapters), which both wastes fetch
        round-trips and dilutes Pass 1/2 with unrelated rules-reference
        text.

        The relay wraps execute-js as an async function body, so each
        script awaits promises directly and returns the resolved value
        (not an async IIFE, whose value the relay drops); results are
        unwrapped from the relay envelope via `.get("result")`.
        """
        from campaign.importer import is_adventure_journal_entry

        index_query = self._pack_finder_js(pack_name) + """
        const index = await pack.getIndex();
        return { entries: index.contents.map(e => ({ id: e._id, name: e.name })) };
        """
        res = await foundry_client.execute_js(index_query)
        payload = res.get("result") if isinstance(res, dict) else res
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError(
                payload.get("error") if isinstance(payload, dict) else "Journal pack index query failed"
            )
        indexed = payload.get("entries", [])
        doc_ids = [e["id"] for e in indexed if is_adventure_journal_entry(e.get("name"))]
        skipped = [e["name"] for e in indexed if not is_adventure_journal_entry(e.get("name"))]
        if skipped:
            logger.info(f"[Import] Journal pack '{pack_name}': skipping non-adventure entries {skipped}")

        entries: List[Dict[str, Any]] = []
        for doc_id in doc_ids:
            doc_query = self._pack_finder_js(pack_name) + f"""
            const doc = await pack.getDocument({doc_id!r});
            if (!doc) return {{ error: 'Document not found: ' + {doc_id!r} }};
            const stripImages = (html) => (html || '')
                .replace(/<img\\b[^>]*>/gi, '')
                .replace(/data:[^"'\\s)]+/gi, '');
            return {{ name: doc.name, pages: (doc.pages?.contents ?? []).slice()
                .sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0))
                .map(p => ({{ name: p.name, html: stripImages(p.text && p.text.content) }})) }};
            """
            res = await foundry_client.execute_js(doc_query)
            payload = res.get("result") if isinstance(res, dict) else res
            if not isinstance(payload, dict) or payload.get("error"):
                logger.warning(
                    f"[Import] Skipping journal document {doc_id!r}: "
                    f"{payload.get('error') if isinstance(payload, dict) else 'query failed'}"
                )
                continue
            entries.append(payload)
        return entries
