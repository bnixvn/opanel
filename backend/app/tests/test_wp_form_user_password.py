"""WordPress install forms start fresh, and Add user has a random password
button (operator, 2026-11-09: "cái form cài wp nó còn ghi nhớ tài khoản, cài web
thứ 2 nó lại sử dụng đúng thông tin đó"; "Chỗ add user nên tạo cái nút random
password")."""
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
APP = (SRC / "App.jsx").read_text(encoding="utf-8")
WPM = (SRC / "pages" / "WordPress.jsx").read_text(encoding="utf-8")


def test_a_created_site_does_not_hand_its_administrator_to_the_next():
    assert "setWpAdminUser('admin');\n" in APP and "setWpAdminPassword('');\n" in APP and "setAdminEmail('');\n" in APP


def test_the_wordpress_manager_install_drawer_starts_fresh_each_time():
    drawer = WPM[WPM.index("function InstallDrawer("):]
    drawer = drawer[:drawer.index("\n}\n")]
    assert "admin_password: generatePassword()" in drawer
    assert "useState(fresh)" in drawer
    assert "if (!open) return;\n    setForm(fresh());" in drawer


def test_add_user_offers_a_random_password():
    fields = APP[APP.index("function renderUserFields"):]
    fields = fields[:fields.index("\n  }\n")]
    assert fields.count("generate copy/>") >= 2
    assert '<input type="password"' not in fields.split("Confirm password")[0]
