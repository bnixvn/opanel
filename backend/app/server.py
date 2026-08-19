"""Panel entrypoint.

Runs the API over HTTPS by default, picking a certificate per requested
hostname so any site that already has SSL can also reach the panel on the panel
port. Falls back, in order: the SNI match, the configured/default certificate,
then plain HTTP. The panel coming up matters more than the panel coming up
encrypted -- it is the tool used to repair the box.
"""

from __future__ import annotations

import logging
import os

import uvicorn

from app.core import tls

logger = logging.getLogger("opanel.server")


def build_config() -> uvicorn.Config:
    host = os.environ.get("PANEL_BIND_HOST", "0.0.0.0")
    port = int(os.environ.get("PANEL_PORT", "2222"))
    kwargs = dict(
        app="app.main:app",
        host=host,
        port=port,
        # Only the loopback reverse proxy may set X-Forwarded-*; a direct hit on
        # the panel port cannot spoof the audit log IP or the rate-limit key.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    pair = tls.default_cert_pair(
        os.environ.get("PANEL_SSL_CERT", ""),
        os.environ.get("PANEL_SSL_KEY", ""),
    )
    if pair is not None:
        kwargs["ssl_certfile"] = str(pair[0])
        kwargs["ssl_keyfile"] = str(pair[1])
    return uvicorn.Config(**kwargs)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = build_config()
    try:
        config.load()
    except Exception:
        logger.exception("TLS setup failed; retrying without HTTPS so the panel stays reachable")
        config = uvicorn.Config(
            app="app.main:app",
            host=os.environ.get("PANEL_BIND_HOST", "0.0.0.0"),
            port=int(os.environ.get("PANEL_PORT", "2222")),
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
        config.load()

    if getattr(config, "ssl", None) is not None:
        config.ssl.sni_callback = tls.SniResolver()
        logger.info("Panel listening with HTTPS; per-domain certificates from %s", tls.CERT_STORE)
    else:
        logger.warning("Panel listening WITHOUT HTTPS (no usable certificate found)")

    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
