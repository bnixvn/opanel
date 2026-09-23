"""The import's rewrite of a site's database config.

Reported from a DirectAdmin migration: every imported site came back with
"App config not updated for <domain>: [Errno 13] Permission denied:
'/home/<user>/<domain>/public_html/wp-config.php'". The copied tree belongs to
the site user, so the opanel account could not write it -- and the write was
attempted even when the file already said exactly what it would have written.
"""
from pathlib import Path

from app.services import da_import


def _wp_config(password: str, db: str = "admin_shop", user: str = "admin_shop") -> str:
    return (
        "<?php\n"
        f"define('DB_NAME', '{db}');\n"
        f"define('DB_USER', '{user}');\n"
        f"define('DB_PASSWORD', '{password}');\n"
        "define('DB_HOST', 'localhost');\n"
    )


def _recorder():
    calls = []

    def write(path: Path, text: str) -> None:
        calls.append((path, text))

    return calls, write


def test_a_config_that_already_matches_is_not_written(tmp_path):
    public = tmp_path / "public_html"
    public.mkdir()
    (public / "wp-config.php").write_text(_wp_config("same-pass"), encoding="utf-8")
    calls, write = _recorder()

    changed = da_import._update_app_db_config(public, "admin_shop", "admin_shop", "same-pass", write=write)

    assert changed == []
    assert calls == []


def test_a_config_that_differs_is_written_through_the_given_writer(tmp_path):
    public = tmp_path / "public_html"
    public.mkdir()
    (public / "wp-config.php").write_text(_wp_config("old-pass"), encoding="utf-8")
    calls, write = _recorder()

    changed = da_import._update_app_db_config(public, "admin_shop", "admin_shop", "new-pass", write=write)

    assert changed == [public / "wp-config.php"]
    assert len(calls) == 1 and "'new-pass'" in calls[0][1]
    # the writer is the only thing that touches the file
    assert "'old-pass'" in (public / "wp-config.php").read_text(encoding="utf-8")


def test_an_env_that_already_matches_is_not_written(tmp_path):
    public = tmp_path / "public_html"
    public.mkdir()
    (public / ".env").write_text(
        "APP_NAME=shop\nDB_HOST=127.0.0.1\nDB_DATABASE=admin_shop\nDB_USERNAME=admin_shop\nDB_PASSWORD=same\n",
        encoding="utf-8",
    )
    calls, write = _recorder()

    assert da_import._update_app_db_config(public, "admin_shop", "admin_shop", "same", write=write) == []
    assert calls == []


def test_the_site_writer_goes_through_the_helper_as_the_site_user(tmp_path, monkeypatch):
    root = tmp_path / "home" / "shopuser" / "shop.test"
    public = root / "public_html"
    public.mkdir(parents=True)
    seen = {}

    def privileged(command, helper_args=None, input=None, fallback=None, **kwargs):
        seen.update(command=command, args=helper_args, input=input)

    monkeypatch.setattr(da_import.shell, "privileged", privileged)

    da_import._site_config_writer(str(root), "shopuser")(public.resolve() / "wp-config.php", "<?php // new")

    assert seen == {
        "command": "site-file-write",
        "args": ["shopuser", str(root.resolve()), "public_html/wp-config.php"],
        "input": "<?php // new",
    }
