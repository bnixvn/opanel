"""clamd loads a copy of ClamAV's signatures filtered by clam-juice.

Measured on .41 (2026-09-30): of the 3.6 million signatures in main and daily,
all but 131 thousand are Windows, macOS or Office malware. clamd held them all
-- 1.06 GB, 24 s to load; the filtered set loads in 1.6 s in 180 MB. The
operator asked for github.com/swelljoe/clam-juice in place of the stock set.
"""
import subprocess
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _function(name: str) -> str:
    return HELPER.split(f"\n{name}() {{", 1)[1].split("\n}\n", 1)[0]


def _command(name: str) -> str:
    return HELPER.split(f"\n  {name})\n", 1)[1].split("\n    ;;\n\n", 1)[0]


def _value(name: str) -> str:
    return HELPER.split(f"\n{name}=", 1)[1].split("\n", 1)[0].strip('"')


def test_clam_juice_is_pinned_and_checked_before_it_is_installed():
    commit, digest = _value("CLAMJUICE_COMMIT"), _value("CLAMJUICE_SHA256")
    assert len(commit) == 40 and int(commit, 16) >= 0
    assert len(digest) == 64 and int(digest, 16) >= 0
    body = _function("install_clamjuice")
    assert "raw.githubusercontent.com/swelljoe/clam-juice/${CLAMJUICE_COMMIT}/clam_juice.py" in body
    assert body.index('!= "$CLAMJUICE_SHA256"') < body.index('install -o root -g root -m 0755 "$tmp" "$CLAMJUICE_BIN"')


def test_it_drops_windows_macos_and_office_but_keeps_what_a_web_server_needs():
    args = HELPER.split("\nCLAMJUICE_ARGS=(", 1)[1].split(")", 1)[0].split()
    platforms = set(args[args.index("--exclude-platforms") + 1].split(","))
    assert platforms == {"Win", "Osx", "Doc", "Xls", "Ppt", "Rtf"}
    assert "mdb" in args[args.index("--exclude-types") + 1].split(",")
    ndb = set(args[args.index("--ndb-types") + 1].split(","))
    # HTML (3) and ELF (6) stay; PE (1) and OLE2 (2) go.
    assert {"0", "3", "6", "7"} <= ndb and not {"1", "2"} & ndb


def test_the_filtered_set_lives_where_clamd_may_read_and_scans_do_not_look():
    # Ubuntu's AppArmor profile for clamd allows /var/lib/clamav/** only.
    assert _value("CLAMAV_FILTERED_DIR") == "${CLAMAV_DB_DIR}/opanel-filtered"
    assert _value("CLAMAV_DB_DIR") == "/var/lib/clamav"
    assert "/var/lib/clamav" in HELPER.split("\nCLAMAV_SCAN_PRUNE_PATHS=(", 1)[1].split(")", 1)[0]


def test_a_new_set_is_test_scanned_before_clamd_is_pointed_at_it():
    body = _function("clamav_filter_build")
    assert body.index('clamscan -d "$gen"') < body.index('mv -Tf "${CLAMAV_FILTERED_DIR}/current.new"')
    assert '[[ "$rc" -ne 1' in body  # it must detect the test file
    # Diffs and their signatures inside the .cld are not databases.
    assert 'rm -f -- "$gen"/*.cdiff "$gen"/*.sign' in body
    # Bytecode and LMD's signatures go across unfiltered.
    assert 'clamav_official_db bytecode' in body and "done < <(clamav_extra_dbs)" in body
    # Unpacked on disk, not in a /tmp that may be a tmpfs.
    assert 'TMPDIR="$work"' in body
    # The set before stays for a reload that started on it.
    assert "if (( n > 2 )); then rm -rf" in body


def test_this_script_is_not_a_test_file_to_the_scanners_that_read_it():
    eicar = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-" + "ANTIVIRUS-TEST-FILE!$H+H*"
    assert eicar not in HELPER
    assert "EICAR-STANDARD-' 'ANTIVIRUS-TEST-FILE" in HELPER


def test_lmd_copying_its_signatures_in_again_is_not_a_change():
    body = _function("clamav_filter_fingerprint")
    assert 'sha256sum <"$f"' in body and "clamav_extra_dbs" in body
    assert "stat -c '%n %s %Y'" in body  # size and time for the large official files


def test_the_totals_are_read_from_clam_juices_report():
    script = _function("clamjuice_totals").split("awk '", 1)[1].rsplit("'", 1)[0]
    report = (
        "\n.LDB files:\n  Original:      38,889 signatures\n  Filtered:       4,000 signatures\n"
        "\n======\nTOTAL:\n  Original:   3,286,543 signatures\n"
        "  Filtered:      88,356 signatures (  2.7%)\n  Removed:   3,198,187 signatures\n"
    )
    out = subprocess.run(["awk", script], input=report, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["3286543", "88356"]


def test_clamd_restarts_for_a_new_directory_and_reloads_for_new_files():
    body = _function("clamav_filter_run")
    assert 'clamd_set_database_dir "$CLAMAV_FILTERED_CURRENT"; then\n    clamd_apply restart' in body
    assert "elif (( changed )); then\n    clamd_apply reload" in body
    # A stopped addon is not started by it.
    assert "systemctl is-active --quiet clamav-daemon 2>/dev/null || return 0" in _function("clamd_apply")


def test_freshclam_and_lmd_updates_are_filtered_again_without_a_loop():
    assert "PathChanged=/var/lib/clamav" in HELPER
    build = _function("clamav_filter_build")
    # install -d re-chmods an existing directory: an event for the path unit.
    assert 'if [[ ! -d "$CLAMAV_FILTERED_DIR" ]]; then\n    install -d' in build
    assert "for attempt in 1 2 3; do" in _function("clamav_filter_run")
    assert "flock -w 900 9" in _function("clamav_filter_run_locked")


def test_every_update_brings_it_to_boxes_with_clamav_unless_turned_off():
    hygiene = HELPER.split("\n  log-hygiene)\n", 1)[1].split(";;", 1)[0]
    assert "ensure_clamav_filter" in hygiene
    ensure = _function("ensure_clamav_filter")
    assert "dpkg -s clamav-daemon" in ensure and '"$CLAMAV_FULL_DB_MARKER"' in ensure
    assert "systemctl start --no-block opanel-clamav-filter.service" in ensure


def test_a_fresh_install_filters_before_clamd_first_loads_anything():
    body = _function("install_clamav_engine")
    assert body.index("freshclam") < body.index("clamav_filter_run_locked")


def test_off_goes_back_to_the_full_databases_before_removing_the_copy():
    command = _command("clamav-filter")
    assert 'touch "$CLAMAV_FULL_DB_MARKER"' in command and 'rm -f "$CLAMAV_FULL_DB_MARKER"' in command
    disable = _function("clamav_filter_disable")
    assert disable.index('clamd_set_database_dir "$CLAMAV_DB_DIR"') < disable.index('rm -rf -- "$CLAMAV_FILTERED_DIR"')


def test_removing_the_addon_removes_the_filter():
    body = _function("remove_clamav_engine")
    assert '"$CLAMAV_FILTER_PATH_UNIT" "$CLAMAV_FILTER_SERVICE_UNIT"' in body
    assert 'rm -rf -- "$CLAMAV_FILTERED_DIR" "${CLAMJUICE_BIN%/*}"' in body


def test_the_scanner_page_shows_how_many_signatures_clamd_loads(monkeypatch):
    from app.services import malware_scan

    line = ("installed=1 running=1 lmd=1 lmd_version=2.0.1 monitor=0 "
            "filter=on filter_kept=131000 filter_total=3626585")
    monkeypatch.setattr(malware_scan.shell, "privileged",
                        lambda *a, **k: SimpleNamespace(stdout=line, stderr="", returncode=0))
    status = malware_scan._lmd_status()
    assert status["signature_filter"] == "on"
    assert status["signatures_kept"] == 131000 and status["signatures_total"] == 3626585
    assert "filter=${state} filter_kept=${kept} filter_total=${total}" in _function("clamav_filter_status")
    assert "$(clamav_filter_status)" in _command("clamav-status")
    app = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "mw.signature_filter === 'on'" in app
