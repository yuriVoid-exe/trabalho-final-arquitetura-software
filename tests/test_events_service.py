import grpc
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sgea.src.events_service import database, grpc_server
from sgea.src.events_service.database import Base
from sgea.src.events_service.main import app
from sgea.src.protos import events_pb2, events_pb2_grpc


@pytest.fixture
async def isolated_events_db(tmp_path, monkeypatch):
    """Cria um banco SQLite isolado por teste para o events_service."""
    test_db_path = tmp_path / "test_events.db"
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
async def events_client(isolated_events_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def events_stub(isolated_events_db):
    server = grpc.aio.server()
    events_pb2_grpc.add_EventsServiceServicer_to_server(
        grpc_server.EventsGrpcServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield events_pb2_grpc.EventsServiceStub(channel)

    await server.stop(grace=0)


def _sample_create_request(
    title: str = "Semana de Arquitetura",
    event_type: events_pb2.EventType = events_pb2.ACADEMIC_WEEK,
    capacity: int = 50,
    organizer_id: str = "org-1",
) -> events_pb2.CreateEventRequest:
    return events_pb2.CreateEventRequest(
        title=title,
        description="Evento acadêmico sobre sistemas distribuídos.",
        event_type=event_type,
        location="Auditório Central",
        start_time="2026-12-10T14:00:00Z",
        end_time="2026-12-10T18:00:00Z",
        capacity=capacity,
        organizer_id=organizer_id,
    )


async def test_events_health_check(events_client: httpx.AsyncClient):
    resp = await events_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "events_service"}


async def test_create_event_and_validation_errors(
    events_stub: events_pb2_grpc.EventsServiceStub,
):
    created = await events_stub.CreateEvent(_sample_create_request())
    assert created.title == "Semana de Arquitetura"
    assert created.status == events_pb2.PENDING
    assert created.capacity == 50
    assert created.occupied_seats == 0
    assert created.available_seats == 50

    # Capacidade inválida -> INVALID_ARGUMENT
    with pytest.raises(grpc.aio.AioRpcError) as exc_cap:
        await events_stub.CreateEvent(_sample_create_request(capacity=0))
    assert exc_cap.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    # Data de fim anterior à data de início -> INVALID_ARGUMENT
    bad_dates = events_pb2.CreateEventRequest(
        title="Palestra Inválida",
        description="Teste",
        event_type=events_pb2.LECTURE,
        location="Sala 101",
        start_time="2026-12-10T18:00:00Z",
        end_time="2026-12-10T14:00:00Z",
        capacity=20,
        organizer_id="org-1",
    )
    with pytest.raises(grpc.aio.AioRpcError) as exc_dates:
        await events_stub.CreateEvent(bad_dates)
    assert exc_dates.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_update_event_ownership_and_admin_override(
    events_stub: events_pb2_grpc.EventsServiceStub,
):
    created = await events_stub.CreateEvent(
        _sample_create_request(organizer_id="org-dono")
    )

    # Outro organizador tentando editar -> PERMISSION_DENIED
    with pytest.raises(grpc.aio.AioRpcError) as exc_perm:
        await events_stub.UpdateEvent(
            events_pb2.UpdateEventRequest(
                event_id=created.event_id,
                requester_id="org-intruso",
                requester_is_admin=False,
                title="Título Alterado",
                description="Desc",
                event_type=events_pb2.LECTURE,
                location="Sala 2",
                start_time="2026-12-10T14:00:00Z",
                end_time="2026-12-10T16:00:00Z",
                capacity=30,
            )
        )
    assert exc_perm.value.code() == grpc.StatusCode.PERMISSION_DENIED

    # Dono editando -> OK
    updated = await events_stub.UpdateEvent(
        events_pb2.UpdateEventRequest(
            event_id=created.event_id,
            requester_id="org-dono",
            requester_is_admin=False,
            title="Semana Atualizada",
            description="Nova descrição",
            event_type=events_pb2.ACADEMIC_WEEK,
            location="Auditório B",
            start_time="2026-12-10T14:00:00Z",
            end_time="2026-12-10T18:00:00Z",
            capacity=60,
        )
    )
    assert updated.title == "Semana Atualizada"
    assert updated.capacity == 60

    # Admin pode editar qualquer evento -> OK
    admin_updated = await events_stub.UpdateEvent(
        events_pb2.UpdateEventRequest(
            event_id=created.event_id,
            requester_id="admin-1",
            requester_is_admin=True,
            title="Semana Revisada pelo Admin",
            description="Nova descrição",
            event_type=events_pb2.ACADEMIC_WEEK,
            location="Auditório B",
            start_time="2026-12-10T14:00:00Z",
            end_time="2026-12-10T18:00:00Z",
            capacity=60,
        )
    )
    assert admin_updated.title == "Semana Revisada pelo Admin"

    # Evento inexistente -> NOT_FOUND
    with pytest.raises(grpc.aio.AioRpcError) as exc_nf:
        await events_stub.GetEvent(
            events_pb2.GetEventRequest(event_id="inexistente")
        )
    assert exc_nf.value.code() == grpc.StatusCode.NOT_FOUND


async def test_review_and_list_events_with_filters(
    events_stub: events_pb2_grpc.EventsServiceStub,
):
    ev1 = await events_stub.CreateEvent(
        _sample_create_request(
            title="Palestra gRPC",
            event_type=events_pb2.LECTURE,
            organizer_id="org-1",
        )
    )
    ev2 = await events_stub.CreateEvent(
        _sample_create_request(
            title="Minicurso FastAPI",
            event_type=events_pb2.MINICOURSE,
            organizer_id="org-2",
        )
    )

    approved = await events_stub.ReviewEvent(
        events_pb2.ReviewEventRequest(event_id=ev1.event_id, approve=True)
    )
    assert approved.status == events_pb2.APPROVED

    rejected = await events_stub.ReviewEvent(
        events_pb2.ReviewEventRequest(event_id=ev2.event_id, approve=False)
    )
    assert rejected.status == events_pb2.REJECTED

    only_approved = await events_stub.ListEvents(
        events_pb2.ListEventsRequest(status_filter=events_pb2.APPROVED)
    )
    assert len(only_approved.events) == 1
    assert only_approved.events[0].event_id == ev1.event_id

    only_minicourses = await events_stub.ListEvents(
        events_pb2.ListEventsRequest(event_type_filter=events_pb2.MINICOURSE)
    )
    assert len(only_minicourses.events) == 1
    assert only_minicourses.events[0].event_id == ev2.event_id

    by_org1 = await events_stub.ListEvents(
        events_pb2.ListEventsRequest(organizer_id="org-1")
    )
    assert len(by_org1.events) == 1


async def test_reserve_and_release_seat_rules(
    events_stub: events_pb2_grpc.EventsServiceStub,
):
    ev = await events_stub.CreateEvent(_sample_create_request(capacity=1))

    # Não pode reservar vaga em evento ainda PENDING -> FAILED_PRECONDITION
    with pytest.raises(grpc.aio.AioRpcError) as exc_pending:
        await events_stub.ReserveSeat(
            events_pb2.SeatOperationRequest(event_id=ev.event_id)
        )
    assert exc_pending.value.code() == grpc.StatusCode.FAILED_PRECONDITION

    # Aprova e reserva a única vaga
    await events_stub.ReviewEvent(
        events_pb2.ReviewEventRequest(event_id=ev.event_id, approve=True)
    )
    reserved = await events_stub.ReserveSeat(
        events_pb2.SeatOperationRequest(event_id=ev.event_id)
    )
    assert reserved.occupied_seats == 1
    assert reserved.available_seats == 0

    # Tentar reservar quando lotado -> FAILED_PRECONDITION
    with pytest.raises(grpc.aio.AioRpcError) as exc_full:
        await events_stub.ReserveSeat(
            events_pb2.SeatOperationRequest(event_id=ev.event_id)
        )
    assert exc_full.value.code() == grpc.StatusCode.FAILED_PRECONDITION

    # Liberar vaga devolve disponibilidade
    released = await events_stub.ReleaseSeat(
        events_pb2.SeatOperationRequest(event_id=ev.event_id)
    )
    assert released.occupied_seats == 0
    assert released.available_seats == 1

    # Evento inexistente -> NOT_FOUND
    with pytest.raises(grpc.aio.AioRpcError) as exc_nf:
        await events_stub.ReserveSeat(
            events_pb2.SeatOperationRequest(event_id="nao-existe")
        )
    assert exc_nf.value.code() == grpc.StatusCode.NOT_FOUND


async def test_get_events_summary(events_stub: events_pb2_grpc.EventsServiceStub):
    ev1 = await events_stub.CreateEvent(_sample_create_request(capacity=10))
    ev2 = await events_stub.CreateEvent(_sample_create_request(capacity=20))
    await events_stub.CreateEvent(_sample_create_request(capacity=30))  # PENDING

    await events_stub.ReviewEvent(
        events_pb2.ReviewEventRequest(event_id=ev1.event_id, approve=True)
    )
    await events_stub.ReserveSeat(
        events_pb2.SeatOperationRequest(event_id=ev1.event_id)
    )
    await events_stub.ReviewEvent(
        events_pb2.ReviewEventRequest(event_id=ev2.event_id, approve=False)
    )

    summary = await events_stub.GetEventsSummary(
        events_pb2.GetEventsSummaryRequest()
    )
    assert summary.total_events == 3
    assert summary.pending_events == 1
    assert summary.approved_events == 1
    assert summary.rejected_events == 1
    assert summary.total_capacity == 60
    assert summary.total_occupied_seats == 1
