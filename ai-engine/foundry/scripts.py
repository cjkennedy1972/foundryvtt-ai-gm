"""Named builders for the JS snippets the engine runs inside Foundry.

Target home for every execute_js payload (architecture plan, Phase 5) so the
scripts are testable and greppable instead of scattered string literals.
Migrate existing inline snippets here opportunistically when touching them.
"""

import json
from typing import Dict, List


def sync_combat_combatants(token_ids: List[str]) -> str:
    """Create (or reuse) the active scene's Combat and make its combatants
    exactly match token_ids, in that order (index 0 = first turn).

    Live-verified against Foundry v14 (game.combat/Combat.create/
    createEmbeddedDocuments/deleteEmbeddedDocuments all behave as expected;
    combat.turns sorts by initiative descending). Initiative is set to a
    descending integer matching token_ids' order purely so Foundry's own
    sort produces the AI's turn order — these aren't real initiative rolls.

    Returns {ok: true, combatId} or {ok: false, error}.
    """
    ids_json = json.dumps(token_ids)
    return f"""
const tokenIds = {ids_json};
const s = canvas?.scene;
if (!s) return {{ok: false, error: 'no active scene'}};
let combat = game.combats.find(c => c.scene?.id === s.id);
if (!combat) {{
    combat = await Combat.create({{scene: s.id, active: true}});
}} else if (!combat.active) {{
    await combat.update({{active: true}});
}}
const existingTokenIds = new Set(combat.combatants.map(c => c.tokenId));
const toDeleteIds = combat.combatants
    .filter(c => !tokenIds.includes(c.tokenId))
    .map(c => c.id);
if (toDeleteIds.length) {{
    await combat.deleteEmbeddedDocuments('Combatant', toDeleteIds);
}}
const toCreate = tokenIds
    .filter(id => !existingTokenIds.has(id))
    .map(id => ({{tokenId: id, sceneId: s.id}}));
if (toCreate.length) {{
    await combat.createEmbeddedDocuments('Combatant', toCreate);
}}
const n = tokenIds.length;
for (let i = 0; i < n; i++) {{
    const cbt = combat.combatants.find(c => c.tokenId === tokenIds[i]);
    if (cbt) await cbt.update({{initiative: n - i}});
}}
return {{ok: true, combatId: combat.id}};
"""


def set_combat_turn(round_number: int, turn_index: int) -> str:
    """Set the active scene's Combat round/turn directly (no dialogs, no
    nextTurn() hook side effects) to mirror CombatLoop's own state.
    """
    return f"""
const combat = game.combat;
if (!combat) return {{ok: false, error: 'no active combat'}};
await combat.update({{round: {int(round_number)}, turn: {int(turn_index)}}});
return {{ok: true, current: combat.combatant?.name ?? null}};
"""


def end_combat() -> str:
    """Delete the active scene's Combat document.

    Uses combat.delete() rather than combat.endCombat() — the latter opens
    a confirmation dialog (live-verified: it hangs a headless session
    waiting for a click that never comes, timing out the RPC).
    """
    return """
const combat = game.combat;
if (!combat) return {ok: true, deleted: false};
await combat.delete();
return {ok: true, deleted: true};
"""


def get_active_modules() -> str:
    """All Foundry modules with id/title/version/active — ground truth read
    directly from game.modules.

    Bypasses the relay's 'world-info' RPC, which was found to always return
    an empty module list regardless of how many modules are actually active
    (root cause is inside the bundled Foundry module's handler, out of
    reach from this repo) — silently disabling every addon-integration
    check in deploy_to_foundry and combat/loop.py's module detection.
    """
    return (
        "return [...game.modules.values()].map(m => ({"
        "id: m.id, title: m.title || m.id, version: m.version || '', active: !!m.active"
        "}));"
    )


def find_actors_needing_portraits() -> str:
    """World actors flagged for AI portrait generation (or legacy blank art).

    Catches actors explicitly flagged needs_portrait, plus any legacy
    auto_placeholder monster whose art is still blank/mystery-man (created
    before the flag existed) so existing worlds self-heal on next deploy.
    Returns a list of {uuid, name}.
    """
    return (
        "return game.actors.filter(a => {"
        "  const f = a.flags?.['ai-gm'];"
        "  if (!f) return false;"
        "  if (f.needs_portrait) return true;"
        "  if (f.auto_placeholder && (!a.img || a.img.includes('mystery-man'))) return true;"
        "  return false;"
        "}).map(a => ({uuid: a.uuid, name: a.name}));"
    )


def count_scene_placeables(scene_name: str) -> str:
    """Wall/light/sound counts for a scene by name, or null if not found.

    Used to skip re-enriching categories a scene already has — enrichment
    runs at build, redeploy, and regenerate, and blindly re-creating
    walls/lights/sounds would duplicate them.
    """
    return (
        f"const s = game.scenes.getName({json.dumps(scene_name)});"
        "return s ? {walls: s.walls.size, lights: s.lights.size, sounds: s.sounds.size} : null;"
    )


def teardown_by_flag() -> str:
    """Delete every document (actors/journal/tables/playlists/scenes) flagged
    flags['ai-gm'] — one round-trip regardless of how many entities exist.

    Returns {label: deletedCount} per collection.
    """
    return r"""
const results = {};
const collections = [
  ["actors",    game.actors],
  ["journal",   game.journal],
  ["tables",    game.tables],
  ["playlists", game.playlists],
  ["scenes",    game.scenes],
];
for (const [label, col] of collections) {
  const toDelete = col.filter(d => d.flags?.["ai-gm"]).map(d => d.id);
  results[label] = toDelete.length;
  if (toDelete.length > 0) {
    await col.documentClass.deleteDocuments(toDelete);
  }
}
return results;
"""


def teardown_by_uuid_map(uuids_by_doc_type: Dict[str, List[str]]) -> str:
    """Delete documents by UUID, grouped by Foundry document type.

    Fallback pass for teardown when the flag pass (teardown_by_flag) misses
    documents — e.g. entities created before flagging existed. Returns
    {docType: deletedCount}.
    """
    uuid_map_json = json.dumps(uuids_by_doc_type)
    return f"""
const uuidMap = {uuid_map_json};
const typeMap = {{
  "Scene": game.scenes,
  "Actor": game.actors,
  "JournalEntry": game.journal,
  "RollTable": game.tables,
  "Playlist": game.playlists,
}};
const fbResults = {{}};
for (const [docType, uuids] of Object.entries(uuidMap)) {{
  const col = typeMap[docType];
  if (!col) continue;
  const ids = uuids.map(u => u.split(".").pop()).filter(id => col.get(id));
  fbResults[docType] = ids.length;
  if (ids.length > 0) await col.documentClass.deleteDocuments(ids);
}}
return fbResults;
"""


def get_active_effects(actor_uuid: str) -> str:
    """Active effects (name, remaining rounds, disabled) on an actor by UUID."""
    return f"""
const actor = await fromUuid('{actor_uuid}');
if (!actor) return [];
return actor.effects.map(e => ({{
    name: e.name,
    duration: e.duration?.rounds || 0,
    disabled: e.disabled
}}));
"""


def get_initiative_order() -> str:
    """Active combat's turn order as a list of token ids, or [] if no combat.

    Reads game.combat.turns so the AI loop follows the same initiative the
    players see in the tracker.
    """
    return (
        "const c = game.combat;"
        "return (c && c.turns) ? c.turns.map(t => t.token?.id).filter(Boolean) : [];"
    )


def tactical_scene_state() -> str:
    """Grid size, wall segments, and token positions of the active scene.

    One round-trip for everything combat tactics needs; walls carry door/ds so
    open doors can be excluded from cover.
    """
    return (
        "const s=canvas?.scene;"
        "if(!s)return null;"
        "return{grid:s.grid?.size??64,"
        "walls:s.walls.contents.map(w=>({c:w.c,door:w.door,ds:w.ds})),"
        "tokens:s.tokens.contents.map(t=>({id:t.id,name:t.name,x:t.x,y:t.y,"
        "width:t.width,height:t.height,disposition:t.disposition,hidden:t.hidden}))};"
    )


def ensure_npc_token(npc_name: str) -> str:
    """Make the named NPC physically present on the active scene.

    - Token already there: reveal it if hidden, otherwise no-op.
    - No token but a world actor exists: spawn its prototype token beside a
      friendly (player) token, so dialogue has a visible speaker.
    - No actor (narrator persona) or empty scene: report why, change nothing.

    Returns {ok, present|revealed|placed|reason} from Foundry.
    """
    want = json.dumps(npc_name.strip().lower())
    return (
        f"const want={want};"
        "const s=canvas?.scene;"
        "if(!s)return{ok:false,reason:'no scene'};"
        "const tok=s.tokens.find(t=>{const n=t.name?.toLowerCase()??'';return n===want||n.startsWith(want+' ');});"
        "if(tok){"
        "  if(tok.hidden){await tok.update({hidden:false});return{ok:true,revealed:tok.name};}"
        "  return{ok:true,present:tok.name};"
        "}"
        "const actor=game.actors.find(a=>a.name?.toLowerCase()===want);"
        "if(!actor)return{ok:false,reason:'no actor'};"
        "const anchor=s.tokens.find(t=>t.disposition===1)??s.tokens.contents[0];"
        "if(!anchor)return{ok:false,reason:'empty scene'};"
        "const gs=s.grid?.size??64;"
        "const doc=await actor.getTokenDocument({x:anchor.x+2*gs,y:anchor.y,hidden:false,disposition:0});"
        "const created=await s.createEmbeddedDocuments('Token',[doc.toObject()]);"
        "return{ok:true,placed:actor.name,id:created[0]?.id??''};"
    )
