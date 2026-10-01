"""Character backstory endpoint: a backstory grounded in the campaign's own lore.

The generator a player would otherwise run against a public LLM only knows what
they paste in. This one pulls the world section and the vault lore closest to
the character (semantic search), so the story names real places and factions
instead of inventing ones that contradict canon.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.deps import AppState, ErrorResponse, get_app_state
from llm.usage import TokenBudgetExceeded

logger = logging.getLogger("ai-gm")

router = APIRouter(tags=["backstory"])

LORE_CHUNKS = 5

SYSTEM_PROMPT = (
    "You are a creative writing assistant for tabletop RPG players. Write a vivid "
    "character backstory of 2-4 paragraphs that fits the campaign world below. "
    "Tie the character to the world's real places, factions and people where it "
    "suits them, and never contradict the lore. Do not invent major world facts "
    "the lore does not support; keep invented details personal and local to the "
    "character. Return only the backstory text, no title or preamble."
)


class BackstoryRequest(BaseModel):
    name: str = Field(max_length=100)
    ancestry: str = Field(default="", max_length=100)
    char_class: str = Field(default="", max_length=100)
    gender: str = Field(default="", max_length=50)
    age: str = Field(default="", max_length=50)
    homeland: str = Field(default="", max_length=200)
    tone: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=2000)


def _character_sheet(req: BackstoryRequest) -> str:
    fields = [
        ("Name", req.name), ("Ancestry", req.ancestry), ("Class", req.char_class),
        ("Gender", req.gender), ("Age", req.age), ("Homeland", req.homeland),
        ("Tone or theme", req.tone), ("Player notes", req.notes),
    ]
    return "\n".join(f"- {label}: {value.strip()}" for label, value in fields if value.strip())


async def _lore(state: AppState, req: BackstoryRequest) -> tuple[str, list[str]]:
    """Vault lore nearest the character, as prompt text plus its source notes."""
    if state.semantic_rag is None:
        return "", []
    query = " ".join(v for v in (req.ancestry, req.char_class, req.homeland, req.tone, req.notes) if v.strip())
    if not query:
        return "", []
    try:
        hits = await state.semantic_rag.inject_lore(query, top_k=LORE_CHUNKS)
    except Exception as e:
        logger.warning(f"[Backstory] lore lookup failed: {e}")
        return "", []
    return "\n".join(f"- {h.text}" for h in hits), sorted({h.source for h in hits})


@router.post("/api/backstory")
async def generate_backstory(req: BackstoryRequest, state: AppState = Depends(get_app_state)):
    """Generate a backstory for one character, grounded in the campaign vault."""
    if state.llm_manager is None:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(error="The engine is still starting up.", code="NOT_READY").model_dump(),
        )
    if not req.name.strip():
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(error="A character name is required.", code="INVALID").model_dump(),
        )

    world = state.campaign_loader.get_world_context_sync() if state.campaign_loader else ""
    lore, sources = await _lore(state, req)
    context = "\n\n".join(
        part for part in (
            f"## WORLD\n{world.strip()}" if world.strip() else "",
            f"## LORE NEAR THIS CHARACTER\n{lore}" if lore else "",
        ) if part
    )

    try:
        text = await state.llm_manager.generate_text(
            user_message=f"Write the backstory for this character:\n{_character_sheet(req)}",
            system_prompt=SYSTEM_PROMPT,
            context=context,
        )
    except TokenBudgetExceeded:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(error="The session's token budget is used up.", code="BUDGET").model_dump(),
        )
    except Exception as e:
        logger.error(f"[Backstory] generation failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(error="The language model did not answer.", code="LLM_ERROR").model_dump(),
        )

    return {"status": "ok", "backstory": text, "sources": sources, "grounded": bool(context)}
