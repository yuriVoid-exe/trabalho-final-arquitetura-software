import grpc
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sgea.src.protos import registrations_pb2, registrations_pb2_grpc
from sgea.src.registrations_service import database, grpc_server
from sgea.src.registrations_service.database import Base
from sgea.src.registrations_service.main import app


@pytest.fixture
async def isolated_registrations_db(tmp_path, monkeypatch):
    """Cria um banco SQLite isolado por teste para o registrations_service."""
    test_db_path = tmp_path / "test_registrations.db"
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
async def registrations_client(isolated_registrations_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def registrations_stub(isolated_registrations_db):
    server = grpc.aio.server()
    registrations_pb2_grpc.add_RegistrationsServiceServicer_to_server(
        grpc_server.RegistrationsGrpcServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield registrations_pb2_grpc.RegistrationsServiceStub(channel)

    await server.stop(grace=0)


def _sample_reg_request(
    event_id: str = "ev-1",
    user_id: str = "user-1",
    user_name: str = "Maria Aluna",
    user_email: str = "maria@universidade.edu.br",
    event_title: str = "Minicurso gRPC",
) -> registrations_pb2.CreateRegistrationRequest:
    return registrations_pb2.CreateRegistrationRequest(
        event_id=event_id,
        user_id=user_id,
        user_name=user_name,
        user_email=user_email,
        event_title=event_title,
    )


async def test_registrations_health_check(
    registrations_client: httpx.AsyncClient,
):
    resp = await registrations_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "registrations_service"}


async def test_create_registration_and_duplicate_conflict(
    registrations_stub: registrations_pb2_grpc.RegistrationsServiceStub,
):
    created = await registrations_stub.CreateRegistration(_sample_reg_request())
    assert created.status == registrations_pb2.CONFIRMED
    assert created.attendance_recorded is False
    assert created.certificate_code == ""

    # Inscrição duplicada no mesmo evento -> ALREADY_EXISTS
    with pytest.raises(grpc.aio.AioRpcError) as exc_dup:
        await registrations_stub.CreateRegistration(_sample_reg_request())
    assert exc_dup.value.code() == grpc.StatusCode.ALREADY_EXISTS

    # Campos vazios -> INVALID_ARGUMENT
    with pytest.raises(grpc.aio.AioRpcError) as exc_inv:
        await registrations_stub.CreateRegistration(
            _sample_reg_request(user_email="")
        )
    assert exc_inv.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_cancel_registration_ownership_and_re_registration(
    registrations_stub: registrations_pb2_grpc.RegistrationsServiceStub,
):
    created = await registrations_stub.CreateRegistration(
        _sample_reg_request(user_id="user-dono")
    )

    # Outro usuário tentando cancelar -> PERMISSION_DENIED
    with pytest.raises(grpc.aio.AioRpcError) as exc_perm:
        await registrations_stub.CancelRegistration(
            registrations_pb2.CancelRegistrationRequest(
                registration_id=created.registration_id,
                requester_id="user-intruso",
                requester_is_admin=False,
            )
        )
    assert exc_perm.value.code() == grpc.StatusCode.PERMISSION_DENIED

    # Dono cancela -> CANCELLED
    cancelled = await registrations_stub.CancelRegistration(
        registrations_pb2.CancelRegistrationRequest(
            registration_id=created.registration_id,
            requester_id="user-dono",
            requester_is_admin=False,
        )
    )
    assert cancelled.status == registrations_pb2.CANCELLED

    # Cancelar novamente -> FAILED_PRECONDITION
    with pytest.raises(grpc.aio.AioRpcError) as exc_again:
        await registrations_stub.CancelRegistration(
            registrations_pb2.CancelRegistrationRequest(
                registration_id=created.registration_id,
                requester_id="user-dono",
                requester_is_admin=False,
            )
        )
    assert exc_again.value.code() == grpc.StatusCode.FAILED_PRECONDITION

    # Após cancelar, pode se inscrever novamente no evento
    re_created = await registrations_stub.CreateRegistration(
        _sample_reg_request(user_id="user-dono")
    )
    assert re_created.status == registrations_pb2.CONFIRMED
    assert re_created.registration_id != created.registration_id

    # Inscrição inexistente -> NOT_FOUND
    with pytest.raises(grpc.aio.AioRpcError) as exc_nf:
        await registrations_stub.GetRegistration(
            registrations_pb2.GetRegistrationRequest(registration_id="nao-existe")
        )
    assert exc_nf.value.code() == grpc.StatusCode.NOT_FOUND


async def test_list_event_and_user_registrations(
    registrations_stub: registrations_pb2_grpc.RegistrationsServiceStub,
):
    r1 = await registrations_stub.CreateRegistration(
        _sample_reg_request(event_id="ev-10", user_id="u-1")
    )
    await registrations_stub.CreateRegistration(
        _sample_reg_request(event_id="ev-10", user_id="u-2")
    )
    await registrations_stub.CancelRegistration(
        registrations_pb2.CancelRegistrationRequest(
            registration_id=r1.registration_id,
            requester_id="u-1",
            requester_is_admin=False,
        )
    )

    active_event_regs = await registrations_stub.ListEventRegistrations(
        registrations_pb2.ListEventRegistrationsRequest(
            event_id="ev-10", include_cancelled=False
        )
    )
    assert len(active_event_regs.registrations) == 1
    assert active_event_regs.registrations[0].user_id == "u-2"

    all_event_regs = await registrations_stub.ListEventRegistrations(
        registrations_pb2.ListEventRegistrationsRequest(
            event_id="ev-10", include_cancelled=True
        )
    )
    assert len(all_event_regs.registrations) == 2

    user_regs = await registrations_stub.ListUserRegistrations(
        registrations_pb2.ListUserRegistrationsRequest(
            user_id="u-1", include_cancelled=True
        )
    )
    assert len(user_regs.registrations) == 1
    assert user_regs.registrations[0].status == registrations_pb2.CANCELLED


async def test_record_attendance_and_issue_certificate(
    registrations_stub: registrations_pb2_grpc.RegistrationsServiceStub,
):
    reg = await registrations_stub.CreateRegistration(
        _sample_reg_request(user_id="u-cert")
    )

    # Sem presença registrada -> FAILED_PRECONDITION
    with pytest.raises(grpc.aio.AioRpcError) as exc_no_att:
        await registrations_stub.IssueCertificate(
            registrations_pb2.IssueCertificateRequest(
                registration_id=reg.registration_id,
                requester_id="u-cert",
                requester_is_admin=False,
            )
        )
    assert exc_no_att.value.code() == grpc.StatusCode.FAILED_PRECONDITION

    # Registra presença
    with_att = await registrations_stub.RecordAttendance(
        registrations_pb2.RecordAttendanceRequest(
            registration_id=reg.registration_id, attended=True
        )
    )
    assert with_att.attendance_recorded is True

    # Outro usuário tentando emitir -> PERMISSION_DENIED
    with pytest.raises(grpc.aio.AioRpcError) as exc_perm:
        await registrations_stub.IssueCertificate(
            registrations_pb2.IssueCertificateRequest(
                registration_id=reg.registration_id,
                requester_id="outro-usuario",
                requester_is_admin=False,
            )
        )
    assert exc_perm.value.code() == grpc.StatusCode.PERMISSION_DENIED

    # Emissão pelo dono -> sucesso e idempotência
    cert1 = await registrations_stub.IssueCertificate(
        registrations_pb2.IssueCertificateRequest(
            registration_id=reg.registration_id,
            requester_id="u-cert",
            requester_is_admin=False,
        )
    )
    assert cert1.certificate_code.startswith("SGEA-")
    assert cert1.event_title == "Minicurso gRPC"

    cert2 = await registrations_stub.IssueCertificate(
        registrations_pb2.IssueCertificateRequest(
            registration_id=reg.registration_id,
            requester_id="u-cert",
            requester_is_admin=False,
        )
    )
    assert cert2.certificate_code == cert1.certificate_code


async def test_get_registrations_summary(
    registrations_stub: registrations_pb2_grpc.RegistrationsServiceStub,
):
    r1 = await registrations_stub.CreateRegistration(
        _sample_reg_request(event_id="ev-1", user_id="u-1")
    )
    r2 = await registrations_stub.CreateRegistration(
        _sample_reg_request(event_id="ev-1", user_id="u-2")
    )

    await registrations_stub.RecordAttendance(
        registrations_pb2.RecordAttendanceRequest(
            registration_id=r1.registration_id, attended=True
        )
    )
    await registrations_stub.IssueCertificate(
        registrations_pb2.IssueCertificateRequest(
            registration_id=r1.registration_id,
            requester_id="u-1",
            requester_is_admin=False,
        )
    )
    await registrations_stub.CancelRegistration(
        registrations_pb2.CancelRegistrationRequest(
            registration_id=r2.registration_id,
            requester_id="u-2",
            requester_is_admin=False,
        )
    )

    summary = await registrations_stub.GetRegistrationsSummary(
        registrations_pb2.GetRegistrationsSummaryRequest()
    )
    assert summary.total_registrations == 2
    assert summary.confirmed_registrations == 1
    assert summary.cancelled_registrations == 1
    assert summary.total_attendances == 1
    assert summary.certificates_issued == 1
