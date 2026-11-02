"""Email and Resource limits are installed from the server's shell only
(operator, 2026-11-02): the Addons page does not offer them, the API refuses
them, and `opanel addon install <id>` installs them in the foreground."""
from pathlib import Path

import pytest

from app import addon_cli
from app.services import addons

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OPANELCTL = (PROJECT_ROOT / "installer" / "files" / "opanelctl").read_text(encoding="utf-8")
SHELL_ONLY = ("mail", "limits")


class _Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(addons, "STATE_FILE", tmp_path / "addons.json")


@pytest.fixture
def box(monkeypatch):
    """A box where nothing is installed yet; installs and removals take effect."""
    state = {"installed": set(), "calls": []}

    def fake(command, helper_args=None, check=True, input=None, sensitive=False, fallback=None):
        addon_id = (helper_args or [""])[0]
        state["calls"].append((command, addon_id))
        if command == "addon-status":
            on = int(addon_id in state["installed"])
            return _Result(f"installed={on} running={on} enabled={on}")
        if command == "addon-install":
            state["installed"].add(addon_id)
        if command == "addon-uninstall":
            state["installed"].discard(addon_id)
        return _Result("ok")

    monkeypatch.setattr(addons.shell, "privileged", fake)
    monkeypatch.setattr(addons, "_after_lifecycle", lambda *a, **k: None)
    return state


def test_the_page_does_not_offer_them_until_they_are_installed(box):
    listed = {entry["id"] for entry in addons.catalog()}
    assert not listed & set(SHELL_ONLY)
    assert "fail2ban" in listed and "dns" in listed  # the others are still offered
    box["installed"].add("mail")
    entry = next(e for e in addons.catalog() if e["id"] == "mail")
    # Installed from the shell, it is listed to be managed (settings, stop, remove).
    assert entry["installed"] and entry["cli_only"]
    assert entry["install_command"] == "opanel addon install mail"
    assert "limits" not in {e["id"] for e in addons.catalog()}


@pytest.mark.parametrize("addon_id", SHELL_ONLY)
def test_the_panel_cannot_install_them(box, addon_id):
    with pytest.raises(addons.CliOnly, match=f"opanel addon install {addon_id}"):
        addons.install(addon_id, actor="admin")
    assert ("addon-install", addon_id) not in box["calls"]


def test_the_api_answers_403_with_the_command(box, monkeypatch):
    from fastapi import HTTPException

    from app.api import addons as api

    from types import SimpleNamespace

    monkeypatch.setattr(api, "_admin", lambda user: None)
    with pytest.raises(HTTPException) as caught:
        api.install_addon("mail", request=None, current_user=SimpleNamespace(id=1, username="admin"), db=None)
    assert caught.value.status_code == 403
    assert "opanel addon install mail" in caught.value.detail


def test_the_shell_installs_in_the_foreground(box):
    result = addons.install("mail", actor="shell", from_shell=True)
    # Done by the time it returns: the CLI process exits right after.
    assert ("addon-install", "mail") in box["calls"]
    assert result["installed"] is True and result["busy"] is False and result["last_error"] == ""


def test_the_shell_records_a_failure(box, monkeypatch):
    def failing(command, helper_args=None, **kw):
        if command == "addon-install":
            return _Result(stderr="apt could not install exim4", returncode=1)
        return _Result("installed=0 running=0 enabled=0")

    monkeypatch.setattr(addons.shell, "privileged", failing)
    assert addon_cli.main(["install", "mail"]) == 1
    assert "apt could not install exim4" in addons.status("mail")["last_error"]
    assert addons.status("mail")["busy"] is False


def test_the_cli(box, capsys):
    assert addon_cli.main(["install", "mail"]) == 0
    assert addon_cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "mail" in out and "installed" in out and "(shell only)" in out
    assert addon_cli.main(["install", "mail"]) == 1          # already installed
    assert addon_cli.main(["install", "no-such"]) == 2
    assert addon_cli.main(["frobnicate", "mail"]) == 2
    assert addon_cli.main(["remove", "mail"]) == 0
    assert "mail" not in box["installed"]


def test_opanel_ctl_has_the_addon_command():
    assert '  addon|--addon) addon_command "${2:-}" "${3:-}" ;;' in OPANELCTL
    # The panel's own code, as the panel user, one at a time; not `-m`, which
    # cannot run a compiled (.so) module.
    assert "from app.addon_cli import main" in OPANELCTL
    assert "runuser -u opanel" in OPANELCTL and "flock -n" in OPANELCTL
