"""Token Notes — GM-only secret information attached to a token."""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    note = ctx.npc.get("gm_token_note")
    if note:
        ctx.prototype_token.setdefault("flags", {})["token-notes"] = {"note": note}


register(ModuleIntegration(module_id="token-notes", on_npc=on_npc))
