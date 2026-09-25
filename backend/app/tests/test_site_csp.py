"""The WordPress site CSP lets optimisation plugins' data: scripts run."""
from pathlib import Path

from app.services import nginx, openlitespeed

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "nginx" / "wordpress.conf.j2"


def _script_src(policy: str) -> list[str]:
    for directive in policy.split(";"):
        parts = directive.split()
        if parts and parts[0] == "script-src":
            return parts[1:]
    raise AssertionError("no script-src")


def test_script_src_allows_data_and_blob_scripts():
    for policy in (openlitespeed.WORDPRESS_CSP, nginx.WORDPRESS_CSP):
        sources = _script_src(policy)
        assert "data:" in sources and "blob:" in sources


def test_the_rest_of_the_policy_is_unchanged():
    for policy in (openlitespeed.WORDPRESS_CSP, nginx.WORDPRESS_CSP):
        assert "object-src 'none'" in policy
        assert "base-uri 'self'" in policy


def test_the_nginx_template_matches_the_service():
    assert nginx.WORDPRESS_CSP in TEMPLATE.read_text(encoding="utf-8")
