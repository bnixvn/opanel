"""The service inventory is not a hosting customer's business.

/services/list names every daemon on the box and its unit state, and
/services/action starts and stops them. Both accepted end_user. The dashboard's
CPU/RAM/disk cards read system-info and resource-usage, which stay open --
those are the numbers a customer is shown about the machine they are on.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from app.api import services as services_api

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP_JSX = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")


def _role_in(func) -> str:
    match = re.search(r"ensure_role\([^,]+,\s*Role\.(\w+)\)", inspect.getsource(func))
    return match.group(1) if match else ""


def test_listing_services_requires_admin():
    assert _role_in(services_api.get_services) == "admin"


def test_service_actions_require_admin_including_status():
    """"status" was the read half of the page end users no longer have."""
    source = inspect.getsource(services_api.run_service_action)
    assert _role_in(services_api.run_service_action) == "admin"
    assert "minimum_role" not in source, (
        "a status-only exemption would let an end user enumerate services anyway"
    )


def test_the_dashboard_numbers_stay_open_to_end_users():
    for func in (services_api.get_system_info, services_api.get_resource_usage):
        assert _role_in(func) == "end_user", (
            f"{func.__name__} feeds the dashboard cards every account sees"
        )


def test_the_page_is_hidden_from_end_users():
    fragment = "['services', tr(\"Services\"), Activity]"
    index = APP_JSX.index(fragment)
    window = APP_JSX[max(0, index - 120):index]
    assert "isAdmin ?" in window, f"not gated on isAdmin: {fragment}"


def test_the_dashboard_services_card_is_admin_only():
    # The card sits in the dashboard's `if (isAdmin) { ... } else { ... }`
    # branch: the nearest branch opening before it must be the admin one.
    index = APP_JSX.index("cards.push({ key: 'services'")
    before = APP_JSX[:index]
    assert before.rfind("if (isAdmin) {") > before.rfind("} else {"), "services card is not inside the isAdmin branch"


def test_a_bookmarked_services_url_does_not_render_the_page():
    index = APP_JSX.index("if (page === 'services')")
    line = APP_JSX[index:APP_JSX.index("\n", index)]
    assert "isAdmin" in line, (
        "a deep link would paint a page whose every request 403s"
    )
