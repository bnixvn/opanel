"""What SNPanel had and OPanel did not (sn-gap.md, 2026-10-17), as it is added."""
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
APP = (SRC / "App.jsx").read_text(encoding="utf-8")
NAV = (SRC / "lib" / "navigation.jsx").read_text(encoding="utf-8")


def test_the_activity_log_is_a_tab_of_the_users_hub_for_administrators():
    hub = NAV[NAV.index("{ key: 'users',"):]
    hub = hub[:hub.index("] },")]
    assert "roles: ['admin']" in hub and "{ page: 'auditLog', label: msg('Activity log') }" in hub
    # A bookmarked address does not paint a page whose request 403s.
    assert "if (page === 'auditLog') return isAdmin ? <AuditLogPage/> : <DashboardPage/>;" in APP
    page = (SRC / "pages" / "AuditLog.jsx").read_text(encoding="utf-8")
    assert "/users/audit/log?" in page and "limit: String(PAGE + 1)" in page
