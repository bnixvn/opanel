from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="end_user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    website_limit: Mapped[int] = mapped_column(Integer, default=5)
    storage_limit_mb: Mapped[int] = mapped_column(Integer, default=1024)
    # Bumped to invalidate previously-issued JWTs (logout-everywhere, role
    # change, password reset by admin, account disable, etc).
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    totp_secret: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    websites: Mapped[List["Website"]] = relationship(back_populates="owner")


class Website(Base):
    __tablename__ = "websites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    root_path: Mapped[str] = mapped_column(String(500))
    document_root: Mapped[str] = mapped_column(String(255), default="public_html")
    linux_user: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    php_version: Mapped[str] = mapped_column(String(16), default="8.4")
    app_type: Mapped[str] = mapped_column(String(32), default="wordpress")
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ssl_mode: Mapped[str] = mapped_column(String(16), default="none")
    ssl_cert_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ssl_key_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ssl_ca_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ssl_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    nginx_custom: Mapped[str] = mapped_column(Text, default="")
    nginx_config_mode: Mapped[str] = mapped_column(String(16), default="managed")
    nginx_rewrite_mode: Mapped[str] = mapped_column(String(32), default="none")

    # Webserver aliases — DB columns kept as nginx_* for migration safety
    @property
    def webserver_custom(self) -> str:
        return self.nginx_custom

    @webserver_custom.setter
    def webserver_custom(self, value: str) -> None:
        self.nginx_custom = value

    @property
    def webserver_config_mode(self) -> str:
        return self.nginx_config_mode

    @webserver_config_mode.setter
    def webserver_config_mode(self, value: str) -> None:
        self.nginx_config_mode = value

    @property
    def webserver_rewrite_mode(self) -> str:
        return self.nginx_rewrite_mode

    @webserver_rewrite_mode.setter
    def webserver_rewrite_mode(self, value: str) -> None:
        self.nginx_rewrite_mode = value
    waf_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    waf_default_rules: Mapped[str] = mapped_column(Text, default="")
    waf_custom_rules: Mapped[str] = mapped_column(Text, default="")
    http_flood_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    http_flood_config: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    owner: Mapped[User] = relationship(back_populates="websites")
    database: Mapped[Optional["DatabaseAccount"]] = relationship(back_populates="website", uselist=False)
    aliases: Mapped[List["WebsiteAlias"]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
        order_by="WebsiteAlias.domain",
    )


class WebsiteAlias(Base):
    __tablename__ = "website_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(16), default="alias")
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    website: Mapped[Website] = relationship(back_populates="aliases")


class DatabaseAccount(Base):
    __tablename__ = "database_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    website_id: Mapped[Optional[int]] = mapped_column(ForeignKey("websites.id"), nullable=True)
    db_name: Mapped[str] = mapped_column(String(64), unique=True)
    db_user: Mapped[str] = mapped_column(String(64), unique=True)
    db_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    owner: Mapped["User"] = relationship()
    website: Mapped[Optional[Website]] = relationship(back_populates="database")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(128))
    target: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    jti: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SftpBackupTarget(Base):
    __tablename__ = "sftp_backup_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(128))
    password: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    private_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remote_path: Mapped[str] = mapped_column(String(500), default="/backups/opanel")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # TOFU host key pinning so the second SSH connection on cannot be silently
    # MITM'd. Populated on first successful connect (or by an explicit rotate
    # action) and verified on every connect afterwards.
    host_key_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    host_key_fingerprint: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BackupSchedule(Base):
    __tablename__ = "backup_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    user_ids: Mapped[str] = mapped_column(Text, default="")
    all_users: Mapped[bool] = mapped_column(Boolean, default=False)
    target_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sftp_backup_targets.id"), nullable=True)
    schedule: Mapped[str] = mapped_column(String(100), default="0 2 * * *")
    retention: Mapped[int] = mapped_column(Integer, default=7)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="pending")
    last_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
