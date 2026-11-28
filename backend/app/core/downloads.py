"""Send an already-open file as a browser download.

The panel hands downloads to the browser's own download manager, which writes
to disk as the bytes arrive, shows the size and progress, and resumes a broken
transfer. For that the response needs three things the old one lacked:

* the real Content-Length -- a 580 MB zip went out gzip-encoded on the fly,
  which strips it (application/octet-stream is kept out of GZipMiddleware);
* a filename header that cannot fail -- a name outside Latin-1, such as a
  Vietnamese one, does not encode as a header value, so it goes RFC 5987;
* byte ranges with a validator, so Chrome's Resume asks for the rest
  (`Range` + `If-Range`) instead of starting the whole file again.

The file is opened by the caller, before the route returns, and never by path
here (see file_manager.download_file_handle for why).
"""
from __future__ import annotations

import os
import re
from email.utils import formatdate
from typing import BinaryIO, Iterator, Mapping
from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse

CHUNK = 64 * 1024
_RANGE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", re.IGNORECASE)


def attachment(name: str) -> str:
    """Content-Disposition for `name`, safe for any characters it holds."""
    quoted = quote(name)
    if quoted == name:
        return f'attachment; filename="{name}"'
    return f"attachment; filename*=utf-8''{quoted}"


def _byte_range(header: str, size: int) -> tuple[int, int] | None | bool:
    """(first, last) for a single satisfiable range, None to send everything,
    False when the range cannot be satisfied (answered with 416)."""
    match = _RANGE.match(header)
    if not match:
        # Several ranges, another unit, or junk: the whole file is a valid answer.
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:
        suffix = int(last)
        if suffix == 0 or size == 0:
            return False
        return max(size - suffix, 0), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return False
    return start, end


def _read(handle: BinaryIO, length: int) -> Iterator[bytes]:
    try:
        left = length
        while left > 0:
            chunk = handle.read(min(CHUNK, left))
            if not chunk:
                # The file shrank under us. Ending short makes the server drop
                # the connection, so the browser reports a failed download
                # instead of keeping a truncated file that looks complete.
                break
            left -= len(chunk)
            yield chunk
    finally:
        handle.close()


def file_download(handle: BinaryIO, name: str, request_headers: Mapping[str, str]) -> Response:
    """The response for an open file `handle`; it is closed when done."""
    try:
        st = os.fstat(handle.fileno())
    except OSError:
        handle.close()
        raise
    size = st.st_size
    etag = f'"{st.st_mtime_ns:x}-{size:x}"'
    last_modified = formatdate(st.st_mtime, usegmt=True)
    headers = {
        "Content-Disposition": attachment(name),
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": last_modified,
    }

    wanted = None
    range_header = request_headers.get("range")
    if range_header:
        if_range = request_headers.get("if-range")
        # A resume names the copy it started from. When the file has changed
        # since, the old bytes and the new ones must not be glued together.
        if not if_range or if_range.strip() in (etag, last_modified):
            wanted = _byte_range(range_header, size)

    if wanted is False:
        handle.close()
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    if wanted is None:
        return StreamingResponse(
            _read(handle, size),
            media_type="application/octet-stream",
            headers={**headers, "Content-Length": str(size)},
        )
    start, end = wanted
    length = end - start + 1
    handle.seek(start)
    return StreamingResponse(
        _read(handle, length),
        status_code=206,
        media_type="application/octet-stream",
        headers={**headers, "Content-Length": str(length), "Content-Range": f"bytes {start}-{end}/{size}"},
    )
