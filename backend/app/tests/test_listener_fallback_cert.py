"""The HTTPS listener never names a certificate that does not exist (2026-11-10).

ssl-cert's snakeoil came with the Debian phpMyAdmin package only. A server
installed after the switch to the upstream tarball had none, LiteSpeed served
:443 as plain HTTP, and every site failed with ERR_SSL_PROTOCOL_ERROR (found on
a VPS cloned from such a server). Without snakeoil the listener now takes the
panel's own self-signed certificate, which the sync makes sure exists."""
from pathlib import Path

HELPER = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def test_the_listener_falls_back_to_the_panels_own_certificate():
    block = HELPER[HELPER.index('_tools_cert = pathlib.Path("/etc/ssl/certs/ssl-cert-snakeoil.pem")'):]
    block = block[:block.index("def listener_block(")]
    fallback = block.index("if not (_tools_cert.is_file() and _tools_key.is_file()):")
    assert block.index('pathlib.Path("/etc/opanel/certs/_default/fullchain.pem")') > fallback
    assert block.index('pathlib.Path("/etc/opanel/certs/_default/privkey.pem")') > fallback
    # A real certificate on the tools vhost still wins over both.
    assert block.index("if tools_conf.is_file():") > fallback


def test_the_sync_makes_the_fallback_exist_first():
    sync = HELPER[HELPER.index("ols_sync_main_config() {"):]
    sync = sync[:sync.index("<<'PY'")]
    assert "panel_self_signed_ensure >/dev/null 2>&1 || true" in sync
