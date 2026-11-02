"""The rename-domain endpoint (DirectAdmin-style change-a-domain).

The file move + vhost rewrite + record flip is the atomic-ish core; once it is
done the site serves under the new name and the rest (certificate, DNS, mail,
WordPress, cron, SFTP/Git) is best effort. These tests pin the core order and
that every follow-up primitive is called with the old and new names.
"""
from app.api import websites
from app.models.entities import GitRepository, SftpAccount, User, Website, WebsiteAlias
from app.schemas.schemas import WebsiteRename


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _Db:
    def __init__(self, website, git_repos=None, sftp=None):
        self.website = website
        self.git_repos = git_repos or []
        self.sftp = sftp or []
        self.committed = 0

    def query(self, model):
        if model is Website:
            return _Query([self.website])
        if model is GitRepository:
            return _Query(self.git_repos)
        if model is SftpAccount:
            return _Query(self.sftp)
        return _Query([])

    def commit(self):
        self.committed += 1

    def refresh(self, item):
        pass


def _patch_services(monkeypatch, calls, *, conflict=False):
    monkeypatch.setattr(websites, "_hostname_conflicts", lambda *a, **k: conflict)
    monkeypatch.setattr(websites, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(websites.site_users, "site_root_for_panel_user",
                        lambda username, domain: f"/home/{username}/{domain}")
    monkeypatch.setattr(websites.site_users, "move_site_runtime",
                        lambda old, new, user, php: calls.setdefault("move", []).append((old, new, user, php)) or new)
    monkeypatch.setattr(websites.waf, "sync_website_rules",
                        lambda website: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(websites, "_rewrite_website_vhost",
                        lambda website, **kw: calls.setdefault("vhost", []).append((website.domain, kw.get("root_path"))) or "/vhost.conf")
    monkeypatch.setattr(websites.openlitespeed, "remove_vhost",
                        lambda domain: calls.setdefault("remove_vhost", []).append(domain))
    monkeypatch.setattr(websites.ssl, "release_site_certificates",
                        lambda db, website: calls.setdefault("ssl_release", []).append(website.domain))
    monkeypatch.setattr(websites.dns_manager, "website_removed",
                        lambda db, hosts, owner_id: calls.setdefault("dns_removed", []).append(tuple(hosts)))
    monkeypatch.setattr(websites.dns_manager, "website_created",
                        lambda db, website, hosts=None: calls.setdefault("dns_created", []).append(tuple(hosts or [])))
    monkeypatch.setattr(websites.cron, "rewrite_website_domain",
                        lambda old, new, oldr, newr, user: calls.setdefault("cron", []).append((old, new, oldr, newr, user)))
    monkeypatch.setattr(websites.sftp_accounts, "repoint_under_root",
                        lambda db, website, oldr, newr: calls.setdefault("sftp", []).append((oldr, newr)))
    monkeypatch.setattr(websites.mail, "migrate_domain",
                        lambda db, old, new, owner_id=None: calls.setdefault("mail", []).append((old, new, owner_id)))
    monkeypatch.setattr(websites.wp_manager, "forget_website",
                        lambda db, website_id: calls.setdefault("wp_forget", []).append(website_id))
    monkeypatch.setattr(websites, "issue_letsencrypt",
                        lambda db, website, names: calls.setdefault("le", []).append(tuple(names)))


def _site(**overrides):
    website = Website(
        id=1,
        domain="old.test",
        owner_id=1,
        root_path="/home/alice/old.test",
        linux_user="alice",
        app_type="static",
        php_version="8.4",
        ssl_enabled=False,
        ssl_mode="none",
        status="active",
    )
    website.aliases = []
    website.owner = User(id=1, username="alice", role="user")
    for key, value in overrides.items():
        setattr(website, key, value)
    return website


def test_rename_moves_files_rewrites_vhost_and_flips_record(monkeypatch):
    website = _site()
    db = _Db(website)
    calls = {}
    _patch_services(monkeypatch, calls)

    result = websites.rename_website(
        1, WebsiteRename(new_domain="New.Test"), request=None, db=db,
        current_user=User(id=9, role="admin"),
    )

    assert result["ok"] is True
    assert result["domain"] == "new.test"
    assert website.domain == "new.test"
    assert website.root_path == "/home/alice/new.test"
    # Core order: files moved old -> new, vhost rewritten under the new root,
    # old server_name vhost dropped.
    assert calls["move"] == [("/home/alice/old.test", "/home/alice/new.test", "alice", None)]
    assert calls["vhost"][0] == ("new.test", "/home/alice/new.test")
    assert calls["remove_vhost"] == ["old.test"]
    # Follow-ups carry the old and new names.
    assert calls["dns_removed"] == [("old.test",)]
    assert calls["dns_created"] == [("new.test",)]
    assert calls["cron"] == [("old.test", "new.test", "/home/alice/old.test", "/home/alice/new.test", "alice")]
    assert calls["sftp"] == [("/home/alice/old.test", "/home/alice/new.test")]
    assert calls["mail"] == [("old.test", "new.test", 1)]
    assert db.committed >= 1


def test_rename_rejects_same_domain(monkeypatch):
    website = _site()
    calls = {}
    _patch_services(monkeypatch, calls)
    try:
        websites.rename_website(1, WebsiteRename(new_domain="old.test"), request=None,
                                db=_Db(website), current_user=User(id=9, role="admin"))
    except websites.HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("expected an HTTPException for an unchanged domain")


def test_rename_rejects_taken_domain(monkeypatch):
    website = _site()
    calls = {}
    _patch_services(monkeypatch, calls, conflict=True)
    try:
        websites.rename_website(1, WebsiteRename(new_domain="taken.test"), request=None,
                                db=_Db(website), current_user=User(id=9, role="admin"))
    except websites.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("expected an HTTPException for a taken domain")
    assert "move" not in calls  # nothing touched the filesystem


def test_rename_reissues_letsencrypt_for_the_new_name(monkeypatch):
    website = _site(ssl_enabled=True, ssl_mode="letsencrypt")
    db = _Db(website)
    calls = {}
    _patch_services(monkeypatch, calls)

    websites.rename_website(1, WebsiteRename(new_domain="new.test", reissue_ssl=True),
                            request=None, db=db, current_user=User(id=9, role="admin"))

    assert calls["ssl_release"] == ["new.test"]
    assert calls["le"] == [("new.test",)]


def test_rename_rewrites_git_repo_paths_under_the_old_root(monkeypatch):
    website = _site()
    repo_in = GitRepository(id=1, owner_id=1, name="app", path="/home/alice/old.test/app")
    repo_out = GitRepository(id=2, owner_id=1, name="other", path="/home/alice/scripts")
    db = _Db(website, git_repos=[repo_in, repo_out])
    calls = {}
    _patch_services(monkeypatch, calls)

    websites.rename_website(1, WebsiteRename(new_domain="new.test"), request=None, db=db,
                            current_user=User(id=9, role="admin"))

    assert repo_in.path == "/home/alice/new.test/app"
    assert repo_out.path == "/home/alice/scripts"  # outside the moved folder, untouched


def test_rename_rolls_back_when_the_vhost_fails(monkeypatch):
    website = _site()
    db = _Db(website)
    calls = {}
    _patch_services(monkeypatch, calls)

    def boom(wbsite, **kw):
        if wbsite.domain == "new.test" and kw.get("root_path"):
            raise RuntimeError("config broke")
        return "/vhost.conf"
    monkeypatch.setattr(websites, "_rewrite_website_vhost", boom)

    try:
        websites.rename_website(1, WebsiteRename(new_domain="new.test"), request=None, db=db,
                                current_user=User(id=9, role="admin"))
    except websites.HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("expected the rename to fail when the vhost cannot be written")
    # The record and the folder were put back.
    assert website.domain == "old.test"
    assert website.root_path == "/home/alice/old.test"
    assert calls["move"][-1] == ("/home/alice/new.test", "/home/alice/old.test", "alice", None)


def test_rename_runs_wordpress_search_replace_in_the_document_root(monkeypatch):
    # wp-cli needs the folder holding wp-config.php (public_html), not the site
    # root above it: on a real box the root gave "not a WordPress installation".
    from pathlib import Path

    website = _site(app_type="wordpress")
    db = _Db(website)
    calls = {}
    _patch_services(monkeypatch, calls)
    monkeypatch.setattr(Website, "wp_installed", property(lambda self: True))
    monkeypatch.setattr(websites.wordpress, "rename_site_url",
                        lambda path, user, old, new, https=True: calls.setdefault("wp", []).append((path, old, new)))

    websites.rename_website(1, WebsiteRename(new_domain="new.test"), request=None, db=db,
                            current_user=User(id=9, role="admin"))

    path, old, new = calls["wp"][0]
    assert (Path(path).parent.name, Path(path).name) == ("new.test", "public_html")
    assert (old, new) == ("old.test", "new.test")
    assert calls["wp_forget"] == [1]
