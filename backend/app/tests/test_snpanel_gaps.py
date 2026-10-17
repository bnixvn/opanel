"""What SNPanel had and OPanel did not (sn-gap.md, 2026-10-17), as it is added."""
import inspect
from pathlib import Path

from app.api import websites as websites_api

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
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


def _case(name: str) -> str:
    start = HELPER.index(f"  {name})")
    return HELPER[start: HELPER.index(";;", start)]


def test_a_website_deleted_with_its_files_kept_loses_only_its_php_pools():
    body = _case("site-runtime-detach")
    assert 'delete_site_php_pools "$user" "$target"' in body
    assert "rm -rf" not in body
    assert 'target=$(require_managed_path "$path" "$user")' in body


def test_kept_databases_stay_with_the_account_on_no_website():
    source = inspect.getsource(websites_api.delete_website)
    assert "site_users.detach_site_runtime(website.root_path, website.linux_user)" in source
    assert "if delete_database:\n            db.delete(db_item)\n        else:" in source
    assert "db_item.website_id = None" in source


def test_the_delete_dialog_asks_which_to_keep():
    fn = APP[APP.index("  async function deleteWebsite(id) {"):]
    fn = fn[:fn.index("\n  }\n")]
    assert "name: 'files'" in fn and "name: 'databases'" in fn and "typeToConfirm: site?.domain" in fn
    assert "delete_files=${!!choice.files}&delete_database=${!!choice.databases}" in fn
