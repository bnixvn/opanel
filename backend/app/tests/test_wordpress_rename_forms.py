"""A rename rewrites every form the old address is stored in (2026-11-07).

Elementor and the block editor keep links JSON-escaped (https:\\/\\/old); only
//old was replaced, and 182 escaped links stayed on the old domain after a
rename on BPanel's .88.
"""
from app.services import wordpress


def test_every_stored_form_of_the_address():
    forms = dict(wordpress._address_forms("legiang360.com", "cms.legiang360.com"))
    assert forms["//legiang360.com"] == "//cms.legiang360.com"
    assert forms[r"\/\/legiang360.com"] == r"\/\/cms.legiang360.com"
    assert forms["%2F%2Flegiang360.com"] == "%2F%2Fcms.legiang360.com"


def test_each_form_is_one_search_replace(monkeypatch):
    calls = []
    monkeypatch.setattr(wordpress.shell, "privileged",
                        lambda verb, helper_args=None, **kw: calls.append(helper_args))
    wordpress.rename_site_url("/home/alice/new.test/public_html", "alice", "old.test", "new.test")
    replaced = [args[2:4] for args in calls if args[1] == "search-replace"]
    assert replaced == [["//old.test", "//new.test"], [r"\/\/old.test", r"\/\/new.test"],
                        ["%2F%2Fold.test", "%2F%2Fnew.test"]]
    assert sum(1 for args in calls if args[1:3] == ["option", "update"]) == 2
