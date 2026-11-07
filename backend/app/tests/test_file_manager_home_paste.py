"""The file manager's tree starts at the account's home, and Copy/Move work like
a clipboard (operator, 2026-11-08, as BPanel v1.15.3).

/home/<user> is the root, with every website of that account under it, instead
of one website's folder; files are still changed only inside a website. Copy and
Move remember the items; Paste drops them into the folder on screen, which can
be the website's own root, so an empty destination is that root.
"""
from pathlib import Path
from types import SimpleNamespace

from app.services import file_manager

SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
APP = (SRC / "App.jsx").read_text(encoding="utf-8")
FILES = (SRC / "pages" / "Files.jsx").read_text(encoding="utf-8")
TREE = (SRC / "components" / "FolderTree.jsx").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = APP.index(f"function {name}(")
    return APP[start:APP.index("\n  }\n", start)]


def test_paste_at_the_top_lands_in_the_website_root(tmp_path):
    root = tmp_path / "site"
    (root / "public_html").mkdir(parents=True)
    (root / "public_html" / "notes.txt").write_text("x", encoding="utf-8")
    website = SimpleNamespace(root_path=str(root), linux_user=None, domain="example.test")
    file_manager.move_entries(website, ["public_html/notes.txt"], "", allow_executable=True)
    assert (root / "notes.txt").read_text(encoding="utf-8") == "x"
    assert not (root / "public_html" / "notes.txt").exists()


def test_the_tree_and_the_path_start_at_the_home():
    assert "match(/^(\\/home\\/[^/]+)\\//)" in FILES
    assert "homeLabel={fileHome} homeEntries={homeEntries}" in FILES
    assert "title={fileHome}>{fileHome}</button>" in FILES
    assert "startsWith(`${fileHome}/`)" in FILES
    assert "homeLabel = ''" in TREE


def test_nothing_is_made_or_uploaded_in_the_home_itself():
    toolbar = FILES[FILES.index('<div className="file-toolbar fm-toolbar">'):]
    toolbar = toolbar[:toolbar.index("</div>")]
    assert "!hasTarget ||" not in toolbar and toolbar.count("!canEdit ||") >= 5
    assert "const canEdit = hasTarget && !atHome;" in FILES


def test_copy_and_move_ask_for_no_path():
    assert "Copy to folder" not in APP and "Move to folder" not in APP
    clip = _function("clipFileItems")
    assert "promptDialog" not in clip and "request(" not in clip
    assert "clipFileItems('copy'" in APP and "clipFileItems('move'" in APP


def test_paste_goes_into_the_folder_on_screen_of_the_same_website():
    paste = _function("pasteFileClipboard")
    assert "clip.websiteId !== String(selectedWebsiteId)" in paste
    assert "destination_path: fileListPath || ''" in paste
    assert "if (clip.action === 'move') setFileClipboard(null);" in paste
    assert "{fileClipboard && !atHome && <div className=\"fm-selection fm-clipboard\"" in FILES
