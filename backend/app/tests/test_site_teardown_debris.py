"""Deleting a site or an account must not leave its certificate, logs or
lsphp sockets behind.

Seen after deleting 39 imported accounts (54 domains, 40 Linux users): 38
certificate directories under ssl/sites, 162 per-domain log entries under
/var/log/openlitespeed and 82 stale sockets in /tmp/lshttpd survived.
"""
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import users as users_api
from app.api import websites as websites_api
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import User, Website
from app.services import provisioning, ssl

HELPER = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _case(name: str) -> str:
    start = HELPER.index(f"  {name})")
    return HELPER[start: HELPER.index(";;", start)]


def _function(name: str) -> str:
    start = HELPER.index(f"{name}() {{")
    return HELPER[start: HELPER.index("\n}\n", start)]


# ---------------------------------------------------------------------------
# Helper: logs and sockets
# ---------------------------------------------------------------------------

def test_vhost_delete_takes_the_domains_logs_with_it():
    body = _case("ols-vhost-delete")
    assert '"/var/log/openlitespeed/${safe_domain}.access.log"' in body
    assert '"/var/log/openlitespeed/${safe_domain}.error.log"' in body
    assert 'rm -rf "/var/log/openlitespeed/${safe_domain}"' in body


def test_site_pool_removal_takes_its_sockets():
    assert "rm -f /tmp/lshttpd/$glob" in _function("delete_site_php_pools")


def test_user_removal_matches_its_own_sockets_only():
    body = _function("delete_panel_user_runtime")
    pattern = re.search(r"=~ (\^opanel-\$\{user\}-\S+\$) \]\]", body).group(1)
    regex = re.compile(pattern.replace("${user}", "baba"))
    assert regex.match("opanel-baba-516cb66d595c-lsphp84.sock")
    assert regex.match("opanel-baba-516cb66d595c-lsphp84.sock.pid")
    # another user whose name merely starts the same way
    assert not regex.match("opanel-baba-x-516cb66d595c-lsphp84.sock")
    assert not regex.match("opanel-baba-516cb66d595c-lsphp84.sock.bak")


# ---------------------------------------------------------------------------
# Certificates, and reuse
# ---------------------------------------------------------------------------

@pytest.fixture
def sites(monkeypatch):
    removed = []
    monkeypatch.setattr(ssl, "remove_manual_ssl", lambda d: removed.append(("manual", d)))
    monkeypatch.setattr(ssl, "remove_wildcard_ssl", lambda d: removed.append(("letsencrypt", d)))
    db = SessionLocal()
    owner = User(username="certowner", email="c@example.test",
                 hashed_password=hash_password("PasswordLongEnough1"), role="end_user")
    db.add(owner)
    db.flush()

    def site(domain, **kwargs):
        w = Website(domain=domain, owner_id=owner.id, root_path=f"/home/certowner/{domain}",
                    document_root="public_html", linux_user="certowner", php_version="8.3",
                    app_type="wordpress", **kwargs)
        db.add(w)
        db.flush()
        return w

    try:
        yield db, site, removed
    finally:
        db.rollback()
        db.query(Website).filter(Website.owner_id == owner.id).delete()
        db.query(User).filter(User.id == owner.id).delete()
        db.commit()
        db.close()


def test_a_sites_own_manual_certificate_goes_with_it(sites):
    db, site, removed = sites
    parent = site("teardown.test", ssl_mode="manual")

    assert ssl.release_site_certificates(db, parent) == ["manual:teardown.test"]
    assert removed == [("manual", "teardown.test")]


def test_a_certificate_another_site_still_reuses_is_kept(sites):
    db, site, removed = sites
    parent = site("teardown.test", ssl_mode="letsencrypt", ssl_wildcard=True)
    site("blog.teardown.test", ssl_mode="reuse", ssl_reuse_name="letsencrypt:teardown.test")

    ssl.release_site_certificates(db, parent)

    assert ("letsencrypt", "teardown.test") not in removed


def test_sites_deleted_together_release_a_shared_wildcard(sites):
    db, site, removed = sites
    parent = site("teardown.test", ssl_mode="letsencrypt", ssl_wildcard=True)
    child = site("blog.teardown.test", ssl_mode="reuse", ssl_reuse_name="letsencrypt:teardown.test")

    ssl.release_site_certificates(db, parent, also_deleting=[parent.id, child.id])

    assert ("letsencrypt", "teardown.test") in removed


def test_a_stale_reuse_name_does_not_pin_a_certificate(sites):
    """Only a site actually in reuse mode serves the certificate."""
    db, site, removed = sites
    parent = site("teardown.test", ssl_mode="manual")
    site("old.teardown.test", ssl_mode="letsencrypt", ssl_reuse_name="manual:teardown.test")

    ssl.release_site_certificates(db, parent)

    assert ("manual", "teardown.test") in removed


def test_a_failed_removal_does_not_block_the_deletion(sites, monkeypatch):
    db, site, removed = sites
    parent = site("teardown.test", ssl_mode="manual")

    def boom(domain):
        raise RuntimeError("helper unavailable")

    monkeypatch.setattr(ssl, "remove_manual_ssl", boom)
    assert ssl.release_site_certificates(db, parent) == []


# ---------------------------------------------------------------------------
# Every delete path releases certificates
# ---------------------------------------------------------------------------

def test_every_delete_path_releases_certificates():
    assert "ssl.release_site_certificates(db, website)" in inspect.getsource(websites_api.delete_website)
    assert "ssl.release_site_certificates(db, website, also_deleting)" in inspect.getsource(
        users_api._delete_owned_website)
    assert "also_deleting=[w.id for w in websites]" in inspect.getsource(users_api.delete_user)
    assert "ssl.release_site_certificates(db, website, also_deleting=" in inspect.getsource(
        provisioning.terminate_account)


def test_deleting_a_website_drops_every_database_it_carries():
    source = inspect.getsource(websites_api.delete_website)
    assert ".all()" in source
    assert "for db_item in db_items:\n            mariadb.drop_database" in source


def test_deleting_an_account_clears_its_sites_aliases():
    assert "WebsiteAlias.website_id == website.id" in inspect.getsource(users_api._delete_owned_website)
