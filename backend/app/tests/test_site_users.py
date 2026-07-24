import hashlib
from pathlib import Path

import pytest

from app.api import websites
from app.services import site_users

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


def test_site_php_fpm_socket_is_scoped_to_site_root(tmp_path):
    first_root = tmp_path / "first.test"
    second_root = tmp_path / "second.test"

    first_socket = site_users.site_php_fpm_socket("siteuser", first_root, "8.3")
    second_socket = site_users.site_php_fpm_socket("siteuser", second_root, "8.3")

    first_hash = hashlib.sha256(str(first_root.resolve()).encode("utf-8")).hexdigest()[:12]
    assert first_socket == f"/tmp/lshttpd/opanel-siteuser-{first_hash}-lsphp83.sock"
    assert second_socket != first_socket


def test_site_php_fpm_socket_returns_none_without_php_version(tmp_path):
    assert site_users.site_php_fpm_socket("siteuser", tmp_path, None) is None


def test_php_fpm_socket_rejects_invalid_php_version(tmp_path):
    with pytest.raises(ValueError, match="Invalid PHP version"):
        site_users.site_php_fpm_socket("siteuser", tmp_path, "../8.3")


def test_legacy_user_php_fpm_socket_is_kept_for_callers_without_site_root():
    assert site_users.php_fpm_socket("siteuser", "8.3") == "/tmp/lshttpd/opanel-siteuser-lsphp83.sock"


def test_placeholder_page_for_linux_user_uses_site_file_write(tmp_path, monkeypatch):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(websites.file_manager.shell, "privileged", fake_privileged)
    monkeypatch.setattr(websites.file_manager, "_clear_fastcgi_cache", lambda: None)

    websites._write_placeholder_page("example.test", str(root), "siteuser", "8.3")

    assert calls[0][0] == "site-file-write"
    assert calls[0][1] == ["siteuser", str(root.resolve()), "public_html/index.html"]
    assert "example.test" in calls[0][2]["input"]


def test_panel_linux_users_are_sftp_chroot_only():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    for script_path in (INSTALL_SCRIPT, UPDATE_SCRIPT):
        script = script_path.read_text(encoding="utf-8")
        assert "Match Group opanel-sftp" in script
        assert "ChrootDirectory /home/%u" in script
        assert "ForceCommand internal-sftp -d /" in script
        assert "PermitTTY no" in script
        assert "AllowTcpForwarding no" in script
    assert "--shell /usr/sbin/nologin" in helper
    assert "--shell /bin/bash" not in helper
    assert 'chmod 0711 "$HOME_ROOT"' in helper
    assert 'chown "root:$user" "$home_dir"' in helper
    assert 'chmod 0751 "$home_dir"' in helper
    assert 'usermod -aG "$user" www-data' in helper


def test_panel_tools_ssl_vhosts_enable_http2_for_nginx_1_24():
    # OLS uses its own listener config for HTTP/2 — check helper uses lswsctrl
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "/usr/local/lsws/bin/lswsctrl" in helper


def test_site_permissions_use_standard_wordpress_modes():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert 'find "$target" -type d -exec chmod 755 {} +' in helper
    assert 'find "$target" -type f -exec chmod 644 {} +' in helper
    assert 'chown -R "$user:$user" "$target"' in helper
    assert 'harden_site_file "$target" "$user"' in helper
    assert 'install -o "$user" -g "$user" -m 0644' in helper
    assert 'chown -R "$user:$user" "$site_dir"' in update
    assert 'find "$site_dir" -type d -exec chmod 755 {} +' in update
    assert 'find "$site_dir" -type f -exec chmod 644 {} +' in update
    assert 'find "$target" -type d -exec chmod a-s {} +' in helper
    assert 'find "$site_dir" -type d -exec chmod a-s {} +' in update
    assert 'chmod a-s "$target"' in helper
    assert 'find "$target" -type d -exec chmod 2750 {} +' not in helper
    assert 'find "$target" -type f -exec chmod 640 {} +' not in helper
    assert 'find "$site_dir" -type d -exec chmod 2750 {} +' not in update
    assert 'find "$site_dir" -type f -exec chmod 640 {} +' not in update
    assert 'find "$target" -type d -exec chmod u-s {} +' not in helper
    assert 'find "$site_dir" -type d -exec chmod u-s {} +' not in update


def test_ols_server_group_can_read_managed_site_roots():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert 'user                             www-data' in helper
    assert 'group                            opanel-sites' in helper
    assert 'user                             nobody' not in helper
    assert 'group                            nogroup' not in helper
    assert 'install -d -o www-data -g "$opanel_SITES_GROUP" -m 2775 /tmp/lshttpd' in helper


def test_php_upload_tmp_dir_keeps_nginx_readable_group():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "ensure_php_runtime_dirs()" in helper
    assert 'install -d -o "$user" -g "$user" -m 0700 "$upload_dir"' in helper
    assert 'chmod g-s "$upload_dir"' in helper
    assert 'install -d -o "$user" -g "$user" -m 0700 "$sess_dir"' in helper
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert 'chown "$user:$user" "/var/lib/php/uploads/$user"' in update
    assert 'chmod 0700 "/var/lib/php/uploads/$user"' in update
    assert 'chmod g-s "/var/lib/php/uploads/$user"' in update
    assert 'ensure_php_runtime_dirs "$pool_user"' in helper


def test_php_fpm_pools_are_auto_tuned_for_vps_size():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "calculate_php_fpm_pool_tuning()" in helper
    assert "php_fpm_total_memory_mb()" in helper
    assert "php_fpm_cpu_count()" in helper
    assert "php_fpm_pool_count()" in helper
    assert "active_pool_divisor * active_pool_divisor < pool_count" in helper
    assert 'php_fpm_set_directive "$pool_file" "pm.max_children" "$PHP_FPM_MAX_CHILDREN"' in helper
    assert 'php_fpm_set_directive "$pool_file" "pm.process_idle_timeout" "${PHP_FPM_PROCESS_IDLE_TIMEOUT}s"' in helper
    assert 'php_fpm_set_directive "$pool_file" "pm.max_requests" "$PHP_FPM_MAX_REQUESTS"' in helper
    assert 'php_fpm_set_directive "$pool_file" "request_terminate_timeout" "${PHP_FPM_REQUEST_TERMINATE_TIMEOUT}s"' in helper
    assert "opanel_PHP_FPM_WORKER_MB" in helper
    assert "opanel_PHP_FPM_MAX_CHILDREN" in helper
    assert "php-fpm-retune)" in helper
    assert "pm.max_children = 8" not in helper


def test_mariadb_is_auto_tuned_for_vps_size():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "calculate_mariadb_tuning()" in helper
    assert "write_mariadb_tuning()" in helper
    assert "mariadb-retune)" in helper
    assert "innodb_buffer_pool_size = ${MARIADB_INNODB_BUFFER_POOL_SIZE}" in helper
    assert "max_connections = ${MARIADB_MAX_CONNECTIONS}" in helper
    assert "table_open_cache = ${MARIADB_TABLE_OPEN_CACHE}" in helper
    assert "ensure_mariadb_slow_log" in helper
    assert "opanel_MARIADB_BUFFER_POOL_SIZE" in helper


def test_manual_ssl_helper_installs_private_key_outside_web_root():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "install_manual_ssl()" in helper
    assert "remove_manual_ssl()" in helper
    assert 'base="/usr/local/lsws/conf/opanel/ssl/sites/${domain}"' in helper
    assert 'install -m 0640 -o root -g opanel "$tmpdir/privkey.key" "$base/privkey.key"' in helper
    assert "manual-ssl-install)" in helper
    assert "manual-ssl-remove)" in helper


def test_terminal_helper_rejects_paths_outside_user_home():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "require_terminal_path_args()" in helper
    assert "require_terminal_download_args()" in helper
    assert 'deny "terminal path argument is outside panel user home: $arg"' in helper
    assert 'deny "terminal path argument escapes user home: $arg"' in helper
    assert 'deny "terminal URL argument uses local file scheme: $arg"' in helper
    assert 'require_terminal_path_args "$user" "$target" "$@"' in helper
    assert 'require_terminal_download_args "$user" "$target" "$@"' in helper


def test_rm_site_helper_binds_delete_to_user_root_and_deletes_no_follow():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "require_bound_managed_path()" in helper
    assert "delete_no_follow()" in helper
    assert 'target=$(require_bound_managed_path "$user" "$root" "$path")' in helper
    assert "os.path.normpath(sys.argv[1])" in helper
    assert 'root_relative="${normalized_root#${HOME_ROOT}/${user}/}"' in helper
    assert '[[ "$root_relative" == */* ]] && deny "site root must be a direct domain path: $normalized_root"' in helper
    assert '[[ "$target" == "$normalized_root" || "$target_relative" == */* ]] || deny "refusing to operate on a panel user home"' in helper
    assert 'delete_no_follow "$user" "$root" "$target"' in helper
    assert "os.O_NOFOLLOW" in helper
    assert "os.unlink(name, dir_fd=dir_fd)" in helper
    assert 'usage: rm-site <site-user> <site-root> <path>' in helper
