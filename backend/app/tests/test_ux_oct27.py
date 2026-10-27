"""The operator's 2026-10-27 batch: one click where there were two, forms in
the page, and an uploaded plugin or theme that is installed, not switched on."""
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
read = lambda path: (SRC / path).read_text(encoding="utf-8")


def test_new_website_and_new_database_open_under_the_search():
    for page, search, form in (("pages/Websites.jsx", '<div className="website-search">', "{renderCreate()}"),
                               ("pages/Databases.jsx", '<div className="list-search">', "<InlinePanel open={creating}")):
        text = read(page)
        assert "import InlinePanel from '../components/InlinePanel.jsx';" in text, page
        assert text.index(search) < text.index(form), page
    assert "<InlinePanel open={creating} onClose={closeCreate}" in read("pages/Websites.jsx")
    assert "<Drawer open={creating}" not in read("pages/Websites.jsx") + read("pages/Databases.jsx")


def test_a_website_and_a_wordpress_row_have_icons_not_a_menu():
    detail = read("pages/WebsiteDetail.jsx")
    assert "<IconActions label={t('Website actions for {domain}', { domain: site.domain })} items={siteMenu(site, { inDetail: true })}/>" in detail
    assert "ActionMenu" not in detail
    wp = read("pages/WordPress.jsx")
    assert "<IconActions className=\"wpm-row-actions\"" in wp
    assert "{t('Manage')}</button>" not in wp


def test_an_uploaded_zip_is_installed_not_activated():
    wp = read("pages/WordPress.jsx")
    assert "form.append('activate', '0');" in wp
    assert "activateUpload" not in wp


def test_the_owner_is_named_as_such_in_the_wordpress_list():
    wp = read("pages/WordPress.jsx")
    assert "t('Owner: {name}', { name: item.owner_username })" in wp
    assert "item.rel_path ? `/${item.rel_path}` : '/'" not in wp
