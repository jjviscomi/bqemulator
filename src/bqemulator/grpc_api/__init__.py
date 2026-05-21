"""gRPC adapter — BigQuery Storage Read / Write APIs.

Generated protobuf stubs live under :mod:`bqemulator.grpc_api.proto`.
Servicers (:mod:`bqemulator.grpc_api.read_servicer`,
:mod:`bqemulator.grpc_api.write_servicer`) translate gRPC calls into
domain operations.
"""

from __future__ import annotations

from bqemulator.grpc_api.server import build_grpc_server

__all__ = ["build_grpc_server"]
