"""Panel entrypoint.

Runs the API over HTTPS, picking a certificate per requested hostname so any
site that already has SSL can also reach the panel on the panel port. Falls
back, in order: the SNI match, then the configured/default certificate.

If neither works the port still answers -- the panel is the tool used to repair
the box, so going dark would be its own outage -- but it answers with the
recovery page in app/core/tls_degraded.py and nothing else. It does not serve
the panel over plain HTTP: cookie flags follow the request scheme, so that mode
put admin session cookies on the wire unencrypted.
"""

from __future__ import annotations

import logging
import os
import socket
import ssl
from pathlib import Path

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


def usable_cert_pair() -> tuple[Path, Path] | None:
    """The certificate to start with, or None if TLS cannot come up.

    uvicorn's Config.load() both builds the SSL context and imports the ASGI
    app, so letting it decide would file an application import error as a
    certificate problem and hide a real bug behind the recovery page. Prove the
    certificate loads here, and let an app that will not import crash loudly.
    """
    pair = tls.default_cert_pair(
        os.environ.get("PANEL_SSL_CERT", ""),
        os.environ.get("PANEL_SSL_KEY", ""),
    )
    if pair is None:
        # The installer seeds a self-signed default before the panel first
        # starts, so an empty store is a broken box, not a supported mode.
        logger.error("No usable panel certificate found in %s", tls.CERT_STORE)
        return None
    try:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(str(pair[0]), str(pair[1]))
    except (ssl.SSLError, OSError, ValueError):
        logger.exception("Panel certificate %s could not be loaded", pair[0])
        return None
    return pair


def build_config(pair: tuple[Path, Path]) -> uvicorn.Config:
    return uvicorn.Config(
        app="app.main:app",
        host=_bindable_host(os.environ.get("PANEL_BIND_HOST", "0.0.0.0")),
        port=int(os.environ.get("PANEL_PORT", "2222")),
        # Only the loopback reverse proxy may set X-Forwarded-*; a direct hit on
        # the panel port cannot spoof the audit log IP or the rate-limit key.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ssl_certfile=str(pair[0]),
        ssl_keyfile=str(pair[1]),
    )


def degraded_config() -> uvicorn.Config:
    """Plain HTTP, but serving only the "TLS is broken" page.

    The port has to keep answering or the operator loses the one screen that
    tells them what went wrong. It must not keep serving the panel: cookie flags
    follow the request scheme, so over HTTP the session and CSRF cookies go out
    without Secure and an admin login crosses the network in clear text.
    """
    config = uvicorn.Config(
        app="app.core.tls_degraded:app",
        host=_bindable_host(os.environ.get("PANEL_BIND_HOST", "0.0.0.0")),
        port=int(os.environ.get("PANEL_PORT", "2222")),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    config.load()
    return config


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    pair = usable_cert_pair()

    if pair is None:
        config = degraded_config()
        logger.error(
            "Panel is serving the TLS recovery page only. Sign-in stays disabled "
            "until a certificate loads; run `opanel-helper panel-cert-sync` and "
            "restart opanel-api."
        )
    else:
        config = build_config(pair)
        config.load()
        config.ssl.sni_callback = tls.SniResolver()
        logger.info("Panel listening with HTTPS; per-domain certificates from %s", tls.CERT_STORE)

    sock = listen_socket(
        _bindable_host(os.environ.get("PANEL_BIND_HOST", "0.0.0.0")),
        int(os.environ.get("PANEL_PORT", "2222")),
    )
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
