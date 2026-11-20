import grpc
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sgea.src.notification_service import config, database, grpc_server
from sgea.src.notification_service.database import Base
from sgea.src.notification_service.main import app
from sgea.src.protos import (
    auth_pb2,
    auth_pb2_grpc,
    notifications_pb2,
    notifications_pb2_grpc,
)


class FakeAuthServicer(auth_pb2_grpc.AuthServiceServicer):
    """Simula o auth_service gRPC para testar a validação JWT/RBAC na borda SOA."""

    TOKENS = {
        "token-admin": ("admin-1", "admin@sgea.edu.br", "Admin", auth_pb2.ADMIN),
        "token-organizer": (
            "org-1",
            "org@sgea.edu.br",
            "Organizador",
            auth_pb2.ORGANIZER,
        ),
        "token-part-1": (
            "part-1",
            "part1@sgea.edu.br",
            "Participante 1",
            auth_pb2.PARTICIPANT,
        ),
        "token-part-2": (
            "part-2",
            "part2@sgea.edu.br",
            "Participante 2",
            auth_pb2.PARTICIPANT,
        ),
    }

    async def ValidateToken(self, request, context):
        if request.token not in self.TOKENS:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED, "Token inválido ou expirado."
            )
        user_id, email, name, role = self.TOKENS[request.token]
        return auth_pb2.ValidateTokenResponse(
            valid=True,
            user_id=user_id,
            email=email,
            name=name,
            role=role,
        )


@pytest.fixture
async def isolated_notifications_db(tmp_path, monkeypatch):
    """Cria banco SQLite isolado por teste para o notification_service."""
    test_db_path = tmp_path / "test_notifications.db"
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{test_db_path}", echo=False
    )
    test_session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    monkeypatch.setattr(database, "async_session_maker", test_session_maker)
    monkeypatch.setattr(grpc_server, "async_session_maker", test_session_maker)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield test_session_maker

    await test_engine.dispose()


@pytest.fixture
async def fake_auth_grpc(monkeypatch):
    """Sobe um servidor gRPC de autenticação fake e aponta o notification_service para ele."""
    server = grpc.aio.server()
    auth_pb2_grpc.add_AuthServiceServicer_to_server(FakeAuthServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    monkeypatch.setattr(config.settings, "auth_grpc_target", f"127.0.0.1:{port}")
    yield port

    await server.stop(grace=0)


@pytest.fixture
async def notif_client(isolated_notifications_db, fake_auth_grpc):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def notif_stub(isolated_notifications_db):
    server = grpc.aio.server()
    notifications_pb2_grpc.add_NotificationServiceServicer_to_server(
        grpc_server.NotificationGrpcServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield notifications_pb2_grpc.NotificationServiceStub(channel)

    await server.stop(grace=0)


# ==============================================================================
# TESTES REST / OPENAPI / RBAC (SOA)
# ==============================================================================


async def test_notification_health_check(notif_client: httpx.AsyncClient):
    resp = await notif_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "notification_service"}


async def test_rest_send_notification_auth_and_rbac(
    notif_client: httpx.AsyncClient,
):
    payload = {
        "recipient_id": "part-1",
        "recipient_email": "part1@sgea.edu.br",
        "recipient_name": "Participante 1",
        "subject": "Aviso Acadêmico",
        "message": "Sua matrícula foi atualizada.",
        "channel": "EMAIL",
        "source_system": "PORTAL_ALUNO",
    }

    # Sem token -> 401
    resp_no_auth = await notif_client.post("/notifications", json=payload)
    assert resp_no_auth.status_code == 401

    # Token inválido -> 401
    resp_bad_auth = await notif_client.post(
        "/notifications",
        json=payload,
        headers={"Authorization": "Bearer token-falso"},
    )
    assert resp_bad_auth.status_code == 401

    # Participante não pode disparar notificações corporativas -> 403
    resp_part = await notif_client.post(
        "/notifications",
        json=payload,
        headers={"Authorization": "Bearer token-part-1"},
    )
    assert resp_part.status_code == 403

    # Organizador pode disparar -> 201 Created
    resp_org = await notif_client.post(
        "/notifications",
        json=payload,
        headers={"Authorization": "Bearer token-organizer"},
    )
    assert resp_org.status_code == 201
    data = resp_org.json()
    assert data["status"] == "SENT"
    assert data["source_system"] == "PORTAL_ALUNO"

    # Payload inválido -> 400 Bad Request
    resp_invalid = await notif_client.post(
        "/notifications",
        json={"recipient_id": "part-1", "recipient_email": "invalido"},
        headers={"Authorization": "Bearer token-admin"},
    )
    assert resp_invalid.status_code == 400


async def test_rest_list_and_get_notifications_rbac(
    notif_client: httpx.AsyncClient,
):
    admin_headers = {"Authorization": "Bearer token-admin"}
    part1_headers = {"Authorization": "Bearer token-part-1"}

    r1 = await notif_client.post(
        "/notifications",
        headers=admin_headers,
        json={
            "recipient_id": "part-1",
            "recipient_email": "part1@sgea.edu.br",
            "recipient_name": "Participante 1",
            "subject": "Notif 1",
            "message": "Msg 1",
        },
    )
    r2 = await notif_client.post(
        "/notifications",
        headers=admin_headers,
        json={
            "recipient_id": "part-2",
            "recipient_email": "part2@sgea.edu.br",
            "recipient_name": "Participante 2",
            "subject": "Notif 2",
            "message": "Msg 2",
        },
    )
    notif1_id = r1.json()["id"]
    notif2_id = r2.json()["id"]

    # Participante não lista notificações globais -> 403
    assert (await notif_client.get("/notifications", headers=part1_headers)).status_code == 403

    # Admin lista todas -> 200 (2 itens)
    all_resp = await notif_client.get("/notifications", headers=admin_headers)
    assert all_resp.status_code == 200
    assert len(all_resp.json()) == 2

    # Participante 1 vê as próprias -> 200
    own_resp = await notif_client.get(
        "/notifications/recipient/part-1", headers=part1_headers
    )
    assert own_resp.status_code == 200
    assert len(own_resp.json()) == 1

    # Participante 1 tentando ver de Participante 2 -> 403
    other_resp = await notif_client.get(
        "/notifications/recipient/part-2", headers=part1_headers
    )
    assert other_resp.status_code == 403

    # Acesso por ID
    assert (
        await notif_client.get(f"/notifications/{notif1_id}", headers=part1_headers)
    ).status_code == 200
    assert (
        await notif_client.get(f"/notifications/{notif2_id}", headers=part1_headers)
    ).status_code == 403
    assert (
        await notif_client.get("/notifications/nao-existe", headers=admin_headers)
    ).status_code == 404


# ==============================================================================
# TESTES gRPC (NotificationService)
# ==============================================================================


async def test_grpc_send_and_list_notifications(
    notif_stub: notifications_pb2_grpc.NotificationServiceStub,
):
    sent = await notif_stub.SendNotification(
        notifications_pb2.SendNotificationRequest(
            recipient_id="u-1",
            recipient_email="u1@sgea.edu.br",
            recipient_name="Usuário Um",
            subject="Inscrição Confirmada",
            message="Você foi inscrito na Semana de Computação.",
            channel=notifications_pb2.EMAIL,
            source_system="SGEA",
        )
    )
    assert sent.status == notifications_pb2.SENT
    assert sent.channel == notifications_pb2.EMAIL
    assert sent.source_system == "SGEA"

    listed = await notif_stub.ListNotificationsByRecipient(
        notifications_pb2.ListNotificationsByRecipientRequest(recipient_id="u-1")
    )
    assert len(listed.notifications) == 1
    assert listed.notifications[0].notification_id == sent.notification_id


async def test_grpc_send_notification_validation_errors(
    notif_stub: notifications_pb2_grpc.NotificationServiceStub,
):
    with pytest.raises(grpc.aio.AioRpcError) as exc_empty:
        await notif_stub.SendNotification(
            notifications_pb2.SendNotificationRequest(
                recipient_id="u-1",
                recipient_email="u1@sgea.edu.br",
                recipient_name="Usuário Um",
                subject="",
                message="Msg",
                channel=notifications_pb2.EMAIL,
            )
        )
    assert exc_empty.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    with pytest.raises(grpc.aio.AioRpcError) as exc_channel:
        await notif_stub.SendNotification(
            notifications_pb2.SendNotificationRequest(
                recipient_id="u-1",
                recipient_email="u1@sgea.edu.br",
                recipient_name="Usuário Um",
                subject="Assunto",
                message="Msg",
                channel=notifications_pb2.NOTIFICATION_CHANNEL_UNSPECIFIED,
            )
        )
    assert exc_channel.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    with pytest.raises(grpc.aio.AioRpcError) as exc_list:
        await notif_stub.ListNotificationsByRecipient(
            notifications_pb2.ListNotificationsByRecipientRequest(recipient_id="")
        )
    assert exc_list.value.code() == grpc.StatusCode.INVALID_ARGUMENT
