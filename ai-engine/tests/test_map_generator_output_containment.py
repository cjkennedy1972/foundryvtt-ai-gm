"""Generated assets may only be written under a known asset root.

Every caller derives output_dir from sanitize_filename(campaign_name) today,
but that was a convention spread across a dozen call sites rather than an
enforced invariant. The same convention-only guarantee is what let a campaign
name reach `vault_path / name` unsanitized in context/loader.py, so this
module checks at the sink where all nine of its filesystem writes converge.
"""

from pathlib import Path

import pytest

from campaign.map_generator import MapGenerator


@pytest.mark.parametrize("allowed", [
    "campaign_assets/my-campaign_maps",
    "campaign_assets/my-campaign_maps/portraits",
    "/tmp/ai-gm-maps",
])
def test_known_asset_roots_are_accepted(allowed):
    resolved = MapGenerator._checked_output_dir(Path(allowed))

    assert resolved.is_absolute()


@pytest.mark.parametrize("escape", [
    "/etc",
    "~/.ssh",
    "campaign_assets/../../etc",
    "/var/root",
])
def test_paths_outside_the_asset_roots_are_refused(escape):
    with pytest.raises(ValueError, match="Refusing to write generated assets"):
        MapGenerator._checked_output_dir(Path(escape))


def test_traversal_that_lands_back_inside_is_accepted():
    """Containment is judged on the resolved path, not the literal string."""
    resolved = MapGenerator._checked_output_dir(
        Path("campaign_assets/a/../b_maps")
    )

    assert resolved.name == "b_maps"
    assert "campaign_assets" in resolved.parts
