import foundry.module_deploy as md
from tests.test_module_deploy import _fake_repo


def test_resolve_prefers_configured_path_and_rejects_a_missing_one(tmp_path):
    assert md.resolve_modules_path(str(tmp_path)) == tmp_path
    assert md.resolve_modules_path(str(tmp_path / "nope")) is None      # no silent fallback to a guessed dir


def test_resolve_scans_candidates_in_order(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    b.mkdir()
    monkeypatch.setattr(md, "_CANDIDATE_DIRS", [str(a), str(b)])
    assert md.resolve_modules_path() == b
    a.mkdir()
    assert md.resolve_modules_path() == a
    monkeypatch.setattr(md, "_CANDIDATE_DIRS", [str(tmp_path / "zz")])
    assert md.resolve_modules_path() is None


def test_deploy_without_a_foundry_dir_or_source_reports_false(tmp_path, monkeypatch):
    _fake_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(md, "_CANDIDATE_DIRS", [])
    assert md.deploy_module("aigm-control-panel") is False              # no modules dir
    assert md.deploy_module("no-such-module", str(tmp_path)) is False   # no source


def test_a_copy_failure_is_reported_not_raised(tmp_path, monkeypatch):
    modules = _fake_repo(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(md.shutil, "copytree", boom)
    assert md.deploy_module("aigm-control-panel", str(modules)) is False


def test_the_tts_module_deploys_under_its_id(tmp_path, monkeypatch):
    modules = _fake_repo(tmp_path, monkeypatch)
    src = md._MODULES_SRC / "aigm-tts"
    src.mkdir()
    (src / "module.json").write_text("{}")
    assert md.deploy_aigm_tts(str(modules)) is True
    assert (modules / "aigm-tts" / "module.json").is_file()
