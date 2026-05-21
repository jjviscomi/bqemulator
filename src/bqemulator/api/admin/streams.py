"""``GET /admin/streams`` — diagnostic dump of active Storage Read/Write streams.

* Write streams live in :class:`~bqemulator.streaming.write_stream.WriteStreamManager`,
  shared between the gRPC servicer and this endpoint via
  :class:`~bqemulator.api.dependencies.AppContext` (see ``server.py``).
* Read streams live in a module-level registry on
  :mod:`bqemulator.streaming.read_session`. We snapshot it without
  taking a lock — the gRPC servicer mutates it under the
  asyncio event loop, and a stale read of a few extra streams is
  acceptable for a diagnostic endpoint.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from bqemulator.api.dependencies import AppContext, get_context

router = APIRouter(tags=["admin"])

_Ctx = Annotated[AppContext, Depends(get_context)]


@router.get("/streams")
def admin_list_streams(ctx: _Ctx) -> dict[str, Any]:
    """List every Storage Read / Write stream the emulator currently tracks."""
    write_streams = [_write_stream_to_dict(s) for s in ctx.write_streams.list_active()]

    # Read sessions live behind a module-level singleton (kept that way so
    # gRPC handlers can publish to it cheaply). The admin endpoint just
    # peeks at it via the documented exposure helper.
    from bqemulator.streaming import read_session

    read_sessions = [
        _read_session_to_dict(s)
        for s in tuple(read_session._SESSIONS.values())  # noqa: SLF001
    ]

    return {
        "kind": "bqemu#adminStreamList",
        "writeStreams": write_streams,
        "writeStreamCount": len(write_streams),
        "readSessions": read_sessions,
        "readSessionCount": len(read_sessions),
    }


def _write_stream_to_dict(stream: Any) -> dict[str, Any]:
    """Render a WriteStream as a JSON-friendly dict."""
    return {
        "name": stream.name,
        "projectId": stream.project_id,
        "datasetId": stream.dataset_id,
        "tableId": stream.table_id,
        "streamType": stream.stream_type.value,
        "state": stream.state.value,
        "nextOffset": stream.next_offset,
        "rowCount": stream.row_count,
        "bufferedBatches": len(stream.buffer),
        "flushedRows": stream.flushed_rows,
    }


def _read_session_to_dict(session: Any) -> dict[str, Any]:
    """Render a ReadSessionState as a JSON-friendly dict."""
    return {
        "name": session.session_name,
        "numRows": session.table.num_rows,
        "numStreams": len(session.streams),
        "streams": [
            {"name": s.name, "startRow": s.start_row, "endRow": s.end_row} for s in session.streams
        ],
    }
