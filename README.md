# OPanel

**OPanel** là bảng điều khiển hosting gọn nhẹ cho **Ubuntu 24.04**, chạy trên **OpenLiteSpeed** và **LSPHP**. Một giao diện web duy nhất để quản lý website WordPress/PHP, SSL, cơ sở dữ liệu, backup, firewall, WAF và người dùng hosting, với mỗi tài khoản là một Linux user riêng biệt.

- **Phiên bản mới nhất:** xem [Releases](https://github.com/bnixvn/opanel/releases)
- **Giao diện:** tiếng Việt và tiếng Anh, có chế độ sáng/tối

## Ảnh chụp màn hình

![Tổng quan](docs/screenshots/dashboard.webp)

<table>
<tr><td width="50%"><a href="docs/screenshots/login.webp"><img src="docs/screenshots/login.webp" alt="Đăng nhập"></a><br><sub>Đăng nhập</sub></td><td width="50%"><a href="docs/screenshots/dashboard-dark.webp"><img src="docs/screenshots/dashboard-dark.webp" alt="Giao diện tối"></a><br><sub>Giao diện tối</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/websites.webp"><img src="docs/screenshots/websites.webp" alt="Danh sách website"></a><br><sub>Danh sách website</sub></td><td width="50%"><a href="docs/screenshots/ssl.webp"><img src="docs/screenshots/ssl.webp" alt="SSL: Let's Encrypt, wildcard, chứng chỉ có sẵn hoặc thủ công"></a><br><sub>SSL: Let's Encrypt, wildcard, chứng chỉ có sẵn hoặc thủ công</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/databases.webp"><img src="docs/screenshots/databases.webp" alt="Cơ sở dữ liệu MariaDB và phpMyAdmin"></a><br><sub>Cơ sở dữ liệu MariaDB và phpMyAdmin</sub></td><td width="50%"><a href="docs/screenshots/cron.webp"><img src="docs/screenshots/cron.webp" alt="Cron"></a><br><sub>Cron</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/file-manager.webp"><img src="docs/screenshots/file-manager.webp" alt="Quản lý tệp"></a><br><sub>Quản lý tệp</sub></td><td width="50%"><a href="docs/screenshots/sftp.webp"><img src="docs/screenshots/sftp.webp" alt="Tài khoản SFTP"></a><br><sub>Tài khoản SFTP</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/backups.webp"><img src="docs/screenshots/backups.webp" alt="Sao lưu website"></a><br><sub>Sao lưu website</sub></td><td width="50%"><a href="docs/screenshots/restore.webp"><img src="docs/screenshots/restore.webp" alt="Restore kiểu DirectAdmin"></a><br><sub>Restore kiểu DirectAdmin</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/backup-schedules.webp"><img src="docs/screenshots/backup-schedules.webp" alt="Sao lưu định kỳ"></a><br><sub>Sao lưu định kỳ</sub></td><td width="50%"><a href="docs/screenshots/users.webp"><img src="docs/screenshots/users.webp" alt="Người dùng panel và hạn mức"></a><br><sub>Người dùng panel và hạn mức</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/settings.webp"><img src="docs/screenshots/settings.webp" alt="Trang Cài đặt"></a><br><sub>Trang Cài đặt</sub></td><td width="50%"><a href="docs/screenshots/firewall.webp"><img src="docs/screenshots/firewall.webp" alt="Tường lửa"></a><br><sub>Tường lửa</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/waf.webp"><img src="docs/screenshots/waf.webp" alt="WAF và chặn bad bot"></a><br><sub>WAF và chặn bad bot</sub></td><td width="50%"><a href="docs/screenshots/access-logs.webp"><img src="docs/screenshots/access-logs.webp" alt="Nhật ký truy cập"></a><br><sub>Nhật ký truy cập</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/account-security.webp"><img src="docs/screenshots/account-security.webp" alt="Bảo mật tài khoản: passkey và 2FA"></a><br><sub>Bảo mật tài khoản: passkey và 2FA</sub></td><td width="50%"><a href="docs/screenshots/panel-settings.webp"><img src="docs/screenshots/panel-settings.webp" alt="Cài đặt panel"></a><br><sub>Cài đặt panel</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/services.webp"><img src="docs/screenshots/services.webp" alt="Dịch vụ"></a><br><sub>Dịch vụ</sub></td><td width="50%"><a href="docs/screenshots/php.webp"><img src="docs/screenshots/php.webp" alt="Cấu hình PHP và extension"></a><br><sub>Cấu hình PHP và extension</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/updates.webp"><img src="docs/screenshots/updates.webp" alt="Cập nhật"></a><br><sub>Cập nhật</sub></td><td width="50%"><a href="docs/screenshots/addons.webp"><img src="docs/screenshots/addons.webp" alt="Addon"></a><br><sub>Addon</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/malware.webp"><img src="docs/screenshots/malware.webp" alt="Quét mã độc"></a><br><sub>Quét mã độc</sub></td><td width="50%"><a href="docs/screenshots/mcp.webp"><img src="docs/screenshots/mcp.webp" alt="Trợ lý AI (MCP)"></a><br><sub>Trợ lý AI (MCP)</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/notifications.webp"><img src="docs/screenshots/notifications.webp" alt="Thông báo qua email và Telegram"></a><br><sub>Thông báo qua email và Telegram</sub></td><td width="50%"><a href="docs/screenshots/customer-dashboard.webp"><img src="docs/screenshots/customer-dashboard.webp" alt="Tổng quan của khách hosting"></a><br><sub>Tổng quan của khách hosting</sub></td></tr>
<tr><td width="50%"><a href="docs/screenshots/customer-websites.webp"><img src="docs/screenshots/customer-websites.webp" alt="Website của khách hosting"></a><br><sub>Website của khách hosting</sub></td><td width="50%"><a href="docs/screenshots/resource-usage.webp"><img src="docs/screenshots/resource-usage.webp" alt="Mức dùng tài nguyên của khách hosting"></a><br><sub>Mức dùng tài nguyên (tiện ích Giới hạn tài nguyên)</sub></td></tr>
</table>

## Tính năng

### Website
- **Tạo website:** WordPress cài sẵn bằng một cú nhấp (WP-CLI), website PHP hoặc website tĩnh.
- **Nhiều phiên bản PHP:** LSPHP 8.4 (mặc định) và 8.3 cài sẵn; có thể cài thêm 7.4, 8.1, 8.2, 8.5 ngay trong panel. Mỗi website chọn phiên bản riêng.
- **Tên miền phụ (alias)** cho mỗi website; `.htaccess` được hỗ trợ đầy đủ.
- **Mỗi tài khoản một Linux user:** mã nguồn nằm ở `/home/<user>/<domain>/public_html`, PHP của website chạy bằng chính user đó, nên các tài khoản không đọc được file của nhau.
- **LSCache** cho WordPress.

### SSL
- **Let's Encrypt** (HTTP-01), tự gia hạn.
- **Wildcard** qua Cloudflare DNS-01.
- **Dùng lại chứng chỉ có sẵn** hoặc tải lên chứng chỉ thủ công.

### Cơ sở dữ liệu
- **MariaDB:** tạo và quản lý database, chuyển quyền sở hữu giữa các tài khoản.
- **phpMyAdmin** đăng nhập một chạm (token dùng một lần, hết hạn sau 60 giây).
- **Tự động tinh chỉnh MariaDB** theo RAM, CPU và loại ổ đĩa của VPS.

### File, SFTP và Cron
- **Trình quản lý file:** tải lên, sửa, nén/giải nén, phân quyền, hiện ngày sửa đổi.
- **SFTP:** tài khoản chính của mỗi user, và tài khoản SFTP phụ giới hạn trong một thư mục (chroot).
- **Cron:** chạy với PHP của website, hỗ trợ lệnh WP-CLI.

### Backup và Restore
- **Backup website** (file + database) và **backup toàn bộ tài khoản** (mọi website, database, chứng chỉ SSL).
- **Backup theo lịch** hằng ngày, hằng tuần hoặc hằng tháng, xoay vòng 7 bản.
- **Nơi lưu ngoài server:** S3 (AWS, Wasabi, Backblaze B2, MinIO, R2…) và SFTP.
- **Restore kiểu DirectAdmin** qua 4 bước: chọn nguồn (trên server này, Backup Destination, hoặc máy chủ khác qua SFTP/FTP/FTPS), điền thông tin kết nối, chọn user, bấm Restore.
- **Chuyển từ DirectAdmin:** restore trực tiếp file `user.admin.<user>.tar.zst`, kể cả kéo thẳng từ server DirectAdmin cũ; website, subdomain, database và SSL được tạo tự động.
- **Backup dung lượng lớn tải lên qua SFTP**, xem [bên dưới](#tải-backup-lớn-qua-sftp).

### Bảo mật
- **Firewall** iptables + ipset: chặn IP/dải mạng kèm ghi chú, blocklist tự cập nhật, danh sách địa chỉ bị chặn có tìm kiếm và phân trang.
- **WAF** ModSecurity cho OpenLiteSpeed với bộ rule gọn cho WordPress/Laravel/PHP, bật tắt theo từng website.
- **Chặn bad bot** theo danh sách chung và danh sách riêng từng website.
- **Đăng nhập an toàn:** 2FA (Google Authenticator), passkey, giới hạn số lần đăng nhập sai, nhật ký đăng nhập và nhật ký thao tác.
- **Nhật ký truy cập** của từng website và những gì WAF đã chặn.

### PHP
- **Cấu hình từng phiên bản PHP:** giới hạn bộ nhớ, upload, thời gian chạy, OPcache.
- **PHP extension:** một bảng cho mọi phiên bản PHP, cài từng ô hoặc "Install all".
- **Tự động tinh chỉnh PHP** (OPcache, số worker LSAPI, bộ nhớ) theo cấu hình VPS.

### Người dùng và hạn mức
- **Vai trò:** Admin và End user (khách hosting chỉ thấy website của mình).
- **Hạn mức** số website, số database và dung lượng cho mỗi tài khoản; gói hosting (plan).
- **Admin đăng nhập nhanh** vào tài khoản khách để hỗ trợ.

### Hệ thống
- **Dashboard:** CPU, RAM, ổ đĩa, mạng và những việc cần chú ý.
- **Dịch vụ:** xem, khởi động, dừng các dịch vụ của server.
- **Cập nhật:** cập nhật OPanel và gói hệ điều hành ngay trong panel, có tự động cập nhật.
- **Menu cứu hộ qua SSH** (`opanel`) khi không vào được giao diện web.
- **Tích hợp WHMCS:** module server tại `modules/servers/opanel`.

## Addon

Các tính năng tuỳ chọn, cài và gỡ ở **Settings › Addons**. Addon chỉ hiện trên menu khi đang bật.

| Addon | Chức năng |
|---|---|
| **Malware Scanner** | ClamAV + Linux Malware Detect: quét một website, mọi website hoặc toàn server, theo lịch hoặc thời gian thực; tự cách ly file độc hại và khôi phục nếu báo nhầm. Bộ chữ ký ClamAV được lọc bằng [clam-juice](https://github.com/swelljoe/clam-juice), chỉ giữ những gì máy chủ web Linux cần, nên clamd chỉ tốn khoảng 200 MB RAM thay vì 1–1,5 GB. |
| **Fail2ban** | Chặn IP ở firewall sau nhiều lần đăng nhập SSH hoặc panel thất bại. |
| **MCP server** | Cho trợ lý AI (Claude Code, Cursor, VS Code…) đọc và thao tác panel qua Model Context Protocol, mỗi người dùng một token riêng. |
| **Notifications** | Gửi cảnh báo qua email (SMTP) và Telegram cho quản trị viên: backup lỗi, dịch vụ dừng, ổ đĩa đầy, SSL sắp hết hạn, phát hiện mã độc, có bản cập nhật… |

## Yêu cầu hệ thống

- Ubuntu 24.04 LTS, nên là máy mới cài
- Quyền root
- Tối thiểu 1 vCPU / 1 GB RAM; khuyến nghị 2 vCPU / 2 GB RAM (cộng thêm khoảng 0,5 GB nếu dùng Malware Scanner)
- Không bắt buộc: một tên miền trỏ về IP của server để panel có SSL

## Cài đặt

Chạy bằng root trên server Ubuntu 24.04 mới:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/bnixvn/opanel/main/installer/install.sh)
```

Trình cài đặt sẽ hỏi tên miền của panel (để trống thì dùng IP), cổng (mặc định `2222`), có bật SSL Let's Encrypt hay không và email đăng ký SSL.

### Cài đặt không cần trả lời

Truyền sẵn các giá trị qua biến môi trường:

```bash
export PANEL_URL=https://panel.example.com:2222 ENABLE_SSL=yes SSL_EMAIL=admin@example.com
bash <(curl -fsSL https://raw.githubusercontent.com/bnixvn/opanel/main/installer/install.sh)
```

### Trình cài đặt làm những gì

1. Cài các gói nền: MariaDB, Redis, OpenSSH/SFTP, Node.js 22, certbot, phpMyAdmin, WP-CLI, iptables, ipset.
2. Cài **OpenLiteSpeed** và **LSPHP 8.4 + 8.3** từ kho của LiteSpeed.
3. Chép mã nguồn vào `/opt/opanel`, build giao diện, tạo môi trường Python.
4. Tạo tài khoản dịch vụ `opanel` và tài khoản `admin` (Linux/SFTP).
5. Tạo dịch vụ systemd `opanel-api`, cấu hình phpMyAdmin SSO và firewall.
6. Cấp SSL Let's Encrypt cho tên miền của panel (nếu chọn).
7. Cài lệnh `opanel-update` để cập nhật về sau.

Sau khi cài xong, mở địa chỉ panel được in ra cuối quá trình cài. Mật khẩu `admin` được in ra ở đó và lưu trong `/root/login.txt`; hãy cất vào trình quản lý mật khẩu.

## Cập nhật

Trong panel: **Settings › Updates**. Hoặc chạy bằng root:

```bash
opanel-update
```

```bash
# Cài một bản phát hành cụ thể
opanel-update --tag v1.23.0
```

Nếu trình duyệt vẫn hiện giao diện cũ, hãy tải lại trang bằng Ctrl + Shift + R.

## Menu cứu hộ qua SSH

```bash
opanel
```

Dùng khi không vào được giao diện web: xem thông tin đăng nhập, trạng thái, log gần đây, khởi động lại dịch vụ, mở lại cổng firewall, đổi địa chỉ/cổng panel, sửa SSL của panel, sửa quyền, đổi mật khẩu `admin` và cập nhật OPanel.

```bash
opanel change-ip                  # Đổi IP sau khi clone VPS
opanel change-admin-password      # Đổi mật khẩu admin
journalctl -u opanel-api -f       # Xem log của panel
systemctl status opanel-api lsws mariadb redis-server
```

## Thư mục quan trọng

| Đường dẫn | Nội dung |
|---|---|
| `/opt/opanel/` | Mã nguồn và giao diện |
| `/opt/opanel/backend/.env` | Cấu hình của panel |
| `/home/<user>/<domain>/public_html` | Mã nguồn website |
| `/var/backups/opanel/` | File backup |
| `/home/admin/opanel-backups/da/` | Backup DirectAdmin chờ restore |
| `/home/admin/backups/` | Thư mục nhận backup lớn tải lên qua SFTP |
| `/var/lib/opanel/` | Dữ liệu vận hành (firewall, addon, lịch sử quét, khu cách ly) |
| `/var/log/opanel-php/<domain>/php_error.log` | Log lỗi PHP của từng website |
| `/usr/local/lsws/conf/opanel/` | Cấu hình vhost OpenLiteSpeed, SSL, rule ModSecurity |

### Tải backup lớn qua SFTP

Trình duyệt chỉ tải lên được file đến 1 GB. File lớn hơn thì tải qua SFTP, giống
cách làm của DirectAdmin:

| | |
|---|---|
| Máy chủ | IP của server, cổng `22` |
| User | `admin`, mật khẩu SFTP đặt ở trang **Tài khoản SFTP** |
| Thư mục | `/backups` (trên server là `/home/admin/backups`) |

Thả cả backup OPanel lẫn backup DirectAdmin vào đó. Tải xong thì bấm **Làm mới** ở
**Sao lưu → Khôi phục → Trên server này**: file hiện chung danh sách, có nhãn `SFTP`.
Không cần chỉnh quyền gì: thư mục thuộc nhóm `opanel`, nên panel đọc được ngay.
File đang tải dở (đuôi `.filepart`, `.part`, hoặc vừa được ghi trong 1 phút) chưa
hiện ra. Khi restore, panel chuyển file sang thư mục của nó rồi mới đọc, nên sau
đó file không còn trong `/backups`. Đăng nhập bằng `root` để tải lên cũng được,
đặt file vào `/home/admin/backups`.

Đừng để backup nằm lâu trong thư mục này: các website của admin chạy bằng chính
user `admin`, nên nếu một website đó bị hack thì đọc được backup đang để ở đây.
Không cần restore nữa thì xoá file bằng nút thùng rác trên dòng của nó.

## Lưu ý về firewall

- **Không phải firewall chặn mặc định.** Chính sách `INPUT` là `ACCEPT` và không chain nào của panel kết thúc bằng `DROP`, nên mọi dịch vụ đang lắng nghe đều truy cập được trừ khi bị chặn rõ ràng. Các cổng 22, 80, 443, 465, 587 và cổng panel chỉ là những cổng panel không cho phép bạn chặn, chứ không phải những cổng duy nhất đang mở. Nếu cần chặn mặc định, hãy đặt ở lớp phía trước (security group của nhà cung cấp) và kiểm tra `ss -ltnp`.
- **Blocklist vẫn có thể khoá bạn khỏi server.** Blocklist tải từ URL bạn nhập và được nạp thành rule `DROP` trước mọi rule khác, nên một dòng sai trong danh sách của bên thứ ba có thể chặn chính bạn. Dải rộng hơn `/8` (IPv4) hoặc `/16` (IPv6) bị từ chối và loopback luôn được miễn.
- **Cài OPanel sẽ tắt và gỡ `ufw`.** Cấu hình cũ được sao lưu ở `/var/lib/opanel/ufw-backup-<thời gian>` nhưng rule không được chuyển đổi: server đang được ufw bảo vệ sẽ không còn được bảo vệ như trước.

## Công nghệ

| Thành phần | Công nghệ |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy, SQLite, Pydantic v2 |
| Frontend | React, Vite, lucide-react |
| Web server | OpenLiteSpeed + LSPHP |
| Cơ sở dữ liệu | MariaDB, Redis |
| Bảo mật | iptables + ipset, ModSecurity, Let's Encrypt (certbot) |
| Hệ điều hành | Ubuntu 24.04 LTS, systemd |

## Giấy phép

Copyright 2026 bNix Limited. Phát hành theo giấy phép GNU Affero General Public License v3.0 (AGPL-3.0).
