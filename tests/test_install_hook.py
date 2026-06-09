import json

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
