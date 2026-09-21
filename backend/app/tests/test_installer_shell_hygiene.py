"""Shell mistakes `bash -n` cannot see.

A literal `\\n` where a line continuation was meant is valid syntax, so the
syntax gate passes, and the success path short-circuits so nobody notices --
until the failure branch runs `n`, exits 127, and `set -e` kills the script.
That happened to the wp-cli health check in update.sh and install.sh, aborting
an update after the new root helper was installed and before migrations ran,
which is verbatim the regression two earlier commits were written to prevent.

Two independent reviewers found it by reading `cat -A`; nothing in the suite
could. These tests are cheap and cover the whole class.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = PROJECT_ROOT / "installer"
SCRIPTS = [
    INSTALLER / "install.sh",
    INSTALLER / "update.sh",
    INSTALLER / "files" / "opanel-helper.sh",
]

# Places a backslash-n is legitimate on a shell line: it is data handed to a
# program that interprets escapes, or part of a regex.
LEGITIMATE = re.compile(
    r"""printf|sed|awk|\btr\b|grep\s+-P|perl|\$'|IFS=|read -r -d"""
)
HEREDOC_START = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def _shell_lines(script: Path):
    """Yield (lineno, line) for shell code only, skipping heredoc bodies.

    These scripts embed python3 and awk programs in heredocs, and those
    legitimately contain \\n inside string literals. The invariant here is about
    shell syntax, so the bodies have to be excluded or the check is all noise.
    """
    terminator = None
    for lineno, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        match = HEREDOC_START.search(line)
        if match:
            terminator = match.group(1)
            yield lineno, line
            continue
        yield lineno, line


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_literal_backslash_n_pretending_to_continue_a_line(script: Path):
    """`|| \\n    cmd` runs `n`. `|| \\` + newline is the continuation."""
    offenders = []
    for lineno, line in _shell_lines(script):
        if "\\n" not in line or LEGITIMATE.search(line):
            continue
        offenders.append(f"{script.name}:{lineno}: {line.strip()[:120]}")
    assert not offenders, (
        "literal \\n in shell code outside a string-escaping context -- almost "
        "certainly a line continuation that lost its newline:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_or_else_branch_is_on_its_own_line_or_continued(script: Path):
    """`cmd || \\` must be followed by a line that starts a command."""
    lines = script.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        stripped = line.rstrip()
        if not stripped.endswith("||") and not stripped.endswith("|| \\"):
            continue
        assert index + 1 < len(lines), f"{script.name}:{index + 1}: dangling ||"
        following = lines[index + 1].strip()
        assert following and not following.startswith("#"), (
            f"{script.name}:{index + 2}: || continues into nothing"
        )


def test_the_wp_cli_health_check_is_a_real_continuation():
    """Pin the specific line that broke, in both scripts."""
    for name in ("install.sh", "update.sh"):
        lines = (INSTALLER / name).read_text(encoding="utf-8").splitlines()
        index = next(i for i, l in enumerate(lines) if "opanel-helper wp-info" in l)
        line = lines[index].rstrip()
        assert line.endswith("\\"), (
            f"{name}:{index + 1}: the health check's || branch must be a real "
            f"line continuation, got: {line[-40:]!r}"
        )
        assert "echo" in lines[index + 1], (
            f"{name}:{index + 2}: expected the warning echo on the next line"
        )
