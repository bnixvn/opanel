"""Swap before the build only on a small server (operator, 2026-11-09: "server
người ta ram 4G, trong khi cần có 720MB")."""
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-ensure-swap.sh").read_text(encoding="utf-8")


def test_two_gigabytes_or_more_get_no_swap():
    assert "SMALL_RAM_MB=2048" in SCRIPT
    assert 'if (( $(ram_mb) >= SMALL_RAM_MB )); then' in SCRIPT
    assert SCRIPT.index("SMALL_RAM_MB )); then") < SCRIPT.index("fallocate")


def test_existing_swap_and_the_opt_out_are_left_alone():
    assert 'swapon --show --noheadings' in SCRIPT and 'SWAP_OPT_OUT="/etc/opanel/no-swap"' in SCRIPT
