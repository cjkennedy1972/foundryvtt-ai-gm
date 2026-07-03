"""Polyglot — NPC spoken language + in-world text language."""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    language = ctx.npc.get("language_spoken")
    if language:
        ctx.prototype_token.setdefault("flags", {})["polyglot"] = {"language": language}


def on_journal(entry: dict, mods: dict):
    if entry.get("language"):
        return {"language": entry["language"]}
    return None


register(ModuleIntegration(module_id="polyglot", on_npc=on_npc, on_journal=on_journal))
