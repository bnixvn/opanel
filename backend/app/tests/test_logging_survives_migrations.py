"""Running the migrations must not switch the panel's own loggers off.

The panel runs Alembic at startup, after its modules have made their
loggers, and Alembic's env.py called fileConfig() with its default
disable_existing_loggers=True: every logger.warning and logger.exception in
the panel went nowhere - a failed restore's traceback included (2026-10-09).
"""
import logging

import app.main  # noqa: F401 - imports the services and runs the migrations, as startup does
from app.core.database import run_migrations


def test_running_the_migrations_leaves_the_panels_loggers_on():
    logger = logging.getLogger("opanel.git")
    run_migrations()
    assert not logger.disabled, "fileConfig must not disable the panel's loggers"
