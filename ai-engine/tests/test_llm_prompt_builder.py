from llm.manager import LLMManager


class _CampaignLoader:
    def get_world_context_sync(self):
        return "World facts"

    def get_npc_context_sync(self):
        return "NPC facts"


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
