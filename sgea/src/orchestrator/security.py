from dataclasses import dataclass

import grpc
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sgea.src.orchestrator.clients import get_auth_stub
from sgea.src.orchestrator.errors import raise_http_from_grpc
from sgea.src.protos import auth_pb2

bearer_scheme = HTTPBearer(auto_error=False)

PROTO_ROLE_TO_STR = {
    auth_pb2.ADMIN: "ADMIN",
    auth_pb2.ORGANIZER: "ORGANIZER",
    auth_pb2.PARTICIPANT: "PARTICIPANT",
}


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str
    name: str
    role: str


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async with get_auth_stub() as stub:
        try:
            resp = await stub.ValidateToken(
                auth_pb2.ValidateTokenRequest(token=credentials.credentials)
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return AuthenticatedUser(
        user_id=resp.user_id,
        email=resp.email,
        name=resp.name,
        role=PROTO_ROLE_TO_STR.get(resp.role, "UNKNOWN"),
    )


def require_roles(*allowed_roles: str):
    async def role_checker(
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permissão insuficiente para executar esta operação.",
            )
        return current_user

    return role_checker
