"""The top bar's status chips and the pages behind them (operator, 2026-10-06):
CPU opens a process monitor (top -c), RAM where the memory is, Disk what takes
the space, Network the traffic of each interface. The language, light/dark and
the account menu moved to the foot of the sidebar.

What must hold: the pages are the administrator's alone (a process list shows
every tenant's command lines); top runs through the helper, since the API
cannot see other users' processes; the disk scan does not count backups kept
under /home twice, and what it does not name is "other".
"""
import inspect
from pathlib import Path

from app.api import server_monitor as api
from app.services import server_monitor

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def test_every_route_is_the_administrators():
    for route in (api.get_processes, api.get_memory, api.get_disk, api.scan_disk, api.get_traffic):
        assert "ensure_role(current_user.role, Role.admin)" in inspect.getsource(route), route.__name__


def test_top_and_du_run_as_root_through_the_helper():
    assert 'shell.privileged("process-top"' in inspect.getsource(server_monitor.processes)
    assert 'shell.privileged("disk-usage-scan"' in inspect.getsource(server_monitor._scan)
    top = HELPER.split("\nprocess_top() {", 1)[1].split("\n}\n", 1)[0]
    assert "top -b -n 1 -c" in top
    scan = HELPER.split("\ndisk_usage_scan() {", 1)[1].split("\n}\n", 1)[0]
    assert "timeout 900 du -sxb" in scan and '! -L "$path"' in scan


def test_backups_under_home_are_not_counted_twice():
    output = "\n".join([
        "home\t10000\t/home\t/",
        "databases\t3000\t/var/lib/mysql\t/",
        "backups\t2000\t/var/backups/opanel\t/",
        "backups\t1500\t/home/admin/opanel-backups\t/",
        "backups\t500\t/home/admin/backups\t/",
        "logs\t700\t/var/log\t/",
        "panel\t300\t/opt/opanel\t/",
        "home\tnot-a-number\t/x\t/",
    ])
    parts = {(row["mount"], row["key"]): row["bytes"] for row in server_monitor._parse_scan(output)}
    assert parts == {("/", "websites"): 8000, ("/", "databases"): 3000, ("/", "backups"): 4000,
                     ("/", "logs"): 700, ("/", "panel"): 300}


def test_what_the_scan_does_not_name_is_other(monkeypatch):
    monkeypatch.setattr(server_monitor, "filesystems", lambda: [
        {"device": "/dev/vda1", "type": "ext4", "size": 100000, "used": 20000, "available": 80000, "mount": "/"},
        {"device": "/dev/vdb1", "type": "ext4", "size": 50000, "used": 1000, "available": 49000, "mount": "/data"},
    ])
    monkeypatch.setattr(server_monitor, "_read_cache", lambda: {"time": 9_999_999_999, "parts": [
        {"mount": "/", "key": "websites", "bytes": 8000}, {"mount": "/", "key": "databases", "bytes": 3000}]})
    result = server_monitor.disk(auto_scan=False)
    root, data = result["filesystems"]
    assert [(part["key"], part["bytes"]) for part in root["parts"]] == [("websites", 8000), ("databases", 3000), ("other", 9000)]
    assert data["parts"] == [], "a disk the scan did not reach shows used and free only"


def test_traffic_reads_each_interface_but_lo(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    dev.write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo:  1000      10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0\n"
        "  eth0: 52000     400    1    0    0     0          0         0    81000     300    0    0    0     0       0          0\n",
        encoding="utf-8")
    real = Path

    def fake(path, *args):
        return dev if str(path) == "/proc/net/dev" else real(path, *args)

    monkeypatch.setattr(server_monitor, "Path", fake)
    result = server_monitor.traffic()
    assert result["interfaces"] == [{"name": "eth0", "rx_bytes": 52000, "rx_packets": 400, "rx_errors": 1,
                                     "tx_bytes": 81000, "tx_packets": 300, "tx_errors": 0}]


def test_the_shell_has_the_chips_on_top_and_the_account_below():
    assert "{isAdmin && renderTopStats()}" in APP
    assert 'className="top-version"' in APP
    assert '<div className="sidebar-foot">' in APP and "renderLanguageToggle('sidebar-tool')" in APP
    assert "renderLanguageToggle('secondary compact-btn top-lang')" not in APP
    stats = APP.split("  function renderTopStats() {", 1)[1].split("\n  function ", 1)[0]
    for page in ("processes", "ramUsage", "diskUsage", "traffic"):
        assert f"['{page}'," in stats, page
    for route in ("processes: '/processes'", "ramUsage: '/ram-usage'", "diskUsage: '/disk-usage'", "traffic: '/traffic'"):
        assert route in APP, route
    assert "dash-resources" not in APP, "the dashboard no longer carries the resource cards"
