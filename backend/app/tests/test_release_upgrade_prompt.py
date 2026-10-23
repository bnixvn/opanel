"""Ubuntu's release-upgrade offer is switched off (operator, 2026-10-24).

The function is run for real, by bash, on a copy of the file, for each shape
/etc/update-manager/release-upgrades can have.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = "installer/files/opanel-helper.sh"
NAME = "ensure_no_release_upgrade_prompt"
# log-hygiene runs at install and on every update.
CALLS = [("installer/files/opanel-helper.sh", "    ensure_network_tuning\n    ensure_no_release_upgrade_prompt\n")]


def _function(script: str, name: str) -> str:
    start = script.index(f"\n{name}() {{\n") + 1
    return script[start:script.index("\n}\n", start) + 3]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("before, after", [
    ("[DEFAULT]\nPrompt=lts\n", "[DEFAULT]\nPrompt=never\n"),
    ("# comment\n[DEFAULT]\n  Prompt = normal\n", "# comment\n[DEFAULT]\nPrompt=never\n"),
    ("[DEFAULT]\n", "[DEFAULT]\nPrompt=never\n"),
    ("", "[DEFAULT]\nPrompt=never\n"),
])
def test_the_release_upgrade_offer_is_switched_off(tmp_path, before, after):
    conf = tmp_path / "release-upgrades"
    conf.write_text(before, encoding="utf-8")
    cache = tmp_path / "release-upgrade-available"
    cache.write_text("New release '26.04.1 LTS' available.\n", encoding="utf-8")
    fn = _function((ROOT / SCRIPT).read_text(encoding="utf-8"), NAME)
    fn = fn.replace("/etc/update-manager/release-upgrades", conf.as_posix())
    fn = fn.replace("/var/lib/ubuntu-release-upgrader/release-upgrade-available", cache.as_posix())
    subprocess.run(["bash", "-c", f"set -euo pipefail\n{fn}\n{NAME}"], check=True)
    assert conf.read_text(encoding="utf-8") == after
    assert not cache.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_a_box_without_the_upgrader_is_left_alone(tmp_path):
    fn = _function((ROOT / SCRIPT).read_text(encoding="utf-8"), NAME)
    fn = fn.replace("/etc/update-manager/release-upgrades", (tmp_path / "missing").as_posix())
    subprocess.run(["bash", "-c", f"set -euo pipefail\n{fn}\n{NAME}"], check=True)
    assert not (tmp_path / "missing").exists()


def test_it_runs_on_every_install_and_update():
    for path, call in CALLS:
        assert call in (ROOT / path).read_text(encoding="utf-8"), path
