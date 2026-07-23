from app.services import wordpress


def test_delete_wordpress_binds_rm_site_to_owner_and_site_root(monkeypatch):
    calls = []
    root = "/home/siteuser/example.test"

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(wordpress.shell, "privileged", fake_privileged)

    wordpress.delete_wordpress(root)

    assert calls[0][0] == "rm-site"
    assert calls[0][1][0] == "siteuser"
    assert calls[0][1][1].replace("\\", "/").endswith(root)
    assert calls[0][1][2].replace("\\", "/").endswith(root)
    assert calls[0][2]["fallback"][0:2] == ["rm", "-rf"]
    assert calls[0][2]["fallback"][2].replace("\\", "/").endswith(root)
