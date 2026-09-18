"""A DirectAdmin import should use the certificate the backup already carries.

The import used to ask Let's Encrypt for a fresh certificate per domain, which
is what exhausts the 50-per-registered-domain-per-week limit on an archive full
of subdomains -- the code says so itself, and then gives up after five failures.
The archive has the real certificate at backup/<domain>/domain.{cert,key,cacert}.
"""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.services import da_import

from app.tests.test_backup_ssl import make_cert


def _da_tree(tmp_path, domain="babatap.com", cert=None, key=None, ca=None):
    base = tmp_path / "backup" / domain
    base.mkdir(parents=True)
    if cert is not None:
        (base / "domain.cert").write_bytes(cert)
    if key is not None:
        (base / "domain.key").write_bytes(key)
    if ca is not None:
        (base / "domain.cacert").write_bytes(ca)
    return tmp_path


def test_the_certificate_in_the_archive_is_found(tmp_path):
    cert, key = make_cert("babatap.com")
    root = _da_tree(tmp_path, cert=cert, key=key)

    material = da_import._read_da_certificate(root, "babatap.com")

    assert material["certificate"].startswith(b"-----BEGIN CERTIFICATE-----")
    assert b"PRIVATE KEY" in material["private_key"]
    assert material["ca_bundle"] == b""


def test_a_ca_bundle_is_picked_up_when_present(tmp_path):
    cert, key = make_cert("babatap.com")
    root = _da_tree(tmp_path, cert=cert, key=key, ca=cert)

    material = da_import._read_da_certificate(root, "babatap.com")

    assert material["ca_bundle"].startswith(b"-----BEGIN CERTIFICATE-----")


def test_a_domain_with_no_certificate_returns_nothing(tmp_path):
    root = _da_tree(tmp_path)

    assert da_import._read_da_certificate(root, "babatap.com") is None


def test_a_key_without_a_certificate_is_not_half_imported(tmp_path):
    _, key = make_cert("babatap.com")
    root = _da_tree(tmp_path, key=key)

    assert da_import._read_da_certificate(root, "babatap.com") is None


def test_a_placeholder_file_that_is_not_pem_is_rejected(tmp_path):
    """DA writes an empty or stub file for a domain that never had a cert."""
    root = _da_tree(tmp_path, cert=b"", key=b"")

    assert da_import._read_da_certificate(root, "babatap.com") is None


def test_an_absurdly_large_file_is_not_read(tmp_path):
    root = _da_tree(tmp_path, cert=b"-----BEGIN CERTIFICATE-----" + b"x" * (300 * 1024),
                    key=b"-----BEGIN PRIVATE KEY-----")

    assert da_import._read_da_certificate(root, "babatap.com") is None


def test_a_domain_name_cannot_escape_the_backup_directory(tmp_path):
    root = _da_tree(tmp_path)

    assert da_import._read_da_certificate(root, "../../etc") is None
    assert da_import._read_da_certificate(root, "") is None


def test_certbot_is_only_asked_for_domains_the_archive_could_not_supply():
    import inspect

    source = inspect.getsource(da_import._process_archive)
    assert "needs_issue.append(website)" in source
    assert "for website in needs_issue:" in source
    # The CA loop must iterate the leftovers, not every website.
    assert "for website in websites:\n        if consecutive_ssl_failures" not in source


def test_a_site_given_a_certificate_gets_its_vhost_rewritten():
    """Flipping ssl_enabled without re-rendering leaves OpenLiteSpeed serving
    plain HTTP while the panel claims the site is secured."""
    import inspect

    source = inspect.getsource(da_import._process_archive)
    assert "_apply_vhost_ssl(vhost_args, website, paths.get(\"cert\"), paths.get(\"key\"), summary)" in source

    apply_source = inspect.getsource(da_import._apply_vhost_ssl)
    assert "ssl_enabled=True," in apply_source
    assert "defer_reload=True," in apply_source


def test_the_certbot_path_also_rewrites_its_vhost():
    import inspect

    source = inspect.getsource(da_import._process_archive)
    assert '/etc/letsencrypt/live/{website.domain}' in source
    assert source.count("_apply_vhost_ssl(") == 2


def test_one_reload_covers_every_https_vhost():
    import inspect

    source = inspect.getsource(da_import._process_archive)
    tail = source[source.index("for website in needs_issue:"):]

    assert "openlitespeed.reload_service()" in tail
    assert 'if summary["ssl_enabled_domains"]:' in tail


def test_an_expired_imported_certificate_is_flagged():
    import inspect

    source = inspect.getsource(da_import._process_archive)

    assert "has expired" in source
    assert "certificate_expiry" in source
