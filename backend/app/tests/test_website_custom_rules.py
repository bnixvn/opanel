"""A site's own rewrite rules and response headers, from an allowlist (as SNPanel's custom directives, 2026-10-17)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import websites as websites_api
from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import User, Website
from app.services import openlitespeed

GOOD = """# moved pages
RewriteCond %{HTTP_HOST} ^old\\.example$ [NC]
RewriteRule ^blog/(.*)$ https://shop.example/news/$1 [R=301,L]
RewriteRule ^private/ - [F]
X-Robots-Tag: noindex
Content-Security-Policy: default-src 'self'
"""


def test_the_allowlist_splits_rewrites_from_headers():
    rewrites, headers = openlitespeed.parse_custom_rules(GOOD)
    assert rewrites == ["RewriteCond %{HTTP_HOST} ^old\\.example$ [NC]",
                        "RewriteRule ^blog/(.*)$ https://shop.example/news/$1 [R=301,L]",
                        "RewriteRule ^private/ - [F]"]
    assert headers == ["X-Robots-Tag: noindex", "Content-Security-Policy: default-src 'self'"]


@pytest.mark.parametrize("line", [
    "RewriteRule ^(.*)$ http://10.0.0.1/$1 [P]",            # proxy
    "RewriteRule .* - [H=lsapi:other]",                    # another handler
    "RewriteRule .* - [E=Cache-Control:no-cache]",         # environment
    "RewriteRule .* - [T=application/x-httpd-php]",        # type
    "RewriteRule a b\nEND_RULES\n}",                       # leaving the heredoc
    "}\ncontext /etc/ {",                                  # leaving the block
    "Content-Length: 0",                                   # LiteSpeed's to set
    "php_admin_value open_basedir none",                   # anything else
    "extprocessor evil {",
])
def test_anything_else_is_refused(line):
    with pytest.raises(ValueError):
        openlitespeed.parse_custom_rules(line)


def test_too_many_rules_are_refused():
    with pytest.raises(ValueError):
        openlitespeed.parse_custom_rules("\n".join(f"X-N{n}: 1" for n in range(101)))


def _render(app_type, rules, **kw):
    return openlitespeed.render_vhost("shop.example", "/home/acme/shop.example", app_type=app_type,
                                      php_version=None if app_type == "static" else "8.3", linux_user="acme",
                                      custom_rules=rules, **kw)


@pytest.mark.parametrize("app_type", ["wordpress", "php", "static"])
def test_rewrites_follow_the_panels_redirects_and_headers_join_its_own(app_type):
    text = _render(app_type, GOOD, ssl_enabled=True, ssl_cert_path="/c.pem", ssl_key_path="/k.pem")
    https = text.index("RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]")
    custom = text.index("RewriteRule ^blog/(.*)$ https://shop.example/news/$1 [R=301,L]")
    assert https < custom
    headers = text.split("extraHeaders <<<END_HEADERS", 1)[1].split("END_HEADERS", 1)[0]
    assert "X-Robots-Tag: noindex" in headers and "X-Content-Type-Options: nosniff" in headers
    # The site's Content-Security-Policy replaces WordPress's, never sits beside it.
    assert headers.count("Content-Security-Policy:") == 1 and "default-src 'self'" in headers


def test_without_rules_the_vhost_is_as_before():
    text = _render("wordpress", "")
    headers = text.split("extraHeaders <<<END_HEADERS", 1)[1].split("END_HEADERS", 1)[0]
    assert "Content-Security-Policy:" in headers and "X-Content-Type-Options: nosniff" in headers


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    owner = User(username="acme", email="acme@example.test", role="end_user", is_active=True,
                 hashed_password=hash_password("PasswordLongEnough1"))
    db.add(owner)
    db.commit()
    site = Website(domain="shop.example", owner_id=owner.id, root_path="/home/acme/shop.example", app_type="wordpress",
                   php_version="8.3", linux_user="acme", status="active", custom_rules="X-Old: 1")
    db.add(site)
    db.commit()
    rendered = []
    state = {"fail": False}

    def rewrite(website, **kw):
        rendered.append(kw.get("custom_rules"))
        if state["fail"] and kw.get("custom_rules") != "X-Old: 1":
            raise RuntimeError("LiteSpeed refused it")
        return ""

    monkeypatch.setattr(websites_api, "_rewrite_website_vhost", rewrite)

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        yield SimpleNamespace(db=db, client=TestClient(app), site=site, rendered=rendered, state=state)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_the_owner_saves_rules(env):
    answer = env.client.put(f"/api/websites/{env.site.id}/custom-rules", json={"custom_rules": GOOD})
    assert answer.status_code == 200, answer.text
    assert answer.json()["custom_rules"] == GOOD.strip()
    assert env.client.get(f"/api/websites/{env.site.id}/custom-rules").json()["custom_rules"] == GOOD.strip()


def test_a_refused_line_changes_nothing(env):
    answer = env.client.put(f"/api/websites/{env.site.id}/custom-rules", json={"custom_rules": "RewriteRule .* - [P]"})
    assert answer.status_code == 400 and "[P]" in answer.json()["detail"]
    assert env.rendered == []


def test_a_vhost_litespeed_refuses_goes_back_to_the_old_rules(env):
    env.state["fail"] = True
    answer = env.client.put(f"/api/websites/{env.site.id}/custom-rules", json={"custom_rules": "X-New: 2"})
    assert answer.status_code == 400
    assert env.rendered == ["X-New: 2", "X-Old: 1"]
    env.db.expire_all()
    assert env.db.get(Website, env.site.id).custom_rules == "X-Old: 1"
