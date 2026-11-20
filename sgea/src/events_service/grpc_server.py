from datetime import datetime, timezone

import grpc
from sqlalchemy import select, update

from sgea.src.events_service.database import async_session_maker
from sgea.src.events_service.models import Event, EventStatus, EventType
from sgea.src.protos import events_pb2, events_pb2_grpc

PROTO_TO_EVENT_TYPE = {
    events_pb2.ACADEMIC_WEEK: EventType.ACADEMIC_WEEK,
    events_pb2.LECTURE: EventType.LECTURE,
    events_pb2.MINICOURSE: EventType.MINICOURSE,
    events_pb2.WORKSHOP: EventType.WORKSHOP,
}
EVENT_TYPE_TO_PROTO = {v: k for k, v in PROTO_TO_EVENT_TYPE.items()}

PROTO_TO_EVENT_STATUS = {
    events_pb2.PENDING: EventStatus.PENDING,
    events_pb2.APPROVED: EventStatus.APPROVED,
    events_pb2.REJECTED: EventStatus.REJECTED,
    events_pb2.CANCELLED: EventStatus.CANCELLED,
}
EVENT_STATUS_TO_PROTO = {v: k for k, v in PROTO_TO_EVENT_STATUS.items()}


def _parse_iso_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_event_response(event: Event) -> events_pb2.EventResponse:
    return events_pb2.EventResponse(
        event_id=event.id,
        title=event.title,
        description=event.description,
        event_type=EVENT_TYPE_TO_PROTO.get(
            event.event_type, events_pb2.EVENT_TYPE_UNSPECIFIED
        ),
        location=event.location,
        start_time=event.start_time.isoformat(),
        end_time=event.end_time.isoformat(),
        capacity=event.capacity,
        occupied_seats=event.occupied_seats,
        available_seats=max(0, event.capacity - event.occupied_seats),
        organizer_id=event.organizer_id,
        status=EVENT_STATUS_TO_PROTO.get(
            event.status, events_pb2.EVENT_STATUS_UNSPECIFIED
        ),
        created_at=event.created_at.isoformat(),
    )


class EventsGrpcServicer(events_pb2_grpc.EventsServiceServicer):
    async def CreateEvent(
        self,
        request: events_pb2.CreateEventRequest,
        context: grpc.aio.ServicerContext,
    ) -> events_pb2.EventResponse:
        if not request.title.strip() or not request.location.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Título e local do evento são obrigatórios.",
            )
        if not request.organizer_id.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "organizer_id é obrigatório.",
            )
        if request.event_type not in PROTO_TO_EVENT_TYPE:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Tipo de evento inválido.",
            )
        if request.capacity <= 0:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "A capacidade do evento deve ser maior que zero.",
            )

        try:
            start_dt = _parse_iso_datetime(request.start_time)
            end_dt = _parse_iso_datetime(request.end_time)
        except ValueError:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Datas devem estar no formato ISO-8601 válido.",
            )

        if end_dt <= start_dt:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "A data de término deve ser posterior à data de início.",
            )

        event = Event(
            title=request.title.strip(),
            description=request.description.strip(),
            event_type=PROTO_TO_EVENT_TYPE[request.event_type],
            location=request.location.strip(),
            start_time=start_dt,
            end_time=end_dt,
            capacity=request.capacity,
            occupied_seats=0,
            organizer_id=request.organizer_id.strip(),
            status=EventStatus.PENDING,
        )

        async with async_session_maker() as db:
            db.add(event)
            await db.commit()
            await db.refresh(event)

        return _to_event_response(event)

    async def UpdateEvent(
        self,
        request: events_pb2.UpdateEventRequest,
        context: grpc.aio.ServicerContext,
    ) -> events_pb2.EventResponse:
        if not request.event_id or not request.requester_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "event_id e requester_id são obrigatórios.",
            )
        if not request.title.strip() or not request.location.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Título e local do evento são obrigatórios.",
            )
        if request.event_type not in PROTO_TO_EVENT_TYPE:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Tipo de evento inválido.",
            )
        if request.capacity <= 0:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "A capacidade do evento deve ser maior que zero.",
            )

        try:
            start_dt = _parse_iso_datetime(request.start_time)
            end_dt = _parse_iso_datetime(request.end_time)
        except ValueError:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Datas devem estar no formato ISO-8601 válido.",
            )

        if end_dt <= start_dt:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "A data de término deve ser posterior à data de início.",
            )

        async with async_session_maker() as db:
            result = await db.execute(
                select(Event).where(Event.id == request.event_id)
            )
            event = result.scalar_one_or_none()
            if not event:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    "Evento não encontrado.",
                )

            if (
                not request.requester_is_admin
                and event.organizer_id != request.requester_id
            ):
                await context.abort(
                    grpc.StatusCode.PERMISSION_DENIED,
                    "Apenas o organizador criador ou um administrador pode editar este evento.",
                )

            if event.status == EventStatus.CANCELLED:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Não é possível editar um evento cancelado.",
                )

            if request.capacity < event.occupied_seats:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "A nova capacidade não pode ser menor que o total de vagas já ocupadas.",
                )

            event.title = request.title.strip()
            event.description = request.description.strip()
            event.event_type = PROTO_TO_EVENT_TYPE[request.event_type]
            event.location = request.location.strip()
            event.start_time = start_dt
            event.end_time = end_dt
            event.capacity = request.capacity
            event.updated_at = datetime.now(timezone.utc)

            await db.commit()
            await db.refresh(event)

        return _to_event_response(event)

    async def ReviewEvent(
        self,
        request: events_pb2.ReviewEventRequest,
        context: grpc.aio.ServicerContext,
    ) -> events_pb2.EventResponse:
        if not request.event_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "event_id é obrigatório.",
            )

        async with async_session_maker() as db:
            result = await db.execute(
                select(Event).where(Event.id == request.event_id)
            )
            event = result.scalar_one_or_none()
            if not event:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    "Evento não encontrado.",
                )

            if event.status == EventStatus.CANCELLED:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Não é possível aprovar ou rejeitar um evento cancelado.",
                )

            event.status = (
                EventStatus.APPROVED if request.approve else EventStatus.REJECTED
            )
            event.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(event)

        return _to_event_response(event)

    async def GetEvent(
        self,
        request: events_pb2.GetEventRequest,
        context: grpc.aio.ServicerContext,
    ) -> events_pb2.EventResponse:
        if not request.event_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "event_id é obrigatório.",
            )

        async with async_session_maker() as db:
            result = await db.execute(
                select(Event).where(Event.id == request.event_id)
            )
            event = result.scalar_one_or_none()

        if not event:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "Evento não encontrado.",
            )

        return _to_event_response(event)

    async def ListEvents(
        self,
        request: events_pb2.ListEventsRequest,
        context: grpc.aio.ServicerContext,
    ) -> events_pb2.ListEventsResponse:
        stmt = select(Event)

        if request.status_filter in PROTO_TO_EVENT_STATUS:
            stmt = stmt.where(
                Event.status == PROTO_TO_EVENT_STATUS[request.status_filter]
            )
        if request.event_type_filter in PROTO_TO_EVENT_TYPE:
            stmt = stmt.where(
                Event.event_type == PROTO_TO_EVENT_TYPE[request.event_type_filter]
            )
        if request.organizer_id.strip():
            stmt = stmt.where(Event.organizer_id == request.organizer_id.strip())

        stmt = stmt.order_by(Event.start_time.asc())

        async with async_session_maker() as db:
            result = await db.execute(stmt)
            events = list(result.scalars().all())

        return events_pb2.ListEventsResponse(
            events=[_to_event_response(ev) for ev in events]
        )

    async def ReserveSeat(
        self,
        request: events_pb2.SeatOperationRequest,
        context: grpc.aio.ServicerContext,
    ) -> events_pb2.EventResponse:
        if not request.event_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "event_id é obrigatório.",
            )

        async with async_session_maker() as db:
            # Atualização atômica condicional para evitar overbooking concorrente
            stmt = (
                update(Event)
                .where(
                    Event.id == request.event_id,
                    Event.status == EventStatus.APPROVED,
                    Event.occupied_seats < Event.capacity,
                )
                .values(
                    occupied_seats=Event.occupied_seats + 1,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            update_result = await db.execute(stmt)

            if update_result.rowcount == 0:
                await db.rollback()
                check = await db.execute(
                    select(Event).where(Event.id == request.event_id)
                )
                event = check.scalar_one_or_none()
                if not event:
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        "Evento não encontrado.",
                    )
                if event.status != EventStatus.APPROVED:
                    await context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        "Apenas eventos aprovados aceitam inscrições.",
                    )
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Não há vagas disponíveis para este evento.",
                )

            await db.commit()
            refreshed = await db.execute(
                select(Event).where(Event.id == request.event_id)
            )
            event = refreshed.scalar_one()

        return _to_event_response(event)

    async def ReleaseSeat(
        self,
        request: events_pb2.SeatOperationRequest,
        context: grpc.aio.ServicerContext,
    ) -> events_pb2.EventResponse:
        if not request.event_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "event_id é obrigatório.",
            )

        async with async_session_maker() as db:
            check = await db.execute(
                select(Event).where(Event.id == request.event_id)
            )
            event = check.scalar_one_or_none()
            if not event:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    "Evento não encontrado.",
                )

            if event.occupied_seats > 0:
                await db.execute(
                    update(Event)
                    .where(Event.id == request.event_id, Event.occupied_seats > 0)
                    .values(
                        occupied_seats=Event.occupied_seats - 1,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()

            refreshed = await db.execute(
                select(Event).where(Event.id == request.event_id)
            )
            event = refreshed.scalar_one()

        return _to_event_response(event)

    async def GetEventsSummary(
        self,
        request: events_pb2.GetEventsSummaryRequest,
        context: grpc.aio.ServicerContext,
    ) -> events_pb2.EventsSummaryResponse:
        async with async_session_maker() as db:
            result = await db.execute(select(Event))
            events = list(result.scalars().all())

        return events_pb2.EventsSummaryResponse(
            total_events=len(events),
            pending_events=sum(1 for e in events if e.status == EventStatus.PENDING),
            approved_events=sum(1 for e in events if e.status == EventStatus.APPROVED),
            rejected_events=sum(1 for e in events if e.status == EventStatus.REJECTED),
            total_capacity=sum(e.capacity for e in events),
            total_occupied_seats=sum(e.occupied_seats for e in events),
        )


def create_grpc_server(port: int) -> grpc.aio.Server:
    server = grpc.aio.server()
    events_pb2_grpc.add_EventsServiceServicer_to_server(
        EventsGrpcServicer(), server
    )
    server.add_insecure_port(f"[::]:{port}")
    return server
