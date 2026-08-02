from llm.manager import LLMManager


class _CampaignLoader:
    def get_world_context_sync(self):
        return "World facts"

    def get_npc_context_sync(self):
        return "NPC facts"

    def get_house_rules_context_sync(self):
        return ""

    def get_canon_context_sync(self):
        return ""


def test_system_prompt_override_is_returned_and_cached():
    manager = LLMManager(campaign_loader=_CampaignLoader())
    manager.set_system_prompt("custom prompt")

    assert manager.system_prompt == "custom prompt"
    assert manager.system_prompt == "custom prompt"


def test_set_active_modules_invalidates_prompt_cache():
    manager = LLMManager(campaign_loader=_CampaignLoader())

    first = manager.system_prompt
    manager.set_active_modules(["midi-qol"])
    second = manager.system_prompt

    assert first != second
    assert "midi-qol" in second.lower()


def test_dynamic_canon_context_overrides_stale_loader_snapshot():
    """A /gm rule|canonize write must appear in the very next turn's system
    prompt without a full vault reload — the loader's get_canon_context_sync()
    reflects an in-memory snapshot from campaign-load time, so a fresh write
    pushed via set_dynamic_canon_context must take precedence over it."""
    manager = LLMManager(campaign_loader=_CampaignLoader())

    before = manager.system_prompt
    assert "Fresh canon fact" not in before

    manager.set_dynamic_canon_context("## Canon ##\nFresh canon fact")
    after = manager.system_prompt

    assert "Fresh canon fact" in after


def test_dynamic_house_rules_context_overrides_stale_loader_snapshot():
    manager = LLMManager(campaign_loader=_CampaignLoader())

    manager.system_prompt  # populate cache from the (empty) loader snapshot
    manager.set_dynamic_house_rules_context("## House Rules ##\nCrits are max damage")
    after = manager.system_prompt

    assert "Crits are max damage" in after


def test_dynamic_npc_context_overrides_stale_loader_snapshot():
    """Regression test: the stale-snapshot-override fix was originally
    applied only to house_rules/canon, leaving set_dynamic_npc_context
    (called from scene/awareness.py on every scene change) with zero
    effect on the actual system prompt content."""
    manager = LLMManager(campaign_loader=_CampaignLoader())

    manager.system_prompt  # populate cache from the loader snapshot ("NPC facts")
    manager.set_dynamic_npc_context("Fresh NPC context from a scene change")
    after = manager.system_prompt

    assert "Fresh NPC context from a scene change" in after


def test_dynamic_world_context_overrides_stale_loader_snapshot():
    manager = LLMManager(campaign_loader=_CampaignLoader())

    manager.system_prompt
    manager.set_dynamic_world_context("Fresh world context from a scene change")
    after = manager.system_prompt

    assert "Fresh world context from a scene change" in after
