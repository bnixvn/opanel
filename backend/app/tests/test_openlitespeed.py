from app.services import openlitespeed


def test_rewrite_vhost_ignores_nginx_compat_ssl_kwargs(monkeypatch):
    captured = {}

    def fake_render_vhost(domain, root_path, **kwargs):
        captured["domain"] = domain
        captured["root_path"] = root_path
        captured["kwargs"] = kwargs
        return "vhssl  { }"

    class DummyShell:
        def privileged(self, *args, **kwargs):
            captured["helper"] = args
            captured["helper_kwargs"] = kwargs

    monkeypatch.setattr(openlitespeed, "render_vhost", fake_render_vhost)
    monkeypatch.setattr(openlitespeed, "shell", DummyShell())

    result = openlitespeed.rewrite_vhost(
        "example.test",
        "/home/admin/example.test",
        app_type="wordpress",
        php_version="8.4",
        include_ssl=False,
        preserve_existing_ssl=False,
    )

    assert result == "vhssl  { }"
    assert "include_ssl" not in captured["kwargs"]
    assert "preserve_existing_ssl" not in captured["kwargs"]
