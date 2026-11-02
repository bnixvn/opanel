"""`opanel addon ...`: addons from the server's shell.

Email and Resource limits are not offered on the Addons page any more
(operator, 2026-11-02); this is how they are installed. It runs the panel's
own install path in the foreground, so the outcome is the same as an install
from the page, and it works for every other addon too.
"""
from __future__ import annotations

from app.services import addons

USAGE = (
    "Usage: opanel addon list\n"
    "       opanel addon install <id>\n"
    "       opanel addon remove <id>"
)


def _line(entry: dict) -> str:
    state = "installed" if entry["installed"] else "not installed"
    if entry["installed"] and not entry.get("running", True):
        state += ", stopped"
    if entry["busy"]:
        state = f"{entry['busy_action'] or 'working'}..."
    shell_only = "  (shell only)" if entry.get("cli_only") else ""
    return f"{entry['id']:<18} {entry['name']:<28} {state}{shell_only}"


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0 if argv else 2
    action, args = argv[0], argv[1:]
    if action == "list" and not args:
        for addon_id in sorted(addons.registry()):
            print(_line(addons.status(addon_id)))
        return 0
    if action not in {"install", "remove"} or len(args) != 1:
        print(USAGE)
        return 2
    addon_id = args[0]
    if not addons.is_known(addon_id):
        print(f"Unknown addon: {addon_id}. See: opanel addon list")
        return 2
    name = addons.registry()[addon_id]["name"]
    print(f"{'Installing' if action == 'install' else 'Removing'} {name}; this can take a few minutes...", flush=True)
    try:
        if action == "install":
            result = addons.install(addon_id, actor="shell", from_shell=True)
        else:
            result = addons.uninstall(addon_id, actor="shell", from_shell=True)
    except ValueError as exc:
        print(f"Not done: {exc}")
        return 1
    if result.get("last_error"):
        print(f"Failed: {result['last_error']}")
        return 1
    print(_line(result))
    return 0
