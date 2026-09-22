"""A subscribed blocklist must not be able to take the host off the network.

OPANEL_BLOCKLIST is jumped from INPUT at position 1, ahead of the loopback and
ESTABLISHED accepts in OPANEL_INPUT, and it contains a single DROP against the
ipset. The entries come from URLs the operator subscribes to, so the content
author is a third party.

The filter rejected loopback, private, link-local, multicast, reserved and
unspecified networks -- and none of those flags is true of 0.0.0.0/1, which
contains 127.0.0.1. One such line therefore dropped every inbound packet
including loopback: panel, SSH, web, mail, the panel's own health check and the
phpMyAdmin SSO call to 127.0.0.1. firewall_persist_rules saved it and the daily
timer re-armed it after reboot, leaving an out-of-band console as the only way
back in.

These tests run the helper's real filter, extracted from the script.
"""
from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh"
HELPER_TEXT = HELPER.read_text(encoding="utf-8")


def _extract_blocklist_filter() -> str:
    """The python3 heredoc inside firewall_blocklist_run."""
    marker = 'python3 - "$fetched" "$tmp" "$rules_tmp" <<\'PY\'\n'
    start = HELPER_TEXT.index(marker) + len(marker)
    end = HELPER_TEXT.index("\nPY\n", start)
    return HELPER_TEXT[start:end]


def _run_filter(tmp_path: Path, lines: list[str]) -> list[str]:
    """Feed the real filter and return the networks it kept."""
    script = tmp_path / "filter.py"
    script.write_text(_extract_blocklist_filter(), encoding="utf-8")
    fetched = tmp_path / "fetched.txt"
    fetched.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp_path / "out.txt"
    rules = tmp_path / "rules.txt"
    completed = subprocess.run(
        [sys.executable, str(script), str(fetched), str(out), str(rules)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    if not out.exists():
        return []
    return [l.strip() for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]


# --------------------------------------------------------------------------
# the filter
# --------------------------------------------------------------------------
@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "0.0.0.0/1", "128.0.0.0/1", "0.0.0.0/4", "32.0.0.0/3"])
def test_a_prefix_wide_enough_to_matter_is_refused(tmp_path, cidr):
    assert _run_filter(tmp_path, [cidr]) == [], f"{cidr} must not reach the ipset"


@pytest.mark.parametrize("cidr", ["::/0", "8000::/1", "2000::/3"])
def test_the_same_holds_for_ipv6(tmp_path, cidr):
    assert _run_filter(tmp_path, [cidr]) == []


def test_the_none_of_the_old_flags_would_have_caught_these():
    """Why the floor was needed: the existing predicate says these are fine."""
    for cidr in ("0.0.0.0/0", "0.0.0.0/1", "0.0.0.0/4", "8000::/1"):
        net = ipaddress.ip_network(cidr, strict=False)
        assert not any([
            net.is_loopback, net.is_private, net.is_link_local,
            net.is_multicast, net.is_reserved, net.is_unspecified,
        ]), f"{cidr} would have been rejected already -- the floor is redundant"
    assert ipaddress.ip_address("127.0.0.1") in ipaddress.ip_network("0.0.0.0/1")


# Globally routable ranges. The documentation blocks (198.51.100.0/24,
# 203.0.113.0/24, 2001:db8::/32) are is_reserved, so the pre-existing filter
# already drops them -- using one here would have tested nothing.
@pytest.mark.parametrize("entry", ["8.8.8.8", "8.8.8.0/24", "45.33.0.0/16", "2606:4700::/32"])
def test_ordinary_entries_still_load(tmp_path, entry):
    kept = _run_filter(tmp_path, [entry])
    assert kept, f"{entry} is a normal blocklist entry and must survive"


def test_private_and_loopback_are_still_refused(tmp_path):
    assert _run_filter(tmp_path, ["127.0.0.0/8", "10.0.0.0/8", "192.168.0.0/16", "::1/128"]) == []


def test_a_wide_entry_does_not_take_the_good_ones_with_it(tmp_path):
    kept = _run_filter(tmp_path, ["0.0.0.0/1", "8.8.8.0/24", "0.0.0.0/0", "1.1.1.1"])
    assert "8.8.8.0/24" in kept
    assert not any(k.startswith("0.0.0.0") for k in kept)


# --------------------------------------------------------------------------
# the load site, and the chain itself
# --------------------------------------------------------------------------
def test_the_floor_is_reapplied_where_the_set_is_loaded():
    """/var/lib/opanel is opanel-owned, so blocklist.set can be written
    without going through the fetch filter at all."""
    awk = HELPER_TEXT[HELPER_TEXT.index("awk -v v4="):]
    awk = awk[: awk.index("ipset restore")]
    assert "prefix < 8" in awk and "prefix < 16" in awk, (
        "the load path must not trust the stored file"
    )


def test_loopback_leaves_the_blocklist_chain_first():
    for tool in ("iptables", "ip6tables"):
        assert f"{tool} -I OPANEL_BLOCKLIST 1 -i lo -j RETURN" in HELPER_TEXT, (
            f"{tool}: loopback must RETURN before the ipset DROP, because this "
            "chain is jumped from INPUT ahead of OPANEL_INPUT's own lo accept"
        )


def test_the_readme_no_longer_claims_the_firewall_cannot_lock_you_out():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    firewall = readme[readme.index("## Firewall"):][:3000]
    assert "It can still lock you out from the other direction" in firewall, (
        "the ACCEPT policy stops a bad allow rule locking you out; it does "
        "nothing about a DROP reached before any allowance"
    )
