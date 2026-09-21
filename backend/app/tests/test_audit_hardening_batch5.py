"""Principal, TOCTOU, quota and one corrected claim."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.services import cron, file_manager, site_users, wordpress

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# --------------------------------------------------------------------------
# nothing of a tenant's runs as www-data
# --------------------------------------------------------------------------
class _Site:
    def __init__(self, linux_user=None, root_path="/home/op_tenant_abc123/site.test", domain="site.test"):
        self.linux_user = linux_user
        self.root_path = root_path
        self.domain = domain
        self.php_version = "8.4"
        self.app_type = "wordpress"


def test_a_site_with_a_linux_user_uses_it():
    assert site_users.require_site_linux_user(_Site(linux_user="op_tenant_abc123")) == "op_tenant_abc123"


def test_a_legacy_row_is_resolved_from_its_root_path():
    """linux_user was added nullable with no backfill, so NULL is permanent."""
    assert site_users.require_site_linux_user(_Site(linux_user=None)) == "op_tenant_abc123"


def test_an_unresolvable_site_fails_instead_of_becoming_www_data():
    with pytest.raises(ValueError, match="no Linux user"):
        site_users.require_site_linux_user(_Site(linux_user=None, root_path="/srv/elsewhere/site.test"))


def test_cron_never_returns_the_shared_account():
    body = _code(inspect.getsource(cron.cron_user_for_website))
    assert '"www-data"' not in body, (
        "list_cron_all returned that whole crontab to the caller and add_cron "
        "rewrote it wholesale, so one tenant could read and replace another's jobs"
    )
    assert "require_site_linux_user" in body


def test_cron_helpers_take_a_mandatory_user():
    source = _code(Path(cron.__file__).read_text(encoding="utf-8"))
    assert 'cron_user: str = "www-data"' not in source


def test_wp_cli_refuses_to_run_without_a_site_user():
    for func in (wordpress.wp_update, wordpress.reset_admin_password):
        body = _code(inspect.getsource(func))
        assert "if not linux_user" in body, f"{func.__name__} still has a fallback"
        # The helper subcommand must always be wp-site. `fallback=["wp", ...]`
        # is a different thing: the local wp binary, used only when the helper
        # is not in play at all (dry run / dev), and it runs as whoever the
        # panel process is rather than as www-data.
        assert 'shell.privileged(\n        "wp-site"' in body or 'shell.privileged("wp-site"' in body, (
            f"{func.__name__} must name wp-site as the helper subcommand"
        )
        assert 'privileged("wp"' not in body and '"wp" if' not in body, (
            f"{func.__name__} can still reach the www-data helper case"
        )


def test_the_helper_wp_case_is_gone():
    assert 'deny "wp is no longer supported' in HELPER
    assert "runuser -u www-data -- env HOME=/var/www" not in HELPER


def test_helper_cron_cases_require_a_validated_user():
    for case in ("cron-list", "cron-write"):
        block = HELPER[HELPER.index(f"  {case})") : HELPER.index(f"  {case})") + 260]
        assert 'user="${1:-www-data}"' not in block
        assert 'require_linux_user "$1"' in block


# --------------------------------------------------------------------------
# TOCTOU
# --------------------------------------------------------------------------
def test_directory_hardening_walks_with_o_nofollow():
    block = HELPER[HELPER.index("harden_site_dir_path() {") : HELPER.index("ensure_panel_user_home() {")]
    assert "O_NOFOLLOW" in block, (
        "chown/chmod on a re-derived path name follow symlinks, and the site user "
        "owns those directories, so a component swapped after the resolve made "
        "root chown an arbitrary directory to that user"
    )
    assert "os.fchown" in block and "os.fchmod" in block
    assert "harden_site_dir " not in block, "must not fall back to the by-name walk"


def test_symlink_refusals_test_the_unresolved_path():
    """require_safe_path returns readlink -m output, so -L on it never fires."""
    for message in (
        "refusing to write through a symlink",
        "refusing to delete through a symlink",
    ):
        for index in range(len(HELPER)):
            index = HELPER.find(message, index)
            if index == -1:
                break
            line_start = HELPER.rfind("\n", 0, index) + 1
            line = HELPER[line_start : HELPER.find("\n", index)]
            assert '"$root_target/$rel_arg"' in line, (
                f"guard tests an already-dereferenced path and cannot fire: {line.strip()}"
            )
            index += 1


def test_option_driven_execution_is_refused():
    assert "TERMINAL_EXEC_OPTIONS" in HELPER
    for option in ("-exec", "--to-command", "-execdir", "--use-compress-program"):
        assert f" {option} " in HELPER or f"{option}\n" in HELPER, f"{option} not in the denylist"
    validator = HELPER[HELPER.index("require_terminal_path_args() {") :][:600]
    assert "require_terminal_safe_options" in validator, (
        "the hyphen skip means an option is never treated as a path, so "
        "find -exec and tar --to-command bypassed the containment check"
    )


def test_the_allowlist_no_longer_claims_to_be_the_boundary():
    block = HELPER[HELPER.index("# Allowed commands for terminal access.") :][:1200]
    assert "not the security boundary" in block
    assert "0750" in block, "the comment should name what the real boundary is"


# --------------------------------------------------------------------------
# quota
# --------------------------------------------------------------------------
def test_databases_are_metered_like_websites_and_bytes():
    from app.api import databases as databases_api

    body = _code(inspect.getsource(databases_api.create_database))
    assert "database_limit" in body, (
        "websites are capped by website_limit and bytes by storage_limit_mb; "
        "databases had no cap at all, and each create ends in FLUSH PRIVILEGES"
    )
    assert "Database limit reached" in body


def test_the_limit_exists_on_the_model_and_the_plan():
    from app.models.entities import HostingPlan, User

    assert hasattr(User, "database_limit")
    assert hasattr(HostingPlan, "database_limit")


def test_migration_0028_is_reversible_and_idempotent():
    path = PROJECT_ROOT / "backend" / "alembic" / "versions" / "0028_database_limit.py"
    source = path.read_text(encoding="utf-8")
    assert "def downgrade" in source
    assert "_has_column" in source, "re-running must not fail on an existing column"
    assert 'down_revision: Union[str, None] = "0027_s3_backup_targets"' in source


def test_tar_prepass_stops_at_the_callers_quota():
    body = _code(inspect.getsource(file_manager._tar_uncompressed_size))
    assert "quota_check" in body, (
        "walking a gzip stream decompresses every member, and that work was "
        "bounded only by the 100 GiB module constant while the caller's 1 GiB "
        "limit was not consulted until afterwards"
    )
    caller = _code(inspect.getsource(file_manager.extract_archive))
    assert "quota_check=quota_check" in caller


def test_the_zip_path_is_left_alone():
    """It reads central-directory metadata, so it never had the problem."""
    body = _code(inspect.getsource(file_manager._zip_uncompressed_size))
    assert "quota_check" not in body


# --------------------------------------------------------------------------
# the firewall claim
# --------------------------------------------------------------------------
def test_the_readme_does_not_promise_default_deny():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    firewall = readme[readme.index("## Firewall") :][:2200]
    assert "not a default-deny firewall" in firewall, (
        "INPUT policy is ACCEPT and no managed chain ends in DROP, but the "
        "page presented a closed-by-default posture"
    )
    assert "are always allowed" not in firewall
    assert "ufw-backup-" in firewall


def test_ufw_rules_are_backed_up_before_removal():
    install = (PROJECT_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    block = install[install.index("remove_ufw_legacy() {") :][:1600]
    assert "ufw-backup-" in block, "the operator's rules were deleted with no copy"
    assert "ufw was ACTIVE" in block, "a host that arrived firewalled must be told"
