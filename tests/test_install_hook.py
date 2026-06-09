import json

import pytest

import install_hook


def _commands(settings):
    return [
        h["command"]
        for g in settings.get("hooks", {}).get("SessionStart", [])
        for h in g.get("hooks", [])
    ]


def test_add_hook_when_absent():
    s = install_hook.add_hook({}, "CMD")
    assert _commands(s) == ["CMD"]


def test_add_hook_idempotent():
    s = install_hook.add_hook({}, "CMD")
    s = install_hook.add_hook(s, "CMD")
    assert _commands(s) == ["CMD"]  # not duplicated


def test_add_hook_preserves_existing():
    existing = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "OTHER"}]}]}}
    s = install_hook.add_hook(existing, "CMD")
    assert set(_commands(s)) == {"OTHER", "CMD"}


def test_remove_hook():
    s = install_hook.add_hook({}, "CMD")
    s = install_hook.remove_hook(s, "CMD")
    assert "CMD" not in _commands(s)


def test_main_writes_and_backs_up(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text('{"existing": true}\n')
    install_hook.main(["install", "--settings", str(settings), "--command", "CMD"])
    data = json.loads(settings.read_text())
    assert data["existing"] is True
    assert (tmp_path / "settings.json.bak").exists()
    assert _commands(data) == ["CMD"]


def test_remove_hook_no_hooks_key_is_noop():
    s = install_hook.remove_hook({"x": 1}, "CMD")
    assert s == {"x": 1}  # must NOT add a 'hooks' key


def test_remove_hook_preserves_other_hooks():
    s = {"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "CMD"}]},
        {"hooks": [{"type": "command", "command": "OTHER"}]},
    ]}}
    install_hook.remove_hook(s, "CMD")
    assert _commands(s) == ["OTHER"]


def test_main_uninstall_removes_hook(tmp_path):
    settings = tmp_path / "settings.json"
    install_hook.main(["install", "--settings", str(settings), "--command", "CMD"])
    install_hook.main(["uninstall", "--settings", str(settings), "--command", "CMD"])
    data = json.loads(settings.read_text())
    assert "CMD" not in _commands(data)


def test_main_malformed_json_raises_and_preserves_file(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{bad json")
    with pytest.raises(SystemExit):
        install_hook.main(["install", "--settings", str(settings), "--command", "CMD"])
    assert settings.read_text() == "{bad json"  # not clobbered


def test_main_backup_holds_original(tmp_path):
    settings = tmp_path / "settings.json"
    original = '{"existing": true}\n'
    settings.write_text(original)
    install_hook.main(["install", "--settings", str(settings), "--command", "CMD"])
    assert (tmp_path / "settings.json.bak").read_text() == original
