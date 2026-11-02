"""Property tests for the upload host's ``multipart/related`` parser.

The media part's bytes come back verbatim for any payload (CSV, NDJSON,
or binary Parquet / Avro / ORC) under any non-composite media type, when
framed the way ``google-resumable-media`` frames a multipart upload.

Any composite media type (``multipart/*``, ``message/*``) is rejected
rather than silently staging the parser's decomposition of the part.
"""

from __future__ import annotations

import json
import re
from unittest import mock

from hypothesis import assume, given, settings
from hypothesis import strategies as st
import pytest

from bqemulator.api.routes.upload import _parse_multipart_related
from bqemulator.domain.errors import InvalidQueryError

pytestmark = pytest.mark.property

# The boundary shape ``google-resumable-media`` generates.
_BOUNDARY = "===============7330845974216740156=="
_DELIMITER = f"--{_BOUNDARY}".encode()
_CONTENT_TYPE = f'multipart/related; boundary="{_BOUNDARY}"'
_ENVELOPE = {"configuration": {"load": {"sourceFormat": "PARQUET"}}}

# RFC 6838 ``restricted-name`` (lower-cased, length-capped).
_RESTRICTED_NAME = r"[a-z0-9][a-z0-9.+-]{0,15}"
_discrete_media_types = st.one_of(
    st.sampled_from(
        [
            "*/*",
            "application/octet-stream",
            "text/plain; charset=utf-8",
            "image/bmp",
            "application/x-gzip",
        ]
    ),
    st.from_regex(rf"\A{_RESTRICTED_NAME}/{_RESTRICTED_NAME}\Z").filter(
        lambda ct: ct.split("/")[0] not in {"multipart", "message"}
    ),
)

# Byte fragments that stress the parser's line handling and boundary
# detection: bare CR / LF, CRLF, NUL, high bytes, and text that starts
# like the delimiter without being a delimiter line.
_tricky_fragments = st.sampled_from(
    [b"\r", b"\n", b"\r\n", b"\x00", b"\xff", b"--", _DELIMITER + b"x", b"PAR1", b" ", b"\t"]
)
_media_bytes = st.one_of(
    st.binary(max_size=2048),
    st.lists(st.one_of(_tricky_fragments, st.binary(max_size=8)), max_size=64).map(b"".join),
)


def _frame(media: bytes, media_ct: str) -> bytes:
    """Frame ``media`` exactly as ``google-resumable-media`` does (CRLF throughout)."""
    return (
        _DELIMITER
        + b"\r\ncontent-type: application/json; charset=UTF-8\r\n\r\n"
        + json.dumps(_ENVELOPE).encode()
        + b"\r\n"
        + _DELIMITER
        + b"\r\ncontent-type: "
        + media_ct.encode()
        + b"\r\n\r\n"
        + media
        + b"\r\n"
        + _DELIMITER
        + b"--"
    )


def _has_delimiter_line(media: bytes) -> bool:
    """Return True if a line of ``media`` is a boundary delimiter.

    Such a payload is mis-framed by the client (RFC 2046 §5.1.1 requires
    the boundary to be absent from the body), so it is outside the
    parser's contract.
    """
    lines = re.split(rb"\r\n|\r|\n", media)
    return any(line.rstrip(b" \t") in (_DELIMITER, _DELIMITER + b"--") for line in lines)


@pytest.mark.parametrize("media", [b"", b"id,name\n1,a", b"PAR1\x00\r\n\xffPAR1"])
def test_frame_matches_google_resumable_media(media: bytes) -> None:
    """``_frame`` must stay byte-identical to the client library's own framing."""
    upload = pytest.importorskip("google.resumable_media._upload")
    with mock.patch.object(upload, "get_boundary", return_value=_BOUNDARY.encode()):
        expected, _ = upload.construct_multipart_request(media, _ENVELOPE, "*/*")
    assert _frame(media, "*/*") == expected


@given(media=_media_bytes, media_ct=_discrete_media_types)
@settings(max_examples=500)
def test_media_bytes_round_trip_verbatim(media: bytes, media_ct: str) -> None:
    assume(not _has_delimiter_line(media))
    envelope, parsed = _parse_multipart_related(_CONTENT_TYPE, _frame(media, media_ct))
    assert envelope == _ENVELOPE
    assert parsed == media


@given(
    maintype=st.sampled_from(["multipart", "message", "MULTIPART", "Message"]),
    subtype=st.from_regex(rf"\A{_RESTRICTED_NAME}\Z"),
    media=_media_bytes,
)
@settings(max_examples=200)
def test_composite_media_type_rejected(maintype: str, subtype: str, media: bytes) -> None:
    assume(not _has_delimiter_line(media))
    body = _frame(media, f"{maintype}/{subtype}")
    with pytest.raises(InvalidQueryError, match="composite Content-Type"):
        _parse_multipart_related(_CONTENT_TYPE, body)
