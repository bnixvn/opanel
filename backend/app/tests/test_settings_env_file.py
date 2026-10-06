"""A .env line the panel does not know must not stop the panel.

The helper tells an operator to put its overrides in the same .env
(opanel_MARIADB_BUFFER_POOL_SIZE and the rest). pydantic-settings forbids
unknown keys by default, so on .122 (2026-10-06) one such line stopped the
minute tick from 11:39 on -- scheduled backups and notifications with it --
and an update then died at its migrations.
"""
from app.core.config import Settings


def test_an_override_for_the_helper_is_ignored(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("APP_ENV=development\nOPANEL_MARIADB_BUFFER_POOL_SIZE=2560M\nopanel_PHP_FPM_WORKER_MB=96\n",
                   encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=str(env))
    assert settings.app_env == "development"
    assert not hasattr(settings, "opanel_mariadb_buffer_pool_size")
