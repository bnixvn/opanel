import pytest
from pydantic import ValidationError

from app.schemas.schemas import WebsiteAliasCreate


def test_website_alias_create_accepts_redirect_mode():
    payload = WebsiteAliasCreate(domain="Alias.Example.Test", mode="redirect")

    assert payload.domain == "alias.example.test"
    assert payload.mode == "redirect"


def test_website_alias_create_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        WebsiteAliasCreate(domain="alias.example.test", mode="mirror")
