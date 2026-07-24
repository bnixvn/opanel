from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services import nginx, ssl


def _cert_pair(domain="example.test", *, days=30, key=None, aliases=None):
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = datetime.now(timezone.utc)
    san_names = [x509.DNSName(domain)]
    for alias in aliases or []:
        san_names.append(x509.DNSName(alias))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def test_validate_manual_ssl_accepts_matching_cert_key_and_optional_ca():
    cert_pem, key_pem = _cert_pair()

    ssl.validate_manual_ssl("example.test", cert_pem, key_pem, cert_pem)


def test_validate_manual_ssl_accepts_alias_san():
    cert_pem, key_pem = _cert_pair("example.test", aliases=["www.example.test"])

    ssl.validate_manual_ssl("example.test", cert_pem, key_pem, aliases=["www.example.test"])


def test_validate_manual_ssl_rejects_mismatched_private_key():
    cert_pem, _key_pem = _cert_pair()
    _other_cert, other_key = _cert_pair("other.test")

    with pytest.raises(ValueError, match="private_key does not match"):
        ssl.validate_manual_ssl("example.test", cert_pem, other_key)


def test_validate_manual_ssl_rejects_wrong_domain():
    cert_pem, key_pem = _cert_pair("other.test")

    with pytest.raises(ValueError, match="CN/SAN"):
        ssl.validate_manual_ssl("example.test", cert_pem, key_pem)


def test_validate_manual_ssl_rejects_missing_alias_domain():
    cert_pem, key_pem = _cert_pair()

    with pytest.raises(ValueError, match="CN/SAN"):
        ssl.validate_manual_ssl("example.test", cert_pem, key_pem, aliases=["alias.example.test"])


def test_validate_manual_ssl_rejects_expired_certificate():
    cert_pem, key_pem = _cert_pair(days=-1)

    with pytest.raises(ValueError, match="expired"):
        ssl.validate_manual_ssl("example.test", cert_pem, key_pem)


def test_apply_manual_ssl_config_adds_https_server_and_ca():
    rendered = nginx.apply_manual_ssl_config(
        nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="wordpress",
        php_version="8.3",
        ),
        "/etc/nginx/opanel/ssl/sites/example.test/cert.crt",
        "/etc/nginx/opanel/ssl/sites/example.test/privkey.key",
        "/etc/nginx/opanel/ssl/sites/example.test/ca.crt",
    )

    assert "return 301 https://$host$request_uri;" in rendered
    assert "listen 443 ssl http2;" in rendered
    assert "ssl_certificate /etc/nginx/opanel/ssl/sites/example.test/fullchain.crt;" in rendered
    assert "ssl_certificate_key /etc/nginx/opanel/ssl/sites/example.test/privkey.key;" in rendered
    assert "ssl_trusted_certificate /etc/nginx/opanel/ssl/sites/example.test/ca.crt;" in rendered


def test_install_manual_ssl_uses_helper_without_logging_key(monkeypatch):
    cert_pem, key_pem = _cert_pair()
    captured = {}

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        captured["helper_command"] = helper_command
        captured["helper_args"] = helper_args
        captured["kwargs"] = kwargs
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ssl.shell, "privileged", fake_privileged)

    paths = ssl.install_manual_ssl("example.test", cert_pem, key_pem)

    assert paths == {
        "cert": "/usr/local/lsws/conf/opanel/ssl/sites/example.test/cert.crt",
        "key": "/usr/local/lsws/conf/opanel/ssl/sites/example.test/privkey.key",
        "ca": None,
    }
    assert captured["helper_command"] == "manual-ssl-install"
    assert captured["helper_args"] == ["example.test"]
    assert captured["kwargs"]["sensitive"] is True
    assert "PRIVATE KEY" in captured["kwargs"]["input"]


def test_issue_ssl_passes_aliases_and_email(monkeypatch):
    captured = {}

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        captured["helper_command"] = helper_command
        captured["helper_args"] = helper_args
        captured["kwargs"] = kwargs
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ssl.shell, "privileged", fake_privileged)
    monkeypatch.setattr(ssl.settings, "ssl_email", "admin@example.test")

    result = ssl.issue_ssl("example.test", aliases=["www.example.test"])

    assert result.returncode == 0
    assert captured["helper_command"] == "certbot-issue"
    assert captured["helper_args"] == ["example.test", "www.example.test", "admin@example.test"]


def test_issue_ssl_uses_opanel_acme_webroot(monkeypatch):
    captured = {}

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        captured["helper_command"] = helper_command
        captured["helper_args"] = helper_args
        captured["kwargs"] = kwargs
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ssl.shell, "privileged", fake_privileged)
    monkeypatch.setattr(ssl.settings, "ssl_email", "")

    result = ssl.issue_ssl("example.test")

    assert result.returncode == 0
    fallback = " ".join(captured["kwargs"]["fallback"])
    assert "/var/www/opanel-acme" in fallback
    assert "/var/www/opanel/acme" not in fallback
