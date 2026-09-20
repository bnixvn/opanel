"""Every helper subcommand the backend calls must exist in the helper.

This is the check that found the suspend bug. provisioning.suspend_account had
been calling ``panel-user-lock`` and ``ols-vhost-suspend`` since it was written,
and neither had a case label: the calls reached the dispatch's default arm,
which exits 1, and because both call sites passed ``check=False`` the failure
was discarded and the provisioning job was still recorded "completed". A
suspended account kept its SFTP access, its websites, its crontab and its
MariaDB grants, and nothing anywhere reported a problem.

``openlitespeed.test_config`` had the same defect for ``ols-config-test``, so
the OpenLiteSpeed configuration test silently passed for every input.

A name-level diff is enough to catch the whole class, and it costs nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh"
BACKEND_APP = PROJECT_ROOT / "backend" / "app"

# Subcommands the helper dispatches that no Python caller names. These are
# reached by the installer, the updater or an operator shell, so an unused
# label is not a defect.
KNOWN_UNCALLED_FROM_PYTHON: frozenset[str] = frozenset()


def _helper_case_labels() -> set[str]:
    """Every label in the helper's dispatch case, alternations expanded.

    Labels look like ``  ols-reload|nginx-reload)`` at two-space indentation
    inside the single ``case "$cmd" in`` block.
    """
    text = HELPER.read_text(encoding="utf-8")
    labels: set[str] = set()
    for match in re.finditer(r"(?m)^  ([a-z0-9][a-z0-9|-]*)\)", text):
        for alternative in match.group(1).split("|"):
            if alternative:
                labels.add(alternative)
    return labels


def _privileged_subcommands() -> dict[str, set[str]]:
    """Map subcommand -> set of "file:line" that calls it.

    Matches the first string argument of ``shell.privileged(...)``, which is
    always the helper subcommand. Only literal names can be checked; a computed
    name is reported separately by ``test_no_computed_subcommand_names``.
    """
    calls: dict[str, set[str]] = {}
    pattern = re.compile(r"""shell\.privileged\(\s*["']([a-z0-9][a-z0-9-]*)["']""")
    for path in sorted(BACKEND_APP.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for name in pattern.findall(line):
                where = f"{path.relative_to(PROJECT_ROOT).as_posix()}:{lineno}"
                calls.setdefault(name, set()).add(where)
    # Multi-line calls: re-scan whole-file text for names the line scan missed.
    for path in sorted(BACKEND_APP.rglob("*.py")):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"""shell\.privileged\(\s*\n?\s*["']([a-z0-9][a-z0-9-]*)["']""", text
        ):
            name = match.group(1)
            lineno = text.count("\n", 0, match.start()) + 1
            where = f"{path.relative_to(PROJECT_ROOT).as_posix()}:{lineno}"
            calls.setdefault(name, set()).add(where)
    return calls


def test_every_called_subcommand_exists_in_the_helper() -> None:
    labels = _helper_case_labels()
    called = _privileged_subcommands()
    assert called, "found no shell.privileged calls -- the scanner is broken"

    missing = {name: sorted(where) for name, where in called.items() if name not in labels}
    assert not missing, (
        "These helper subcommands are called but have no case label in "
        "installer/files/opanel-helper.sh, so they hit the default arm's "
        "'unknown command' and exit 1:\n"
        + "\n".join(f"  {name}  <- {', '.join(where)}" for name, where in sorted(missing.items()))
    )


def test_suspend_path_subcommands_are_implemented() -> None:
    """Pin the four that were missing, so a rename cannot quietly drop them."""
    labels = _helper_case_labels()
    for name in (
        "panel-user-lock",
        "panel-user-unlock",
        "ols-vhost-suspend",
        "ols-vhost-restore",
        "ols-config-test",
    ):
        assert name in labels, f"{name} must be implemented in the helper"


def test_suspend_does_not_discard_helper_failures() -> None:
    """A suspension that cannot lock the account must not report success."""
    source = (BACKEND_APP / "services" / "provisioning.py").read_text(encoding="utf-8")
    for call in ("panel-user-lock", "panel-user-unlock"):
        index = source.index(call)
        window = source[index : index + 200]
        assert "check=False" not in window, (
            f"{call} must not pass check=False -- a failed lock has to fail the "
            "provisioning job, not be recorded as completed"
        )


def test_suspend_covers_every_website_the_account_owns() -> None:
    """Suspending only primary_website_id left the other sites serving."""
    source = (BACKEND_APP / "services" / "provisioning.py").read_text(encoding="utf-8")
    suspend = source[source.index("def suspend_account") : source.index("def unsuspend_account")]
    assert "Website.owner_id == account.user_id" in suspend, (
        "suspend_account must iterate every website the account owns"
    )
    assert "primary_website_id" not in suspend, (
        "suspend_account should no longer key the vhost teardown on "
        "primary_website_id alone"
    )


def test_no_computed_subcommand_names() -> None:
    """The name-level check above only works if every name is a literal.

    ``rewrite_vhost`` picks between two literals with a conditional expression
    assigned to a variable, which the scanner cannot see; both literals are
    asserted here instead.
    """
    labels = _helper_case_labels()
    for name in ("ols-vhost-write", "ols-vhost-write-defer"):
        assert name in labels, f"{name} must be implemented in the helper"
