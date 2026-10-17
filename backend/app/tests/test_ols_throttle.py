"""LiteSpeed's per-client throttle for the whole server (as SNPanel's, 2026-10-17)."""
import json
import pathlib
import subprocess
import sys

import pytest

from app.services import ols_throttle, panel_settings

HELPER_PATH = pathlib.Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh"


def _editor(tmp_path):
    text = HELPER_PATH.read_text(encoding="utf-8")
    body = text.split("ols_throttle() {", 1)[1]
    script = body.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    path = tmp_path / "editor.py"
    path.write_text(script, encoding="utf-8")
    return path


SAMPLE = """perClientConnLimit{
    staticReqPerSec                          0
    outBandwidth                             0
    softLimit                                10000
    hardLimit                                10000
    gracePeriod                              15
    banPeriod                                300
}

accessControl{
\tallow                                   ALL
\tdeny
}
"""


def test_the_helper_writes_the_block_and_trusts_this_server(tmp_path):
    editor, conf = _editor(tmp_path), tmp_path / "httpd_config.conf"
    conf.write_text(SAMPLE, encoding="utf-8")
    subprocess.run([sys.executable, str(editor), str(conf), "set", "200", "20", "60", "120", "15", "60",
                    "203.0.113.7", "fe80::1%eth0"], check=True, capture_output=True)
    text = conf.read_text(encoding="utf-8")
    assert "    banPeriod                                60\n" in text and "\n}\n" in text  # the brace stays on its own line
    assert "dynReqPerSec                             20" in text and "outBandwidth" in text
    assert "allow                                   ALL, 127.0.0.1T, ::1T, 203.0.113.7T\n" in text
    got = subprocess.run([sys.executable, str(editor), str(conf), "get"], check=True, capture_output=True, text=True).stdout
    assert json.loads(got) == {"staticReqPerSec": 200, "dynReqPerSec": 20, "softLimit": 60, "hardLimit": 120,
                               "gracePeriod": 15, "banPeriod": 60}
    # Again: nothing is trusted twice.
    subprocess.run([sys.executable, str(editor), str(conf), "set", "0", "0", "10000", "10000", "15", "300", "203.0.113.7"],
                   check=True, capture_output=True)
    assert conf.read_text(encoding="utf-8").count("203.0.113.7T") == 1


def test_values_are_checked(monkeypatch):
    with pytest.raises(ValueError):
        ols_throttle.validate({**ols_throttle.DEFAULTS, "conn_hard": 10})
    with pytest.raises(ValueError):
        ols_throttle.validate({**ols_throttle.DEFAULTS, "dynamic_rps": -1})
    with pytest.raises(ValueError):
        ols_throttle.validate({**ols_throttle.DEFAULTS, "ban_secs": "soon"})


def test_off_writes_no_limits_and_keeps_the_numbers_for_later(monkeypatch, tmp_path):
    store, calls = {}, []
    monkeypatch.setattr(panel_settings, "_read_raw", lambda: dict(store))
    monkeypatch.setattr(panel_settings, "_write_raw", lambda data: store.update(data))
    monkeypatch.setattr(ols_throttle.shell, "privileged", lambda verb, helper_args=None, **kw: calls.append((verb, helper_args)))
    chosen = {**ols_throttle.DEFAULTS, "dynamic_rps": 30}
    assert ols_throttle.apply(True, chosen)["dynamic_rps"] == 30
    assert calls[-1] == ("ols-throttle-set", ["200", "30", "60", "120", "15", "60"])
    back = ols_throttle.apply(False, chosen)
    assert calls[-1] == ("ols-throttle-set", ["0", "0", "10000", "10000", "15", "300"])
    assert back == {"enabled": False, **chosen}
