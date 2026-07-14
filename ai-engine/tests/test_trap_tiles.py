"""build_trap_tile_docs converts scene trap data into valid MAT tile documents."""

from campaign.trap_tiles import build_trap_tile_docs


def test_builds_hidden_mat_tile_from_grid_coords():
    docs = build_trap_tile_docs([{
        "name": "Poison Dart Trap", "x": 8, "y": 6, "w": 1, "h": 1,
        "save_ability": "dex", "save_dc": 13, "damage": "2d6",
        "damage_type": "poison", "description": "Darts spring out.",
    }], grid_px=64)

    assert len(docs) == 1
    d = docs[0]
    # grid -> pixels
    assert (d["x"], d["y"], d["width"], d["height"]) == (512, 384, 64, 64)
    assert d["hidden"] is True
    mat = d["flags"]["monks-active-tiles"]
    assert mat["active"] is True
    assert mat["trigger"] == ["enter"]
    assert len(mat["actions"]) == 1
    action = mat["actions"][0]
    assert action["action"] == "chatmessage"
    assert action["data"]["for"] == "gm"
    # the GM message carries the resolution details
    text = action["data"]["text"]
    assert "Poison Dart Trap" in text
    assert "DEX save DC 13" in text
    assert "2d6 poison on fail" in text
    assert "Darts spring out." in text
    # tagged for redeploy replacement
    assert d["flags"]["aigm-trap"]["version"] == 1


def test_multicell_and_defaults():
    docs = build_trap_tile_docs([{"name": "Pit", "x": 2, "y": 2, "w": 2, "h": 3}])
    d = docs[0]
    assert (d["width"], d["height"]) == (128, 192)
    # no save/damage -> message is just the name, no crash
    assert d["flags"]["monks-active-tiles"]["actions"][0]["data"]["text"].startswith("⚠️ Trap triggered: Pit")


def test_empty_and_malformed_are_skipped():
    assert build_trap_tile_docs(None) == []
    assert build_trap_tile_docs([]) == []
    # malformed coords are skipped, not raised
    assert build_trap_tile_docs([{"name": "bad", "x": "oops"}, "notadict"]) == []
