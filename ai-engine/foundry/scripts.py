"""Named builders for the JS snippets the engine runs inside Foundry.

Target home for every execute_js payload (architecture plan, Phase 5) so the
scripts are testable and greppable instead of scattered string literals.
Migrate existing inline snippets here opportunistically when touching them.
"""

import json


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
