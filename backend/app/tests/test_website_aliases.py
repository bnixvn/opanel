from app.api import websites
from app.models.entities import User, Website, WebsiteAlias
from app.schemas.schemas import WebsiteAliasCreate


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _Db:
    def __init__(self, website):
        self.website = website
        self.aliases = []
        self.added = []
        self.committed = False
        self.rolled_back = False

    def query(self, model):
        if model is Website:
            return _Query([self.website])
        if model is WebsiteAlias:
            return _Query(self.aliases)
        return _Query([])

    def add(self, item):
        self.added.append(item)
        if isinstance(item, WebsiteAlias):
            item.id = len(self.aliases) + 1
            item.website = self.website
            self.aliases.append(item)
            self.website.aliases = [*getattr(self.website, "aliases", []), item]

    def flush(self):
        pass

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, item):
        pass


def test_create_alias_expands_letsencrypt_certificate(monkeypatch):
    website = Website(
        id=1,
        domain="example.test",
        owner_id=1,
        root_path="/home/bp_example_test/example.test",
        linux_user=None,
        ssl_enabled=True,
        ssl_mode="letsencrypt",
    )
    website.aliases = [WebsiteAlias(domain="old.example.test", mode="redirect")]
    db = _Db(website)
    issued = []

    monkeypatch.setattr(websites, "_rewrite_website_vhost", lambda *args, **kwargs: "/etc/nginx/conf.d/example.test.conf")
    monkeypatch.setattr(websites.ssl, "issue_ssl", lambda domain, aliases: issued.append((domain, aliases)) or type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(websites, "log_action", lambda *args, **kwargs: None)

    alias = websites.create_website_alias(
        1,
        WebsiteAliasCreate(domain="Alias.Example.Test"),
        request=None,
        db=db,
        current_user=User(id=1, role="admin"),
    )

    assert alias.domain == "alias.example.test"
    assert db.committed is True
    assert db.rolled_back is False
    assert issued == [("example.test", ["alias.example.test", "old.example.test"])]
