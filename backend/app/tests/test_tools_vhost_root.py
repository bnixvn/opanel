"""The server's own address opens the panel, not phpMyAdmin (operator,
2026-11-11: https://sv.inoxtanson.vn/ bounced to phpMyAdmin's sign-on page).
The tools vhost answers phpMyAdmin under /phpmyadmin/ only; / goes to the
panel and any other path but /.well-known/ is refused."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")


def _rules(text: str, marker: str) -> list[str]:
    block = text[text.index(marker):]
    block = block[block.index("rules                   <<<END_rules\n") + len("rules                   <<<END_rules\n"):]
    return block[:block.index("END_rules\n")].splitlines()


def test_the_helper_and_the_installer_write_the_same_rules():
    helper = _rules(HELPER, "refresh_tools_ols() {")
    install = _rules(INSTALL, "write_tools_vhost_config() {")
    assert helper == [line.replace("${PANEL_PORT}", "${port}") for line in install]


def test_root_goes_to_the_panel_and_phpmyadmin_stays_under_its_folder():
    rules = _rules(HELPER, "refresh_tools_ols() {")
    assert rules == [
        "RewriteRule ^/phpmyadmin\\$ /phpmyadmin/ [R=301,L]",
        "RewriteRule ^/?\\$ https://${host}:${port}/ [R=302,L]",
        "RewriteCond %{REQUEST_URI} !^/phpmyadmin/",
        "RewriteCond %{REQUEST_URI} !^/\\.well-known/",
        "RewriteRule ^ - [F,L]",
        "RewriteRule ^/phpmyadmin/(.*)\\$ /\\$1 [L]",
    ]
