import asyncio

import grpc
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sgea.src.orchestrator.clients import (
    get_events_stub,
    get_notification_stub,
    get_registrations_stub,
)
from sgea.src.orchestrator.config import settings
from sgea.src.orchestrator.errors import raise_http_from_grpc
from sgea.src.orchestrator.schemas import (
    AttendanceRequest,
    CertificateOut,
    EventCreateRequest,
    EventOut,
    EventReviewRequest,
    EventStatusEnum,
    EventTypeEnum,
    EventUpdateRequest,
    NotificationOut,
    OrchestratedRegistrationOut,
    RegistrationCreateRequest,
    RegistrationOut,
    SummaryReportOut,
)
from sgea.src.orchestrator.security import (
    AuthenticatedUser,
    get_current_user,
    require_roles,
)
from sgea.src.protos import events_pb2, notifications_pb2, registrations_pb2

EVENT_TYPE_TO_PROTO = {
    EventTypeEnum.ACADEMIC_WEEK: events_pb2.ACADEMIC_WEEK,
    EventTypeEnum.LECTURE: events_pb2.LECTURE,
    EventTypeEnum.MINICOURSE: events_pb2.MINICOURSE,
    EventTypeEnum.WORKSHOP: events_pb2.WORKSHOP,
}
PROTO_TO_EVENT_TYPE = {v: k for k, v in EVENT_TYPE_TO_PROTO.items()}

EVENT_STATUS_TO_PROTO = {
    EventStatusEnum.PENDING: events_pb2.PENDING,
    EventStatusEnum.APPROVED: events_pb2.APPROVED,
    EventStatusEnum.REJECTED: events_pb2.REJECTED,
    EventStatusEnum.CANCELLED: events_pb2.CANCELLED,
}
PROTO_TO_EVENT_STATUS = {v: k for k, v in EVENT_STATUS_TO_PROTO.items()}

PROTO_TO_REG_STATUS = {
    registrations_pb2.CONFIRMED: "CONFIRMED",
    registrations_pb2.CANCELLED: "CANCELLED",
}
PROTO_TO_CHANNEL = {
    notifications_pb2.EMAIL: "EMAIL",
    notifications_pb2.SYSTEM: "SYSTEM",
}
PROTO_TO_NOTIF_STATUS = {
    notifications_pb2.SENT: "SENT",
    notifications_pb2.FAILED: "FAILED",
}


def _map_event(ev: events_pb2.EventResponse) -> EventOut:
    return EventOut(
        event_id=ev.event_id,
        title=ev.title,
        description=ev.description,
        event_type=PROTO_TO_EVENT_TYPE[ev.event_type],
        location=ev.location,
        start_time=ev.start_time,
        end_time=ev.end_time,
        capacity=ev.capacity,
        occupied_seats=ev.occupied_seats,
        available_seats=ev.available_seats,
        organizer_id=ev.organizer_id,
        status=PROTO_TO_EVENT_STATUS[ev.status],
        created_at=ev.created_at,
    )


def _map_registration(
    reg: registrations_pb2.RegistrationResponse,
) -> RegistrationOut:
    return RegistrationOut(
        registration_id=reg.registration_id,
        event_id=reg.event_id,
        user_id=reg.user_id,
        user_name=reg.user_name,
        user_email=reg.user_email,
        event_title=reg.event_title,
        status=PROTO_TO_REG_STATUS.get(reg.status, "UNKNOWN"),
        attendance_recorded=reg.attendance_recorded,
        certificate_code=reg.certificate_code,
        created_at=reg.created_at,
    )


app = FastAPI(
    title=settings.service_name,
    version="1.0.0",
    description=(
        "API Gateway e Orquestrador do SGEA. Expõe contratos REST/OpenAPI "
        "e coordena os microsserviços internos de forma assíncrona via gRPC."
    ),
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Dados de entrada inválidos.", "errors": exc.errors()},
    )


@app.get("/health", tags=["Infraestrutura"])
async def health_check():
    return {"status": "ok", "service": "orchestrator"}


# ==============================================================================
# EVENTOS
# ==============================================================================


@app.post(
    "/api/v1/events",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Eventos"],
    summary="Criar evento acadêmico (Organizador ou Administrador)",
)
async def create_event(
    payload: EventCreateRequest,
    current_user: AuthenticatedUser = Depends(
        require_roles("ORGANIZER", "ADMIN")
    ),
):
    async with get_events_stub() as stub:
        try:
            resp = await stub.CreateEvent(
                events_pb2.CreateEventRequest(
                    title=payload.title,
                    description=payload.description,
                    event_type=EVENT_TYPE_TO_PROTO[payload.event_type],
                    location=payload.location,
                    start_time=payload.start_time,
                    end_time=payload.end_time,
                    capacity=payload.capacity,
                    organizer_id=current_user.user_id,
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)
    return _map_event(resp)


@app.get(
    "/api/v1/events",
    response_model=list[EventOut],
    tags=["Eventos"],
    summary="Consultar eventos (Participante vê apenas eventos aprovados)",
)
async def list_events(
    status_filter: EventStatusEnum | None = Query(default=None, alias="status"),
    event_type: EventTypeEnum | None = Query(default=None),
    only_mine: bool = Query(default=False),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    proto_status = events_pb2.EVENT_STATUS_UNSPECIFIED
    organizer_id = ""

    if current_user.role == "PARTICIPANT":
        # Participante só consulta eventos aprovados
        proto_status = events_pb2.APPROVED
    else:
        if status_filter is not None:
            proto_status = EVENT_STATUS_TO_PROTO[status_filter]
        if only_mine:
            organizer_id = current_user.user_id

    proto_type = (
        EVENT_TYPE_TO_PROTO[event_type]
        if event_type is not None
        else events_pb2.EVENT_TYPE_UNSPECIFIED
    )

    async with get_events_stub() as stub:
        try:
            resp = await stub.ListEvents(
                events_pb2.ListEventsRequest(
                    status_filter=proto_status,
                    event_type_filter=proto_type,
                    organizer_id=organizer_id,
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return [_map_event(ev) for ev in resp.events]


@app.get(
    "/api/v1/events/{event_id}",
    response_model=EventOut,
    tags=["Eventos"],
    summary="Detalhar um evento por ID",
)
async def get_event(
    event_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    async with get_events_stub() as stub:
        try:
            ev = await stub.GetEvent(events_pb2.GetEventRequest(event_id=event_id))
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    if (
        current_user.role == "PARTICIPANT"
        and ev.status != events_pb2.APPROVED
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Evento ainda não disponível para visualização pública.",
        )

    return _map_event(ev)


@app.put(
    "/api/v1/events/{event_id}",
    response_model=EventOut,
    tags=["Eventos"],
    summary="Editar evento (Organizador dono ou Administrador)",
)
async def update_event(
    event_id: str,
    payload: EventUpdateRequest,
    current_user: AuthenticatedUser = Depends(
        require_roles("ORGANIZER", "ADMIN")
    ),
):
    async with get_events_stub() as stub:
        try:
            resp = await stub.UpdateEvent(
                events_pb2.UpdateEventRequest(
                    event_id=event_id,
                    requester_id=current_user.user_id,
                    requester_is_admin=(current_user.role == "ADMIN"),
                    title=payload.title,
                    description=payload.description,
                    event_type=EVENT_TYPE_TO_PROTO[payload.event_type],
                    location=payload.location,
                    start_time=payload.start_time,
                    end_time=payload.end_time,
                    capacity=payload.capacity,
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return _map_event(resp)


@app.patch(
    "/api/v1/events/{event_id}/review",
    response_model=EventOut,
    tags=["Eventos"],
    summary="Aprovar ou rejeitar evento (apenas Administrador)",
)
async def review_event(
    event_id: str,
    payload: EventReviewRequest,
    _: AuthenticatedUser = Depends(require_roles("ADMIN")),
):
    async with get_events_stub() as stub:
        try:
            resp = await stub.ReviewEvent(
                events_pb2.ReviewEventRequest(
                    event_id=event_id, approve=payload.approve
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return _map_event(resp)


# ==============================================================================
# INSCRIÇÕES (FLUXO ORQUESTRADO), PRESENÇA E CERTIFICADOS
# ==============================================================================


@app.post(
    "/api/v1/registrations",
    response_model=OrchestratedRegistrationOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Inscrições (Orquestrado)"],
    summary="Inscrever-se em evento (Orquestrador -> Eventos -> Inscrições -> Notificações)",
)
async def register_in_event(
    payload: RegistrationCreateRequest,
    current_user: AuthenticatedUser = Depends(require_roles("PARTICIPANT")),
):
    # Passo 1: Reserva atômica de vaga no events_service
    async with get_events_stub() as events_stub:
        try:
            event_resp = await events_stub.ReserveSeat(
                events_pb2.SeatOperationRequest(event_id=payload.event_id)
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    # Passo 2: Persistência da inscrição no registrations_service (com compensação Saga)
    async with get_registrations_stub() as reg_stub:
        try:
            reg_resp = await reg_stub.CreateRegistration(
                registrations_pb2.CreateRegistrationRequest(
                    event_id=event_resp.event_id,
                    user_id=current_user.user_id,
                    user_name=current_user.name,
                    user_email=current_user.email,
                    event_title=event_resp.title,
                )
            )
        except grpc.aio.AioRpcError as exc:
            # Compensação: devolve a vaga reservada se a inscrição falhar (ex: duplicidade)
            async with get_events_stub() as comp_events_stub:
                try:
                    await comp_events_stub.ReleaseSeat(
                        events_pb2.SeatOperationRequest(event_id=event_resp.event_id)
                    )
                except grpc.aio.AioRpcError:
                    pass
            raise_http_from_grpc(exc)

    # Passo 3: Disparo de notificação de confirmação via notification_service
    notif_id = ""
    notif_status = "PENDING"
    async with get_notification_stub() as notif_stub:
        try:
            notif_resp = await notif_stub.SendNotification(
                notifications_pb2.SendNotificationRequest(
                    recipient_id=current_user.user_id,
                    recipient_email=current_user.email,
                    recipient_name=current_user.name,
                    subject=f"Inscrição Confirmada: {event_resp.title}",
                    message=(
                        f"Olá {current_user.name}, sua inscrição no evento "
                        f"'{event_resp.title}' ({event_resp.location}) foi confirmada."
                    ),
                    channel=notifications_pb2.EMAIL,
                    source_system="SGEA",
                )
            )
            notif_id = notif_resp.notification_id
            notif_status = PROTO_TO_NOTIF_STATUS.get(notif_resp.status, "SENT")
        except grpc.aio.AioRpcError:
            notif_status = "UNAVAILABLE"

    return OrchestratedRegistrationOut(
        registration=_map_registration(reg_resp),
        notification_id=notif_id,
        notification_status=notif_status,
    )


@app.get(
    "/api/v1/registrations/me",
    response_model=list[RegistrationOut],
    tags=["Inscrições (Orquestrado)"],
    summary="Listar inscrições do participante autenticado",
)
async def list_my_registrations(
    include_cancelled: bool = Query(default=False),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    async with get_registrations_stub() as reg_stub:
        try:
            resp = await reg_stub.ListUserRegistrations(
                registrations_pb2.ListUserRegistrationsRequest(
                    user_id=current_user.user_id,
                    include_cancelled=include_cancelled,
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return [_map_registration(r) for r in resp.registrations]


@app.delete(
    "/api/v1/registrations/{registration_id}",
    response_model=RegistrationOut,
    tags=["Inscrições (Orquestrado)"],
    summary="Cancelar inscrição (libera vaga e notifica participante)",
)
async def cancel_registration(
    registration_id: str,
    current_user: AuthenticatedUser = Depends(
        require_roles("PARTICIPANT", "ADMIN")
    ),
):
    async with get_registrations_stub() as reg_stub:
        try:
            cancelled = await reg_stub.CancelRegistration(
                registrations_pb2.CancelRegistrationRequest(
                    registration_id=registration_id,
                    requester_id=current_user.user_id,
                    requester_is_admin=(current_user.role == "ADMIN"),
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    # Libera a vaga no events_service
    async with get_events_stub() as events_stub:
        try:
            await events_stub.ReleaseSeat(
                events_pb2.SeatOperationRequest(event_id=cancelled.event_id)
            )
        except grpc.aio.AioRpcError:
            pass

    # Notifica cancelamento
    async with get_notification_stub() as notif_stub:
        try:
            await notif_stub.SendNotification(
                notifications_pb2.SendNotificationRequest(
                    recipient_id=cancelled.user_id,
                    recipient_email=cancelled.user_email,
                    recipient_name=cancelled.user_name,
                    subject=f"Inscrição Cancelada: {cancelled.event_title}",
                    message=(
                        f"Sua inscrição no evento '{cancelled.event_title}' "
                        "foi cancelada com sucesso."
                    ),
                    channel=notifications_pb2.EMAIL,
                    source_system="SGEA",
                )
            )
        except grpc.aio.AioRpcError:
            pass

    return _map_registration(cancelled)


@app.get(
    "/api/v1/events/{event_id}/registrations",
    response_model=list[RegistrationOut],
    tags=["Eventos"],
    summary="Listar inscritos de um evento (Organizador dono ou Administrador)",
)
async def list_event_registrations(
    event_id: str,
    include_cancelled: bool = Query(default=False),
    current_user: AuthenticatedUser = Depends(
        require_roles("ORGANIZER", "ADMIN")
    ),
):
    async with get_events_stub() as events_stub:
        try:
            ev = await events_stub.GetEvent(
                events_pb2.GetEventRequest(event_id=event_id)
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    if (
        current_user.role != "ADMIN"
        and ev.organizer_id != current_user.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o organizador responsável pelo evento ou um administrador pode ver os inscritos.",
        )

    async with get_registrations_stub() as reg_stub:
        try:
            resp = await reg_stub.ListEventRegistrations(
                registrations_pb2.ListEventRegistrationsRequest(
                    event_id=event_id,
                    include_cancelled=include_cancelled,
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return [_map_registration(r) for r in resp.registrations]


@app.patch(
    "/api/v1/registrations/{registration_id}/attendance",
    response_model=RegistrationOut,
    tags=["Presença e Certificados"],
    summary="Registrar presença de participante (Organizador dono do evento ou Administrador)",
)
async def record_attendance(
    registration_id: str,
    payload: AttendanceRequest,
    current_user: AuthenticatedUser = Depends(
        require_roles("ORGANIZER", "ADMIN")
    ),
):
    async with get_registrations_stub() as reg_stub:
        try:
            reg = await reg_stub.GetRegistration(
                registrations_pb2.GetRegistrationRequest(
                    registration_id=registration_id
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

        if current_user.role != "ADMIN":
            async with get_events_stub() as events_stub:
                try:
                    ev = await events_stub.GetEvent(
                        events_pb2.GetEventRequest(event_id=reg.event_id)
                    )
                except grpc.aio.AioRpcError as exc:
                    raise_http_from_grpc(exc)

                if ev.organizer_id != current_user.user_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Apenas o organizador responsável pelo evento pode registrar presença.",
                    )

        try:
            updated = await reg_stub.RecordAttendance(
                registrations_pb2.RecordAttendanceRequest(
                    registration_id=registration_id,
                    attended=payload.attended,
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return _map_registration(updated)


@app.get(
    "/api/v1/registrations/{registration_id}/certificate",
    response_model=CertificateOut,
    tags=["Presença e Certificados"],
    summary="Emitir certificado de participação (Participante dono ou Administrador)",
)
async def issue_certificate(
    registration_id: str,
    current_user: AuthenticatedUser = Depends(
        require_roles("PARTICIPANT", "ADMIN")
    ),
):
    async with get_registrations_stub() as reg_stub:
        try:
            cert = await reg_stub.IssueCertificate(
                registrations_pb2.IssueCertificateRequest(
                    registration_id=registration_id,
                    requester_id=current_user.user_id,
                    requester_is_admin=(current_user.role == "ADMIN"),
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return CertificateOut(
        registration_id=cert.registration_id,
        event_id=cert.event_id,
        event_title=cert.event_title,
        user_id=cert.user_id,
        user_name=cert.user_name,
        certificate_code=cert.certificate_code,
        issued_at=cert.issued_at,
    )


# ==============================================================================
# NOTIFICAÇÕES E RELATÓRIOS GERAIS
# ==============================================================================


@app.get(
    "/api/v1/notifications/me",
    response_model=list[NotificationOut],
    tags=["Notificações"],
    summary="Consultar minhas notificações recebidas",
)
async def list_my_notifications(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    async with get_notification_stub() as notif_stub:
        try:
            resp = await notif_stub.ListNotificationsByRecipient(
                notifications_pb2.ListNotificationsByRecipientRequest(
                    recipient_id=current_user.user_id
                )
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return [
        NotificationOut(
            notification_id=n.notification_id,
            recipient_id=n.recipient_id,
            recipient_email=n.recipient_email,
            subject=n.subject,
            message=n.message,
            channel=PROTO_TO_CHANNEL.get(n.channel, "EMAIL"),
            status=PROTO_TO_NOTIF_STATUS.get(n.status, "SENT"),
            source_system=n.source_system,
            created_at=n.created_at,
        )
        for n in resp.notifications
    ]


@app.get(
    "/api/v1/reports/summary",
    response_model=SummaryReportOut,
    tags=["Relatórios (Admin)"],
    summary="Relatório geral consolidado de eventos e inscrições (apenas Administrador)",
)
async def get_general_report(
    _: AuthenticatedUser = Depends(require_roles("ADMIN")),
):
    async with (
        get_events_stub() as events_stub,
        get_registrations_stub() as reg_stub,
    ):
        try:
            events_summary, regs_summary = await asyncio.gather(
                events_stub.GetEventsSummary(events_pb2.GetEventsSummaryRequest()),
                reg_stub.GetRegistrationsSummary(
                    registrations_pb2.GetRegistrationsSummaryRequest()
                ),
            )
        except grpc.aio.AioRpcError as exc:
            raise_http_from_grpc(exc)

    return SummaryReportOut(
        total_events=events_summary.total_events,
        pending_events=events_summary.pending_events,
        approved_events=events_summary.approved_events,
        rejected_events=events_summary.rejected_events,
        total_capacity=events_summary.total_capacity,
        total_occupied_seats=events_summary.total_occupied_seats,
        total_registrations=regs_summary.total_registrations,
        confirmed_registrations=regs_summary.confirmed_registrations,
        cancelled_registrations=regs_summary.cancelled_registrations,
        total_attendances=regs_summary.total_attendances,
        certificates_issued=regs_summary.certificates_issued,
    )
