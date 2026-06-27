"""Unit tests for layout mask generation."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / ".." / ".."))
from campaign.map_generator import MapGenerator


def _run(coro):
    """Helper to run async methods in sync pytest tests."""
    return asyncio.run(coro)


@pytest.fixture
def mg():
    return MapGenerator(comfyui_url="http://127.0.0.1:9999")


class TestLayoutMask:
    """Tests for generate_layout_mask output dimensions and correctness."""

    def test_small_grid_uses_computed_dimensions(self, mg, tmp_path):
        """4x4 grid at 128px should produce 512x512, not 1024x768."""
        scene = {
            "walls": [[0, 0, 4, 0], [4, 0, 4, 4], [4, 4, 0, 4], [0, 4, 0, 0]],
            "doors": [{"c": [1, 4, 3, 4], "door": 1, "ds": 0}],
            "_output_dir": str(tmp_path),
        }
        mask = _run(mg.generate_layout_mask(scene, 1024, 768, 128))
        from PIL import Image
        img = Image.open(str(mask))
        # 4x4 grid spans 0→4, so width = (4+1)*128 = 640
        assert img.size == (640, 640), f"Expected 640x640, got {img.size}"

    def test_large_grid_uses_requested_dimensions(self, mg, tmp_path):
        """32x24 grid at 64px = 2048x1536 > 1024x768, cap at 1024x768."""
        scene = {
            "walls": [[0, 0, 32, 0], [32, 0, 32, 24], [32, 24, 0, 24], [0, 24, 0, 0]],
            "doors": [{"c": [14, 24, 18, 24], "door": 1, "ds": 0}],
            "_output_dir": str(tmp_path),
        }
        mask = _run(mg.generate_layout_mask(scene, 1024, 768, 64))
        from PIL import Image
        img = Image.open(str(mask))
        assert img.size == (1024, 768), f"Expected 1024x768, got {img.size}"

    def test_standard_room_matches_default(self, mg, tmp_path):
        """16x12 grid at 64px = 1024x768, matches default exactly."""
        scene = {
            "walls": [[0, 0, 16, 0], [16, 0, 16, 12], [16, 12, 0, 12], [0, 12, 0, 0]],
            "_output_dir": str(tmp_path),
        }
        mask = _run(mg.generate_layout_mask(scene, 1024, 768, 64))
        from PIL import Image
        img = Image.open(str(mask))
        assert img.size == (1024, 768)
        # Verify walls are white (255), doors/background are black (0)
        # Check a wall pixel (top edge, middle)
        assert img.getpixel((512, 1)) > 200, "Wall pixel should be white"
        # Check background (away from walls)
        assert img.getpixel((100, 100)) < 50, "Background should be black"

    def test_no_walls_returns_none(self, mg, tmp_path):
        """Empty walls/doors returns None."""
        scene = {"walls": [], "doors": [], "_output_dir": str(tmp_path)}
        mask = _run(mg.generate_layout_mask(scene, 1024, 768, 64))
        assert mask is None

    def test_no_walls_no_doors_returns_none(self, mg, tmp_path):
        """Both empty returns None."""
        scene = {"_output_dir": str(tmp_path)}
        mask = _run(mg.generate_layout_mask(scene, 1024, 768, 64))
        assert mask is None

    def test_doors_create_gaps(self, mg, tmp_path):
        """Door line is black (0) even though walls are white."""
        scene = {
            "walls": [[0, 0, 4, 0], [4, 0, 4, 4], [4, 4, 0, 4], [0, 4, 0, 0]],
            "doors": [{"c": [1, 4, 3, 4], "door": 1, "ds": 0}],
            "_output_dir": str(tmp_path),
        }
        mask = _run(mg.generate_layout_mask(scene, 1024, 768, 128))
        from PIL import Image
        img = Image.open(str(mask))
        # Door gap: y=512, x from 128 to 384 (door from grid 1→3)
        # Pixel (256, 512) is midpoint of door gap → should be black
        assert img.getpixel((256, 512)) < 50, "Door gap should be black"
        # Pixel (450, 512) is on the wall portion (x=450 > door end=384)
        assert img.getpixel((450, 512)) > 200, "Wall pixel after door gap should be white"
