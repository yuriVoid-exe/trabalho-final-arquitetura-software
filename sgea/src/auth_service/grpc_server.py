import grpc
import jwt
from sqlalchemy import select

from sgea.src.auth_service.database import async_session_maker
from sgea.src.auth_service.models import User, UserRole
from sgea.src.auth_service.security import decode_access_token
from sgea.src.protos import auth_pb2, auth_pb2_grpc

ROLE_TO_PROTO = {
    UserRole.ADMIN: auth_pb2.ADMIN,
    UserRole.ORGANIZER: auth_pb2.ORGANIZER,
    UserRole.PARTICIPANT: auth_pb2.PARTICIPANT,
}


class AuthGrpcServicer(auth_pb2_grpc.AuthServiceServicer):
    async def ValidateToken(
        self,
        request: auth_pb2.ValidateTokenRequest,
        context: grpc.aio.ServicerContext,
    ) -> auth_pb2.ValidateTokenResponse:
        if not request.token:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "Token não fornecido.",
            )

        try:
            payload = decode_access_token(request.token)
            user_id = payload.get("sub")
            if not user_id:
                await context.abort(
                    grpc.StatusCode.UNAUTHENTICATED,
                    "Token sem identificador de usuário.",
                )
        except jwt.ExpiredSignatureError:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "Token expirado.",
            )
        except jwt.InvalidTokenError:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "Token inválido.",
            )

        async with async_session_maker() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

        if not user or not user.is_active:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "Usuário inexistente ou inativo.",
            )

        return auth_pb2.ValidateTokenResponse(
            valid=True,
            user_id=user.id,
            email=user.email,
            name=user.name,
            role=ROLE_TO_PROTO.get(user.role, auth_pb2.USER_ROLE_UNSPECIFIED),
        )

    async def GetUserById(
        self,
        request: auth_pb2.GetUserByIdRequest,
        context: grpc.aio.ServicerContext,
    ) -> auth_pb2.UserResponse:
        if not request.user_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "O campo user_id é obrigatório.",
            )

        async with async_session_maker() as db:
            result = await db.execute(select(User).where(User.id == request.user_id))
            user = result.scalar_one_or_none()

        if not user:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "Usuário não encontrado.",
            )

        return auth_pb2.UserResponse(
            user_id=user.id,
            name=user.name,
            email=user.email,
            role=ROLE_TO_PROTO.get(user.role, auth_pb2.USER_ROLE_UNSPECIFIED),
            is_active=user.is_active,
        )


def create_grpc_server(port: int) -> grpc.aio.Server:
    server = grpc.aio.server()
    auth_pb2_grpc.add_AuthServiceServicer_to_server(AuthGrpcServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    return server
