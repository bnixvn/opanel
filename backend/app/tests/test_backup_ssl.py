"""A restored site has to answer HTTPS without anyone re-issuing a certificate.

The box being restored onto has no certbot account for the domain, no renewal
config, and usually no DNS pointing at it yet -- so "issue a new one" is not
something the restore can fall back on. The certificate has to travel inside
the archive.
"""
import datetime as dt
import json
import os
import sys
import tarfile

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.services import backup
from app.services import ssl as ssl_service


def make_cert(domain="api.babatap.com", days=60):
    """A real key pair, so the key/cert pairing check is actually exercised."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )


class _User:
    id = 7
    username = "babatapapp"
    email = "owner@example.test"
    hashed_password = ""
    role = "user"
    is_active = True
    website_limit = 5
    storage_limit_mb = 0


class _Site:
    def __init__(self, domain, root_path, **kw):
        self.id = 1
        self.domain = domain
        self.root_path = str(root_path)
        self.php_version = "8.3"
        self.app_type = "php"
        self.status = "active"
        self.document_root = "public_html"
        self.nginx_custom = ""
        self.nginx_rewrite_mode = "none"
        self.waf_enabled = False
        self.waf_default_rules = ""
        self.waf_custom_rules = ""
        self.aliases = []
        self.ssl_enabled = kw.get("ssl_enabled", True)
        self.ssl_mode = kw.get("ssl_mode", "letsencrypt")
        self.ssl_cert_path = kw.get("ssl_cert_path")
        self.ssl_key_path = kw.get("ssl_key_path")
        self.ssl_ca_path = kw.get("ssl_ca_path")
        self.ssl_reuse_name = kw.get("ssl_reuse_name")
        self.ssl_wildcard = kw.get("ssl_wildcard", False)


def _db_with(sites):
    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *a, **k):
            return self

        def order_by(self, *a, **k):
            return self

        def all(self):
            return self.rows

        def first(self):
            return None

    class _DB:
        def query(self, model, *a, **k):
            return _Query(sites if getattr(model, "__name__", "") == "Website" else [])

    return _DB()


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "api.babatap.com"
    (root / "public_html").mkdir(parents=True)
    (root / "public_html" / "index.php").write_text("<?php", encoding="utf-8")
    (tmp_path / "out").mkdir()
    return root


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """Stand in for /etc/opanel/certs, the root:opanel mirror the panel reads
    because /etc/letsencrypt/live is root-only."""
    store = tmp_path / "certs"
    store.mkdir()
    monkeypatch.setattr(ssl_service, "PANEL_CERT_STORE", store)
    monkeypatch.setattr(ssl_service, "MANUAL_SSL_ROOT", tmp_path / "manual")
    return store


def _backup(tmp_path, monkeypatch, site, skipped=None):
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path / "out")
    archive = backup.create_user_backup(
        _User(), _db_with([site]), skipped=skipped if skipped is not None else []
    )
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        manifest = json.loads(tar.extractfile(backup.BACKUP_MANIFEST).read().decode("utf-8"))
    return archive, names, manifest


def test_a_lets_encrypt_cert_is_taken_from_the_readable_mirror(tmp_path, monkeypatch, tree, mirror):
    cert, key = make_cert()
    live = mirror / "api.babatap.com"
    live.mkdir()
    (live / "fullchain.pem").write_bytes(cert)
    (live / "privkey.pem").write_bytes(key)

    _, names, manifest = _backup(tmp_path, monkeypatch, _Site("api.babatap.com", tree))

    assert "ssl/api.babatap.com/fullchain.pem" in names
    assert "ssl/api.babatap.com/privkey.pem" in names
    assert manifest["websites"][0]["ssl"]["issued_as"] == "letsencrypt"


def test_the_private_key_is_stored_unreadable_to_others(tmp_path, monkeypatch, tree, mirror):
    cert, key = make_cert()
    live = mirror / "api.babatap.com"
    live.mkdir()
    (live / "fullchain.pem").write_bytes(cert)
    (live / "privkey.pem").write_bytes(key)

    archive, _, _ = _backup(tmp_path, monkeypatch, _Site("api.babatap.com", tree))

    with tarfile.open(archive) as tar:
        member = tar.getmember("ssl/api.babatap.com/privkey.pem")
        assert member.mode == 0o600
        assert tar.extractfile(member).read() == key


def test_a_manual_cert_is_taken_from_its_own_paths(tmp_path, monkeypatch, tree, mirror):
    cert, key = make_cert()
    own = tmp_path / "own"
    own.mkdir()
    (own / "cert.crt").write_bytes(cert)
    (own / "privkey.key").write_bytes(key)
    (own / "ca.crt").write_bytes(cert)
    site = _Site("api.babatap.com", tree, ssl_mode="manual",
                 ssl_cert_path=str(own / "cert.crt"), ssl_key_path=str(own / "privkey.key"),
                 ssl_ca_path=str(own / "ca.crt"))

    _, names, manifest = _backup(tmp_path, monkeypatch, site)

    assert "ssl/api.babatap.com/ca.pem" in names
    assert manifest["websites"][0]["ssl"]["issued_as"] == "manual"


def test_a_site_with_ssl_on_but_no_cert_on_disk_is_reported(tmp_path, monkeypatch, tree, mirror):
    skipped = []
    _, names, manifest = _backup(tmp_path, monkeypatch, _Site("api.babatap.com", tree), skipped)

    assert manifest["websites"][0]["ssl"] is None
    assert skipped[0]["path"] == "ssl:api.babatap.com"
    # The rest of the backup still happened.
    assert "sites/api.babatap.com/site/public_html/index.php" in names


def test_a_plain_http_site_carries_no_ssl_section(tmp_path, monkeypatch, tree, mirror):
    site = _Site("api.babatap.com", tree, ssl_enabled=False, ssl_mode="none")

    _, names, manifest = _backup(tmp_path, monkeypatch, site)

    assert manifest["websites"][0]["ssl"] is None
    assert not any(name.startswith("ssl/") for name in names)


def test_a_wildcard_parent_cert_travels_too(tmp_path, monkeypatch, tree, mirror):
    cert, key = make_cert("*.babatap.com")
    live = mirror / "api.babatap.com"
    live.mkdir()
    (live / "fullchain.pem").write_bytes(cert)
    (live / "privkey.pem").write_bytes(key)
    site = _Site("api.babatap.com", tree, ssl_wildcard=True)

    _, names, manifest = _backup(tmp_path, monkeypatch, site)

    assert manifest["websites"][0]["ssl"]["wildcard"] is True
    assert "ssl/api.babatap.com/fullchain.pem" in names


def test_installing_refuses_a_key_that_does_not_match_the_certificate(monkeypatch):
    cert, _ = make_cert()
    _, other_key = make_cert()
    monkeypatch.setattr(ssl_service, "_write_manual_ssl_files",
                        lambda *a, **k: {"cert": "c", "key": "k", "ca": None})

    with pytest.raises(Exception):
        ssl_service.install_site_certificate("api.babatap.com", cert, other_key)


def test_an_expired_certificate_is_still_installed(monkeypatch):
    """Refusing it would leave the site on plain HTTP with no way back; it
    still describes what the site was serving, and LE can take over after."""
    cert, key = make_cert(days=-1)
    written = {}
    monkeypatch.setattr(ssl_service, "_write_manual_ssl_files",
                        lambda domain, c, k, ca=b"": written.update(domain=domain) or
                        {"cert": "c", "key": "k", "ca": None})

    paths = ssl_service.install_site_certificate("api.babatap.com", cert, key)

    assert paths["cert"] == "c"
    assert written["domain"] == "api.babatap.com"


def test_restore_installs_the_cert_before_it_writes_the_vhost():
    """Order matters: a vhost rendered without the cert paths serves plain
    HTTP until something rewrites it."""
    import inspect

    source = inspect.getsource(backup.restore_user_backup)
    install_at = source.index("install_site_certificate")
    vhost_at = source.index("openlitespeed.rewrite_vhost")

    assert install_at < vhost_at
    assert "**ssl_kwargs," in source


def test_restore_marks_a_restored_cert_manual_not_lets_encrypt():
    """This box has no renewal config, so a vhost pointing into
    /etc/letsencrypt/live would point at nothing."""
    import inspect

    source = inspect.getsource(backup.restore_user_backup)

    assert 'website.ssl_mode = "manual"' in source
    assert "website.ssl_cert_path = paths.get(\"cert\")" in source


def test_restore_reports_what_it_restored_and_what_it_could_not():
    import inspect

    source = inspect.getsource(backup.restore_user_backup)

    assert '"certificates": restored_certificates' in source
    assert '"ssl_warnings": ssl_warnings' in source
    # A certificate that will not install must not abort the restore.
    assert "ssl_warnings.append" in source


def test_a_cert_member_that_is_absurdly_large_is_refused(tmp_path):
    archive = tmp_path / "a.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("ssl/x/privkey.pem")
        info.size = backup.MAX_SSL_MEMBER_BYTES + 1
        tar.addfile(info, backup.BytesIO(b"\0" * info.size))

    with pytest.raises(ValueError, match="Invalid certificate member"):
        backup._read_member_bytes(archive, "ssl/x/privkey.pem")


def test_a_missing_cert_member_reads_as_empty(tmp_path):
    archive = tmp_path / "a.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        backup._add_bytes(tar, "manifest.json", b"{}")

    assert backup._read_member_bytes(archive, "ssl/x/privkey.pem") == b""
    assert backup._read_member_bytes(archive, "") == b""
