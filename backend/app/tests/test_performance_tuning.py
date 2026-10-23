"""What a site's speed got from the 2026-10-23 tuning, and that it reaches every server.

A customer found OPanel slower than wptangtoc-ols. Measured on .41 with a
plain WordPress (no cache plugin):

- the first request after an idle spell took 1.7 s: PHP starts cold and
  compiles WordPress again. OPcache's file cache brought it to 0.5 s;
- open_basedir turns PHP's realpath cache off (a fifth of an uncached page),
  now off unless a site or its package asks (test_php_isolation.py);
- OPcache stat'ed every included file on every request (revalidate_freq = 0);
- static files had a week of browser cache, where PageSpeed asks for a year;
- the network stack ran cubic, BBR is what wptangtoc-ols turns on.

The WAF made no measurable difference, so it stays as it is.
"""
from __future__ import annotations

from pathlib import Path

from app.services import php

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _body(name: str) -> str:
    body = HELPER[HELPER.index(f"{name}() {{"):]
    return body[:body.index("\n}\n")]


def test_every_new_step_runs_on_every_update():
    hygiene = HELPER[HELPER.index("  log-hygiene)"):]
    hygiene = hygiene[:hygiene.index("    ;;")]
    for step in ("ensure_ols_long_expires", "ensure_php_revalidate_freq",
                 "ensure_php_file_cache_all", "ensure_network_tuning"):
        assert step in hygiene, step


def test_the_file_cache_is_the_users_own_and_its_settings_are_roots():
    body = _body("ensure_php_file_cache")
    assert 'install -d -o "$user" -g "$user" -m 0700 "$cache"' in body, "no other uid writes code this PHP runs"
    assert 'install -d -o root -g root -m 0755 /etc/opanel /etc/opanel/php-users "$ini_dir"' in body
    assert "opcache.file_cache=${cache}" in body
    assert '/var/lib/php/opcache/${user}' in body, "outside /home: not in backups, not in the quota"
    # A new site gets it with its PHP folders, and deleting the user removes it.
    assert 'ensure_php_file_cache "$user"' in _body("ensure_php_runtime_dirs")
    assert '/var/lib/php/opcache/$user' in HELPER and '/etc/opanel/php-users/$user' in HELPER


def test_static_files_get_a_year_and_only_the_stock_week_is_raised():
    body = _body("ensure_ols_long_expires")
    assert "=A604800" in body and "=A31536000" in body
    assert "=A604800/=A31536000/g" in body
    assert "text\\/javascript=A31536000" in _body("ensure_ols_js_expires")


def test_opcache_rechecks_files_every_ten_seconds():
    assert php.recommend_php_config()["opcache_revalidate_freq"] == 10
    assert php.recommend_php_config()["opcache_validate_timestamps"] == 1, "edits must still be seen"
    body = _body("ensure_php_revalidate_freq")
    assert "revalidate_freq[[:space:]]*=[[:space:]]*0[[:space:]]*$" in body, "only the old default is changed"


def test_network_tuning_uses_bbr_only_where_the_kernel_has_it():
    body = _body("ensure_network_tuning")
    assert "modprobe tcp_bbr" in body and "tcp_available_congestion_control" in body
    assert "net.ipv4.tcp_congestion_control = bbr" in body and "net.core.default_qdisc = fq" in body
    assert "net.core.somaxconn = 65535" in body and "net.ipv4.tcp_fastopen = 3" in body
    assert 'cmp -s "$tmp" "$conf"' in body, "an unchanged file is not rewritten on every update"
