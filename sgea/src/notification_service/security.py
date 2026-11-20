from dataclasses import dataclass

import grpc
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sgea.src.notification_service.config import settings
from sgea.src.protos import auth_pb2, auth_pb2_grpc

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


async def validate_token_via_auth_grpc(token: str) -> AuthenticatedUser:
    async with grpc.aio.insecure_channel(settings.auth_grpc_target) as channel:
        stub = auth_pb2_grpc.AuthServiceStub(channel)
        try:
            resp = await stub.ValidateToken(
                auth_pb2.ValidateTokenRequest(token=token)
            )
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAUTHENTICATED:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=exc.details() or "Token inválido ou expirado.",
                )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Serviço de autenticação indisponível.",
            )

    return AuthenticatedUser(
        user_id=resp.user_id,
        email=resp.email,
        name=resp.name,
        role=PROTO_ROLE_TO_STR.get(resp.role, "UNKNOWN"),
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await validate_token_via_auth_grpc(credentials.credentials)


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
