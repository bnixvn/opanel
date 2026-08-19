import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import User
from app.services import da_import


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _add(db, username, email):
    db.add(User(username=username, email=email, hashed_password="x", role="end_user"))
    db.flush()


def test_free_email_is_used_as_is(db):
    email, warning = da_import._available_email(db, "babatap", "owner@example.com")
    assert email == "owner@example.com"
    assert warning is None


def test_missing_email_falls_back_to_import_local(db):
    email, warning = da_import._available_email(db, "babatap", "")
    assert email == "babatap@import.local"
    assert warning is None


def test_email_owned_by_another_user_does_not_collide(db):
    """Regression: DirectAdmin accounts often share one contact mailbox. The
    import de-duplicated on username only, so the second account hit
    UNIQUE constraint failed: users.email and aborted the whole run."""
    _add(db, "duchonotes", "duchonotes.com@gmail.com")

    email, warning = da_import._available_email(db, "babatap", "duchonotes.com@gmail.com")

    assert email == "babatap@import.local"
    assert warning is not None
    assert "duchonotes.com@gmail.com" in warning
    assert "babatap" in warning
    # and the address it picked must actually be insertable
    _add(db, "babatap", email)


def test_fallback_keeps_going_when_import_local_is_taken_too(db):
    _add(db, "other", "shared@example.com")
    _add(db, "stale", "babatap@import.local")

    email, warning = da_import._available_email(db, "babatap", "shared@example.com")

    assert email == "babatap+2@import.local"
    assert warning is not None
    _add(db, "babatap", email)


def test_without_the_fallback_the_insert_really_does_fail(db):
    """Proves the constraint being worked around is real, not assumed."""
    _add(db, "duchonotes", "duchonotes.com@gmail.com")
    with pytest.raises(IntegrityError):
        _add(db, "babatap", "duchonotes.com@gmail.com")
