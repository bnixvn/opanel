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
import socket

import uvicorn

from app.core import tls

logger = logging.getLogger("opanel.server")


def _bindable_host(host: str) -> str:
    """Fall back to IPv4 when the requested IPv6 bind cannot work.

    Turning IPv6 on writes PANEL_BIND_HOST=:: . If the address family is later
    taken away -- ipv6.disable=1 on the kernel command line, a rebuilt VPS --
    binding it would abort start-up, and the panel is the tool used to fix that
    kind of mistake, so it has to come up anyway.
    """
    if host not in {"::", "[::]"}:
        return host
    if not socket.has_ipv6:
        logger.warning("IPv6 is unavailable on this host; binding 0.0.0.0 instead")
        return "0.0.0.0"
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::", 0))
    except OSError as exc:
        logger.warning("Cannot bind IPv6 (%s); binding 0.0.0.0 instead", exc)
        return "0.0.0.0"
    return "::"


def listen_socket(host: str, port: int) -> socket.socket:
    """Open the panel's listening socket, dual-stack whenever IPv6 is asked for.

    asyncio sets IPV6_V6ONLY on every AF_INET6 server socket it creates, so
    letting uvicorn bind "::" would answer IPv6 clients and refuse every IPv4
    one -- including the admin trying to undo the change. Binding it here with
    V6ONLY cleared serves both families from one socket, and a host that cannot
    do IPv6 at all still gets an IPv4 panel.
    """
    if host in {"::", "[::]"}:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.bind(("::", port))
            sock.listen(2048)
            sock.set_inheritable(True)
            return sock
        except OSError as exc:
            logger.warning("Cannot listen on IPv6 (%s); falling back to 0.0.0.0", exc)
            if sock is not None:
                sock.close()
        host = "0.0.0.0"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(2048)
    sock.set_inheritable(True)
    return sock


def build_config() -> uvicorn.Config:
    host = _bindable_host(os.environ.get("PANEL_BIND_HOST", "0.0.0.0"))
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
            host=_bindable_host(os.environ.get("PANEL_BIND_HOST", "0.0.0.0")),
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

    sock = listen_socket(
        _bindable_host(os.environ.get("PANEL_BIND_HOST", "0.0.0.0")),
        int(os.environ.get("PANEL_PORT", "2222")),
    )
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
