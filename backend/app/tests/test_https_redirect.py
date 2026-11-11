"""http:// on the panel's HTTPS port answers with a redirect (operator,
2026-11-08: "panel.media.io.vn:2222" typed in a browser was a broken
connection). A real listener on loopback: TLS still reaches the protocol
behind it, a plain request gets a 301 to https:// on the same host and port."""
import asyncio
import datetime
import ssl
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.core import https_redirect


@pytest.fixture(scope="module")
def server_context(tmp_path_factory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "panel.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    folder = tmp_path_factory.mktemp("tls")
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(folder / "cert.pem", folder / "key.pem")
    return context


class Echo(asyncio.Protocol):
    """Stands in for uvicorn's protocol: says whether its transport is TLS."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        tls = bool(self.transport.get_extra_info("sslcontext"))
        self.transport.write(b"tls=" + str(tls).encode() + b" got=" + data)


async def _serve(server_context, client):
    config = SimpleNamespace(ssl=server_context, http_protocol_class=Echo, port=2222, loop="auto")
    https_redirect.install(config)
    assert config.ssl is None and config.loop == "asyncio"
    loop = asyncio.get_running_loop()
    server = await loop.create_server(lambda: config.http_protocol_class(config="c", _loop=loop), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        return await asyncio.wait_for(client(port), 10)
    finally:
        server.close()
        await server.wait_closed()


async def _plain(port, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    data = await reader.read()
    writer.close()
    return data


def test_plain_http_gets_a_redirect_to_https_on_the_same_host_port_and_path(server_context):
    reply = asyncio.run(_serve(server_context, lambda port: _plain(
        port, b"GET /login?next=/files HTTP/1.1\r\nHost: panel.media.io.vn:2222\r\nUser-Agent: x\r\n\r\n")))
    assert reply.startswith(b"HTTP/1.1 301 ")
    assert b"\r\nLocation: https://panel.media.io.vn:2222/login?next=/files\r\n" in reply


def test_a_host_without_a_port_gets_the_panels(server_context):
    reply = asyncio.run(_serve(server_context, lambda port: _plain(port, b"GET / HTTP/1.1\r\nHost: 203.0.113.7\r\n\r\n")))
    assert b"\r\nLocation: https://203.0.113.7:2222/\r\n" in reply


@pytest.mark.parametrize("request_bytes", [
    b"GET / HTTP/1.0\r\n\r\n",
    b"GET / HTTP/1.1\r\nHost: evil.test\r\nX: y\r\nHost: \"><script>\r\n\r\n".replace(b"evil.test", b"bad host!"),
])
def test_no_usable_host_is_a_plain_400(server_context, request_bytes):
    reply = asyncio.run(_serve(server_context, lambda port: _plain(port, request_bytes)))
    assert reply.startswith(b"HTTP/1.1 400 ") and b"Location" not in reply


def test_tls_still_reaches_the_protocol_behind_it(server_context):
    async def client(port):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname="panel.test")
        writer.write(b"hello")
        await writer.drain()
        data = await reader.read(100)
        writer.close()
        return data

    assert asyncio.run(_serve(server_context, client)) == b"tls=True got=hello"


def test_without_tls_nothing_changes():
    config = SimpleNamespace(ssl=None, http_protocol_class=Echo, port=2222, loop="auto")
    https_redirect.install(config)
    assert config.http_protocol_class is Echo and config.loop == "auto"


def test_a_transport_closed_with_data_buffered_keeps_its_write_callback(monkeypatch):
    """Python 3.12.3 (Ubuntu 24.04) dropped it on close(); the writer that
    resume_writing() then registered had a None callback, and the event loop
    ran it forever (BPanel on 163.61.72.88, 2026-11-11: hundreds of thousands
    of "Exception in callback None()", the API all but stalled)."""
    from asyncio import selector_events
    import socket

    cls = selector_events._SelectorSocketTransport

    def close_as_in_3_12_3(self):
        self._read_ready_cb = None
        self._write_ready = None
        super(cls, self).close()

    monkeypatch.setattr(cls, "close", close_as_in_3_12_3)
    monkeypatch.setattr(cls, "_keeps_write_ready", False, raising=False)
    https_redirect.keep_write_ready_on_close()
    loop = asyncio.SelectorEventLoop()
    ours, theirs = socket.socketpair()
    try:
        transport = cls(loop, ours, asyncio.Protocol())
        transport._buffer.append(b"still to send")
        transport.close()
        assert transport._write_ready is not None
    finally:
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()
        ours.close()
        theirs.close()


def test_the_https_port_applies_the_workaround(server_context):
    import inspect

    install = inspect.getsource(https_redirect.install)
    assert install.index('config.loop = "asyncio"') < install.index("keep_write_ready_on_close()")
