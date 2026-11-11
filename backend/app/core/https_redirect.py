"""Plain HTTP on the panel's HTTPS port answers with a redirect to HTTPS.

The panel terminates TLS itself on one port (2222 by default), so typing
panel.example.com:2222 in a browser - which means http:// - sent a plain
request into a TLS socket, and the browser showed a broken-connection error
(operator, 2026-11-08). Now each connection's first byte decides: a TLS
handshake (0x16) is handed to TLS and the panel as before; anything else is
read as an HTTP request and answered with a 301 to https:// on the same host,
port and path. Nothing else is served over plain HTTP.

asyncio's own SSLProtocol does the TLS, fed the bytes already read, so the
panel sees the same TLS transport (client address, SNI certificate, scheme
https) it saw when create_server did the wrapping. That needs the asyncio
event loop: uvloop's transports are not mixed with asyncio's SSLProtocol.
"""

from __future__ import annotations

import asyncio
import re
from asyncio import sslproto
from functools import partial

TLS_HANDSHAKE = 0x16
# How long a connection may stay silent before its first byte, and how much of
# a plain request is read to find its Host header.
FIRST_BYTE_TIMEOUT = 15.0
MAX_PLAIN_HEAD = 8192
SSL_HANDSHAKE_TIMEOUT = 60.0
HOST_RE = re.compile(r"(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?")
PATH_RE = re.compile(r"/[\x21-\x7e]*")


def redirect_location(head: bytes, port: int) -> str | None:
    """https://<host>[:port]<path> for a plain request head, or None when it
    names no usable host."""
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    path = parts[1] if len(parts) >= 2 and PATH_RE.fullmatch(parts[1]) else "/"
    host = ""
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "host":
            host = value.strip()
            break
    if not host or not HOST_RE.fullmatch(host):
        return None
    if not re.search(r":\d+$", host.rsplit("]", 1)[-1]):
        host = f"{host}:{port}"
    return f"https://{host}{path}"


class FirstByteProtocol(asyncio.Protocol):
    """Reads the first bytes of a connection, then becomes TLS or a redirect."""

    def __init__(self, protocol_factory, ssl_context, port: int, **kwargs):
        self._make_protocol = partial(protocol_factory, **kwargs)
        self._ssl_context = ssl_context
        self._port = port
        self._loop = kwargs.get("_loop") or asyncio.get_event_loop()
        self._transport: asyncio.Transport | None = None
        self._buffer = b""
        self._timer: asyncio.TimerHandle | None = None
        self._done = False

    def connection_made(self, transport):
        self._transport = transport
        self._timer = self._loop.call_later(FIRST_BYTE_TIMEOUT, self._give_up)

    def data_received(self, data: bytes) -> None:
        if self._done:
            return
        self._buffer += data
        if self._buffer[0] == TLS_HANDSHAKE:
            self._finish()
            self._start_tls()
        elif b"\r\n\r\n" in self._buffer or len(self._buffer) >= MAX_PLAIN_HEAD:
            self._finish()
            self._redirect()

    def connection_lost(self, exc) -> None:
        self._finish()

    def _finish(self) -> None:
        self._done = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _give_up(self) -> None:
        self._timer = None
        self._done = True
        if self._transport is not None:
            self._transport.close()

    def _start_tls(self) -> None:
        tls = sslproto.SSLProtocol(
            self._loop, self._make_protocol(), self._ssl_context, None,
            server_side=True, call_connection_made=True,
            ssl_handshake_timeout=SSL_HANDSHAKE_TIMEOUT,
        )
        transport = self._transport
        transport.set_protocol(tls)
        tls.connection_made(transport)
        data, self._buffer = self._buffer, b""
        while data:
            buffer = tls.get_buffer(len(data))
            size = min(len(buffer), len(data))
            buffer[:size] = data[:size]
            tls.buffer_updated(size)
            data = data[size:]

    def _redirect(self) -> None:
        location = redirect_location(self._buffer.split(b"\r\n\r\n", 1)[0], self._port)
        if location:
            body = f"Moved to {location}\n".encode()
            head = (f"HTTP/1.1 301 Moved Permanently\r\nLocation: {location}\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n")
        else:
            body = b"This port speaks HTTPS: open it with https://\n"
            head = "HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain; charset=utf-8\r\n"
        head += f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        self._transport.write(head.encode("latin-1") + body)
        self._transport.close()


def keep_write_ready_on_close() -> None:
    """Work around Python 3.12.3's selector transport (Ubuntu 24.04's).

    Its close() drops the write callback (_write_ready = None) even with data
    still buffered. When that data drains, resume_writing() lets the protocol
    write again; write() sends part of it and registers a writer whose
    callback is that None, and the event loop runs it forever: "Exception in
    callback None()" hundreds of thousands of times, the panel's API all but
    stalled (BPanel on 163.61.72.88, 2026-11-11). Python 3.12.10 no longer
    drops it; keeping the callback lets the rest drain and the connection
    close as it would have. Only the asyncio loop has this transport, and
    install() below chooses that loop.
    """
    from asyncio import selector_events

    cls = selector_events._SelectorSocketTransport
    if getattr(cls, "_keeps_write_ready", False):
        return
    close = cls.close

    def close_keeping_write_ready(self):
        write_ready = getattr(self, "_write_ready", None)
        close(self)
        if getattr(self, "_write_ready", None) is None and write_ready is not None:
            self._write_ready = write_ready

    cls.close = close_keeping_write_ready
    cls._keeps_write_ready = True


def install(config) -> None:
    """Make a loaded uvicorn.Config answer plain HTTP with a redirect.

    A no-op without TLS. create_server is then given no SSL context: every
    connection starts as FirstByteProtocol, which hands TLS ones to the
    protocol class uvicorn chose, with the same context (SNI callback and all).
    """
    if config.ssl is None:
        return
    ssl_context, protocol_class = config.ssl, config.http_protocol_class
    config.ssl = None
    config.loop = "asyncio"
    keep_write_ready_on_close()
    config.http_protocol_class = partial(FirstByteProtocol, protocol_class, ssl_context, config.port)
