"""The control panel drifted to a stale, wrongly-named copy because only aigm-tts
was ever deployed. Deployment must land under the module id and stay current."""
import foundry.module_deploy as md


def _fake_repo(tmp_path, monkeypatch):
    src = tmp_path / "repo" / "foundry-module"
    (src / "aigm-control-panel" / "scripts").mkdir(parents=True)
    (src / "aigm-control-panel" / "module.json").write_text('{"id": "aigm-control-panel", "version": "2.0.0"}')
    (src / "aigm-control-panel" / "scripts" / "main.mjs").write_text("// entry")
    (src / "aigm-control-panel" / "tests").mkdir()
    (src / "aigm-control-panel" / "tests" / "x.test.mjs").write_text("// dev only")
    monkeypatch.setattr(md, "_MODULES_SRC", src)
    modules = tmp_path / "Data" / "modules"
    modules.mkdir(parents=True)
    return modules


def test_the_panel_lands_under_its_module_id_without_dev_files(tmp_path, monkeypatch):
    modules = _fake_repo(tmp_path, monkeypatch)

    assert md.deploy_aigm_control_panel(str(modules)) is True

    dest = modules / "aigm-control-panel"
    assert (dest / "module.json").is_file() and (dest / "scripts" / "main.mjs").is_file()
    assert not (dest / "tests").exists()


def test_a_stale_installed_copy_is_replaced_not_merged(tmp_path, monkeypatch):
    modules = _fake_repo(tmp_path, monkeypatch)
    stale = modules / "aigm-control-panel"
    stale.mkdir()
    (stale / "old-only.js").write_text("v1")

    md.deploy_aigm_control_panel(str(modules))

    assert not (stale / "old-only.js").exists()
    assert '"2.0.0"' in (stale / "module.json").read_text()


def test_deploy_reports_false_when_there_is_no_modules_dir_or_no_source(tmp_path, monkeypatch):
    _fake_repo(tmp_path, monkeypatch)
    assert md.deploy_module("aigm-control-panel", str(tmp_path / "nowhere")) is False
    assert md.deploy_module("no-such-module", str(tmp_path / "Data" / "modules")) is False
