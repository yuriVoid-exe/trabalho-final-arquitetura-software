import grpc
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sgea.src.auth_service import (
    database as auth_db,
    grpc_server as auth_grpc,
    main as auth_main,
)
from sgea.src.events_service import database as events_db, grpc_server as events_grpc
from sgea.src.notification_service import (
    database as notif_db,
    grpc_server as notif_grpc,
)
from sgea.src.orchestrator import config as orch_config, main as orch_main
from sgea.src.protos import (
    auth_pb2_grpc,
    events_pb2_grpc,
    notifications_pb2_grpc,
    registrations_pb2_grpc,
)
from sgea.src.registrations_service import (
    database as reg_db,
    grpc_server as reg_grpc,
)


@pytest.fixture
async def sgea_mesh(tmp_path, monkeypatch):
    """Sobe os 4 serviços gRPC reais isolados e conecta o Orquestrador a eles."""
    # 1. Bancos SQLite isolados por serviço
    auth_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    events_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'events.db'}")
    reg_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'regs.db'}")
    notif_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'notifs.db'}")

    auth_sm = async_sessionmaker(auth_engine, class_=AsyncSession, expire_on_commit=False)
    events_sm = async_sessionmaker(events_engine, class_=AsyncSession, expire_on_commit=False)
    reg_sm = async_sessionmaker(reg_engine, class_=AsyncSession, expire_on_commit=False)
    notif_sm = async_sessionmaker(notif_engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(auth_db, "async_session_maker", auth_sm)
    monkeypatch.setattr(auth_main, "async_session_maker", auth_sm)
    monkeypatch.setattr(auth_grpc, "async_session_maker", auth_sm)

    monkeypatch.setattr(events_db, "async_session_maker", events_sm)
    monkeypatch.setattr(events_grpc, "async_session_maker", events_sm)

    monkeypatch.setattr(reg_db, "async_session_maker", reg_sm)
    monkeypatch.setattr(reg_grpc, "async_session_maker", reg_sm)

    monkeypatch.setattr(notif_db, "async_session_maker", notif_sm)
    monkeypatch.setattr(notif_grpc, "async_session_maker", notif_sm)

    for engine, base in (
        (auth_engine, auth_db.Base),
        (events_engine, events_db.Base),
        (reg_engine, reg_db.Base),
        (notif_engine, notif_db.Base),
    ):
        async with engine.begin() as conn:
            await conn.run_sync(base.metadata.create_all)

    await auth_main.seed_default_admin()

    # 2. Servidores gRPC reais em portas efêmeras
    auth_srv = grpc.aio.server()
    auth_pb2_grpc.add_AuthServiceServicer_to_server(auth_grpc.AuthGrpcServicer(), auth_srv)
    auth_port = auth_srv.add_insecure_port("127.0.0.1:0")

    events_srv = grpc.aio.server()
    events_pb2_grpc.add_EventsServiceServicer_to_server(events_grpc.EventsGrpcServicer(), events_srv)
    events_port = events_srv.add_insecure_port("127.0.0.1:0")

    reg_srv = grpc.aio.server()
    registrations_pb2_grpc.add_RegistrationsServiceServicer_to_server(
        reg_grpc.RegistrationsGrpcServicer(), reg_srv
    )
    reg_port = reg_srv.add_insecure_port("127.0.0.1:0")

    notif_srv = grpc.aio.server()
    notifications_pb2_grpc.add_NotificationServiceServicer_to_server(
        notif_grpc.NotificationGrpcServicer(), notif_srv
    )
    notif_port = notif_srv.add_insecure_port("127.0.0.1:0")

    await auth_srv.start()
    await events_srv.start()
    await reg_srv.start()
    await notif_srv.start()

    monkeypatch.setattr(orch_config.settings, "auth_grpc_target", f"127.0.0.1:{auth_port}")
    monkeypatch.setattr(orch_config.settings, "events_grpc_target", f"127.0.0.1:{events_port}")
    monkeypatch.setattr(orch_config.settings, "registrations_grpc_target", f"127.0.0.1:{reg_port}")
    monkeypatch.setattr(orch_config.settings, "notification_grpc_target", f"127.0.0.1:{notif_port}")

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=auth_main.app),
            base_url="http://auth",
        ) as auth_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=orch_main.app),
            base_url="http://orchestrator",
        ) as orch_client,
    ):
        yield auth_client, orch_client

    await auth_srv.stop(grace=0)
    await events_srv.stop(grace=0)
    await reg_srv.stop(grace=0)
    await notif_srv.stop(grace=0)

    await auth_engine.dispose()
    await events_engine.dispose()
    await reg_engine.dispose()
    await notif_engine.dispose()


async def _login(auth_client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await auth_client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


async def test_orchestrator_health_and_error_mapping(sgea_mesh):
    auth_client, orch_client = sgea_mesh
    health = await orch_client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "orchestrator"}

    admin_token = await _login(auth_client, "admin@sgea.edu.br", "admin123")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Rota protegida sem token -> 401
    assert (await orch_client.get("/api/v1/events")).status_code == 401

    # Payload inválido -> 400
    bad_payload = await orch_client.post(
        "/api/v1/events", headers=headers, json={"title": "A"}
    )
    assert bad_payload.status_code == 400

    # Recurso inexistente -> 404
    not_found = await orch_client.get("/api/v1/events/nao-existe", headers=headers)
    assert not_found.status_code == 404


async def test_full_e2e_academic_event_lifecycle(sgea_mesh):
    auth_client, orch_client = sgea_mesh

    # 1. Setup de usuários (Admin, Organizador, 3 Participantes)
    admin_token = await _login(auth_client, "admin@sgea.edu.br", "admin123")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    create_org = await auth_client.post(
        "/users",
        headers=admin_headers,
        json={
            "name": "Prof. Organizador",
            "email": "org@sgea.edu.br",
            "password": "senha123",
            "role": "ORGANIZER",
        },
    )
    assert create_org.status_code == 201
    org_token = await _login(auth_client, "org@sgea.edu.br", "senha123")
    org_headers = {"Authorization": f"Bearer {org_token}"}

    part_tokens = []
    for idx in (1, 2, 3):
        reg_user = await auth_client.post(
            "/auth/register",
            json={
                "name": f"Participante {idx}",
                "email": f"part{idx}@sgea.edu.br",
                "password": "senha123",
            },
        )
        assert reg_user.status_code == 201
        tok = await _login(auth_client, f"part{idx}@sgea.edu.br", "senha123")
        part_tokens.append({"Authorization": f"Bearer {tok}"})

    p1_headers, p2_headers, p3_headers = part_tokens

    # 2. Criação e aprovação de evento
    event_payload = {
        "title": "Semana de Arquitetura de Software",
        "description": "Palestras sobre SOA, Microsserviços e gRPC.",
        "event_type": "ACADEMIC_WEEK",
        "location": "Auditório Principal",
        "start_time": "2026-12-15T14:00:00Z",
        "end_time": "2026-12-15T18:00:00Z",
        "capacity": 2,
    }

    # Participante não cria evento -> 403
    assert (
        await orch_client.post("/api/v1/events", headers=p1_headers, json=event_payload)
    ).status_code == 403

    # Organizador cria evento -> 201 (PENDING)
    ev_resp = await orch_client.post(
        "/api/v1/events", headers=org_headers, json=event_payload
    )
    assert ev_resp.status_code == 201
    event_id = ev_resp.json()["event_id"]
    assert ev_resp.json()["status"] == "PENDING"

    # Participante não enxerga evento ainda PENDING
    assert (
        await orch_client.get("/api/v1/events", headers=p1_headers)
    ).json() == []
    assert (
        await orch_client.get(f"/api/v1/events/{event_id}", headers=p1_headers)
    ).status_code == 403

    # Organizador não aprova próprio evento -> 403
    assert (
        await orch_client.patch(
            f"/api/v1/events/{event_id}/review",
            headers=org_headers,
            json={"approve": True},
        )
    ).status_code == 403

    # Admin aprova evento -> 200
    approved = await orch_client.patch(
        f"/api/v1/events/{event_id}/review",
        headers=admin_headers,
        json={"approve": True},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"

    # Participante passa a enxergar o evento aprovado
    visible_events = (
        await orch_client.get("/api/v1/events", headers=p1_headers)
    ).json()
    assert len(visible_events) == 1

    # 3. Fluxo orquestrado de inscrição + compensação Saga + lotação
    reg1_resp = await orch_client.post(
        "/api/v1/registrations",
        headers=p1_headers,
        json={"event_id": event_id},
    )
    assert reg1_resp.status_code == 201
    reg1_data = reg1_resp.json()
    reg1_id = reg1_data["registration"]["registration_id"]
    assert reg1_data["registration"]["status"] == "CONFIRMED"
    assert reg1_data["notification_status"] == "SENT"
    assert reg1_data["notification_id"] != ""

    # Participante 1 recebeu a notificação gerada pelo fluxo orquestrado
    notifs_p1 = (
        await orch_client.get("/api/v1/notifications/me", headers=p1_headers)
    ).json()
    assert len(notifs_p1) == 1
    assert "Inscrição Confirmada" in notifs_p1[0]["subject"]

    # Duplicidade: Participante 1 tenta se inscrever de novo -> 409 Conflict
    # E a compensação Saga DEVE devolver a vaga reservada (mantendo occupied_seats = 1)
    dup_reg = await orch_client.post(
        "/api/v1/registrations",
        headers=p1_headers,
        json={"event_id": event_id},
    )
    assert dup_reg.status_code == 409

    ev_check = (
        await orch_client.get(f"/api/v1/events/{event_id}", headers=p1_headers)
    ).json()
    assert ev_check["occupied_seats"] == 1
    assert ev_check["available_seats"] == 1

    # Participante 2 preenche a 2ª e última vaga
    reg2_resp = await orch_client.post(
        "/api/v1/registrations",
        headers=p2_headers,
        json={"event_id": event_id},
    )
    assert reg2_resp.status_code == 201
    reg2_id = reg2_resp.json()["registration"]["registration_id"]

    # Participante 3 tenta se inscrever em evento lotado -> 409 Conflict
    full_resp = await orch_client.post(
        "/api/v1/registrations",
        headers=p3_headers,
        json={"event_id": event_id},
    )
    assert full_resp.status_code == 409

    # 4. Lista de inscritos e Presença (Organizador)
    assert (
        await orch_client.get(
            f"/api/v1/events/{event_id}/registrations", headers=p1_headers
        )
    ).status_code == 403

    event_regs = (
        await orch_client.get(
            f"/api/v1/events/{event_id}/registrations", headers=org_headers
        )
    ).json()
    assert len(event_regs) == 2

    # Certificado antes da presença -> 409 Conflict
    assert (
        await orch_client.get(
            f"/api/v1/registrations/{reg1_id}/certificate", headers=p1_headers
        )
    ).status_code == 409

    # Organizador registra presença do Participante 1
    att_resp = await orch_client.patch(
        f"/api/v1/registrations/{reg1_id}/attendance",
        headers=org_headers,
        json={"attended": True},
    )
    assert att_resp.status_code == 200
    assert att_resp.json()["attendance_recorded"] is True

    # 5. Emissão de Certificado (Participante)
    # Participante 2 não pode emitir certificado de Participante 1 -> 403
    assert (
        await orch_client.get(
            f"/api/v1/registrations/{reg1_id}/certificate", headers=p2_headers
        )
    ).status_code == 403

    cert_resp = await orch_client.get(
        f"/api/v1/registrations/{reg1_id}/certificate", headers=p1_headers
    )
    assert cert_resp.status_code == 200
    assert cert_resp.json()["certificate_code"].startswith("SGEA-")

    # 6. Cancelamento Orquestrado (libera vaga + notifica)
    cancel_resp = await orch_client.delete(
        f"/api/v1/registrations/{reg2_id}", headers=p2_headers
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    ev_after_cancel = (
        await orch_client.get(f"/api/v1/events/{event_id}", headers=p1_headers)
    ).json()
    assert ev_after_cancel["occupied_seats"] == 1
    assert ev_after_cancel["available_seats"] == 1

    notifs_p2 = (
        await orch_client.get("/api/v1/notifications/me", headers=p2_headers)
    ).json()
    assert any("Cancelada" in n["subject"] for n in notifs_p2)

    # 7. Relatório Geral Consolidado (apenas Admin)
    assert (
        await orch_client.get("/api/v1/reports/summary", headers=org_headers)
    ).status_code == 403

    report = await orch_client.get("/api/v1/reports/summary", headers=admin_headers)
    assert report.status_code == 200
    summary = report.json()
    assert summary["total_events"] == 1
    assert summary["approved_events"] == 1
    assert summary["total_capacity"] == 2
    assert summary["total_occupied_seats"] == 1
    assert summary["total_registrations"] == 2
    assert summary["confirmed_registrations"] == 1
    assert summary["cancelled_registrations"] == 1
    assert summary["total_attendances"] == 1
    assert summary["certificates_issued"] == 1
