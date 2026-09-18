"""CampaignOrchestrator's public surface, pinned.

The class was one 4,469-line file. Asset generation, Foundry deployment and
world import now live in mixins. Composition is invisible to callers, which is
the point and also the risk: a method dropped or shadowed during a later move
would surface as an AttributeError deep in a campaign build, long after the
refactor. These tests fail at import time instead.
"""

from campaign.orchestrator import CampaignOrchestrator
from campaign.orchestrator_assets import AssetPipelineMixin
from campaign.orchestrator_deploy import DeploymentMixin
from campaign.orchestrator_import import WorldImportMixin

# Every public entry point callers rely on, plus the private helpers that moved
# across module boundaries (those are the ones a bad move would lose).
EXPECTED = {
    # stays in orchestrator.py
    "scan_foundry_world", "generate_campaign_data", "save_to_vault",
    "enrich_scenes", "build_campaign", "build_campaign_convenience",
    "extend_campaign_arc",
    # AssetPipelineMixin
    "generate_assets", "upload_maps_to_foundry", "upload_portraits_to_foundry",
    "upload_prologue_to_foundry", "regenerate_assets_for_campaign",
    "_attach_map_to_scene", "_attach_portrait_to_actor",
    "_generate_placeholder_portraits", "_build_scene_prompt",
    "_build_location_prompt", "_default_monster_icon",
    # DeploymentMixin
    "deploy_to_foundry", "deploy_encounters", "teardown_campaign",
    "_ensure_monster_actor", "_scene_setup_to_canvas", "_wall_blocked_squares",
    "_real_wall_blocked_squares", "_safe_fallback_positions",
    # WorldImportMixin
    "import_campaign", "_wait_for_foundry_ready", "_fetch_world_document_index",
    "_fetch_world_rolltables", "_fetch_world_journals", "_fetch_journal_pack",
    "_semantic_match_names", "_semantic_dedupe_section", "_pack_finder_js",
}


def test_every_method_is_reachable_on_the_composed_class():
    missing = sorted(n for n in EXPECTED if not callable(getattr(CampaignOrchestrator, n, None)))
    assert missing == [], f"lost in composition: {missing}"


def test_mixins_do_not_shadow_each_other():
    """Two mixins defining the same name would make the MRO decide silently."""
    owners = {}
    for mixin in (AssetPipelineMixin, DeploymentMixin, WorldImportMixin):
        for name in vars(mixin):
            if name.startswith("__"):
                continue
            owners.setdefault(name, []).append(mixin.__name__)

    clashes = {n: o for n, o in owners.items() if len(o) > 1}
    assert clashes == {}, f"same method defined in more than one mixin: {clashes}"


def test_orchestrator_does_not_redefine_mixin_methods():
    """A copy left behind in orchestrator.py would win the MRO and drift."""
    mixin_names = set()
    for mixin in (AssetPipelineMixin, DeploymentMixin, WorldImportMixin):
        mixin_names |= {n for n in vars(mixin) if not n.startswith("__")}

    shadowed = sorted(mixin_names & {n for n in vars(CampaignOrchestrator) if not n.startswith("__")})
    assert shadowed == [], f"orchestrator.py still defines moved methods: {shadowed}"


def test_generation_budget_reaches_the_mixins():
    """WorldImportMixin.import_campaign reads it off self, not a module global."""
    assert CampaignOrchestrator.CAMPAIGN_GEN_MAX_TOKENS == 65536
