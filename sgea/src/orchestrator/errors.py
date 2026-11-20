from typing import NoReturn

import grpc
from fastapi import HTTPException, status

GRPC_TO_HTTP_STATUS: dict[grpc.StatusCode, int] = {
    grpc.StatusCode.INVALID_ARGUMENT: status.HTTP_400_BAD_REQUEST,
    grpc.StatusCode.UNAUTHENTICATED: status.HTTP_401_UNAUTHORIZED,
    grpc.StatusCode.PERMISSION_DENIED: status.HTTP_403_FORBIDDEN,
    grpc.StatusCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    grpc.StatusCode.ALREADY_EXISTS: status.HTTP_409_CONFLICT,
    grpc.StatusCode.FAILED_PRECONDITION: status.HTTP_409_CONFLICT,
    grpc.StatusCode.RESOURCE_EXHAUSTED: status.HTTP_409_CONFLICT,
    grpc.StatusCode.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED: status.HTTP_504_GATEWAY_TIMEOUT,
}


def raise_http_from_grpc(exc: grpc.aio.AioRpcError) -> NoReturn:
    http_status = GRPC_TO_HTTP_STATUS.get(
        exc.code(), status.HTTP_502_BAD_GATEWAY
    )
    detail = exc.details() or "Erro na comunicação com microsserviço interno."
    raise HTTPException(status_code=http_status, detail=detail)
